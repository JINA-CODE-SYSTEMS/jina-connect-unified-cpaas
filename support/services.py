"""What happens to a ticket, from either side.

GitHub → ticket: ``handle_issues_event`` and ``handle_comment_event``, called by
the webhook. Ticket → GitHub: ``customer_comment``, ``customer_reopen`` and
``customer_confirm``, called when a platform admin acts on a ticket, plus
``auto_close_due`` from cron.

Every handler is safe to run twice. GitHub redelivers webhooks, and each change
made *from* this side comes back as a webhook of its own — closing an issue
here produces an ``issues.closed`` delivery for a ticket that is already
closed — so transitions check the current status rather than assume it.
"""

import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from support import bugdrop, conf, github
from support.models import Actor, CloseReason, EventKind, SupportTicket, SupportTicketEvent, TicketStatus

logger = logging.getLogger(__name__)

WORKING = (TicketStatus.OPEN, TicketStatus.IN_PROGRESS)


class TicketStateError(Exception):
    """The action does not apply to the ticket in its current state."""


def _ts(value: str | None) -> datetime:
    return parse_datetime(value) if value else timezone.now()


def _event(ticket, kind, actor, *, agent_name="", author=None, body="", comment_id=None, at=None):
    return SupportTicketEvent.objects.create(
        ticket=ticket,
        kind=kind,
        actor=actor,
        agent_name=agent_name,
        author=author,
        body=body,
        github_comment_id=comment_id,
        created_at=at or timezone.now(),
    )


def _working_status(ticket) -> str:
    return TicketStatus.IN_PROGRESS if ticket.assignee_login else TicketStatus.OPEN


def _has_label(issue: dict, name: str) -> bool:
    return any((label.get("name") or "").lower() == name.lower() for label in issue.get("labels") or [])


# ── GitHub → ticket ─────────────────────────────────────────────────────────


def upsert_ticket(issue: dict) -> tuple[SupportTicket | None, bool]:
    """The ticket for this issue, created if it is a BugDrop report filed from this deployment.

    Returns ``(None, False)`` for anything that is not ours: not a BugDrop
    issue, or filed from another deployment's host.
    """
    if not bugdrop.is_bugdrop_issue(issue):
        return None, False
    report = bugdrop.parse(issue.get("body"))
    if report.page_host not in conf.report_hosts():
        return None, False

    fields = {
        "title": (issue.get("title") or "")[:500],
        "description": report.description,
        "page_url": report.page_url[:2000],
        "reporter_name": report.reporter_name[:200],
        "reporter_email": report.reporter_email[:254],
    }
    ticket = SupportTicket.objects.filter(github_repo=conf.repo(), github_number=issue["number"]).first()
    if ticket:
        changed = [name for name, value in fields.items() if getattr(ticket, name) != value]
        if changed:
            for name in changed:
                setattr(ticket, name, fields[name])
            ticket.save(update_fields=[*changed, "updated_at"])
        return ticket, False

    assignee = (issue.get("assignee") or {}).get("login") or ""
    ticket = SupportTicket(
        github_repo=conf.repo(),
        github_number=issue["number"],
        assignee_login=assignee,
        reported_at=_ts(issue.get("created_at")),
        **fields,
    )
    ticket.status = _working_status(ticket)
    # First sight of an issue is normally its "opened" delivery. When it is not
    # — the backfill, or an issue filed before the webhook existed — start from
    # where the issue already stands rather than from "open".
    if issue.get("state") == "closed":
        ticket.status = TicketStatus.CLOSED
        ticket.closed_at = _ts(issue.get("closed_at"))
    elif _has_label(issue, conf.resolved_label()):
        ticket.status = TicketStatus.RESOLVED
        # The countdown starts from first sight, so nobody is auto-closed for
        # time that passed before they could see the ticket at all.
        ticket.resolved_at = timezone.now()

    with transaction.atomic():
        ticket.save()
        _event(ticket, EventKind.RECEIVED, Actor.SYSTEM, at=ticket.reported_at)
        if assignee:
            _event(ticket, EventKind.ASSIGNED, Actor.SUPPORT, agent_name=conf.agent_name(assignee))
        if ticket.status == TicketStatus.RESOLVED:
            _event(ticket, EventKind.RESOLVED, Actor.SUPPORT, agent_name=conf.agent_name(assignee))
        elif ticket.status == TicketStatus.CLOSED:
            _event(ticket, EventKind.CLOSED, Actor.SYSTEM, at=ticket.closed_at)
    return ticket, True


def _reopen_from_support(ticket, sender_login: str):
    ticket.status = _working_status(ticket)
    ticket.resolved_at = None
    ticket.closed_at = None
    ticket.close_reason = ""
    ticket.save(update_fields=["status", "resolved_at", "closed_at", "close_reason", "updated_at"])
    _event(ticket, EventKind.REOPENED, Actor.SUPPORT, agent_name=conf.agent_name(sender_login))


def handle_issues_event(payload: dict) -> SupportTicket | None:
    action = payload.get("action")
    issue = payload.get("issue") or {}
    sender = (payload.get("sender") or {}).get("login") or ""

    ticket, created = upsert_ticket(issue)
    if ticket is None or created:
        # A new ticket was built from the issue as it stands, which already
        # accounts for whatever this delivery was about.
        return ticket

    with transaction.atomic():
        ticket = SupportTicket.objects.select_for_update().get(pk=ticket.pk)

        if action == "assigned":
            login = (payload.get("assignee") or {}).get("login") or ""
            if login and login != ticket.assignee_login:
                ticket.assignee_login = login
                if ticket.status == TicketStatus.OPEN:
                    ticket.status = TicketStatus.IN_PROGRESS
                ticket.save(update_fields=["assignee_login", "status", "updated_at"])
                _event(ticket, EventKind.ASSIGNED, Actor.SUPPORT, agent_name=conf.agent_name(login))

        elif action == "unassigned":
            # Quietly: the customer does not need to hear that nobody is on it,
            # and whoever picks it up next will be announced.
            remaining = (issue.get("assignee") or {}).get("login") or ""
            if ticket.assignee_login != remaining:
                ticket.assignee_login = remaining
                ticket.save(update_fields=["assignee_login", "updated_at"])

        elif action == "labeled" and _label_is_resolved(payload):
            if ticket.status in WORKING:
                ticket.status = TicketStatus.RESOLVED
                ticket.resolved_at = timezone.now()
                ticket.save(update_fields=["status", "resolved_at", "updated_at"])
                _event(ticket, EventKind.RESOLVED, Actor.SUPPORT, agent_name=conf.agent_name(sender))

        elif action == "unlabeled" and _label_is_resolved(payload):
            # Also arrives when a customer reopens, because reopening removes
            # the label — by then the ticket is already working again.
            if ticket.status == TicketStatus.RESOLVED:
                _reopen_from_support(ticket, sender)

        elif action == "closed":
            if issue.get("state_reason") == "not_planned":
                if ticket.status != TicketStatus.CLOSED:
                    _close(ticket, CloseReason.SUPPORT, Actor.SUPPORT, agent_name=conf.agent_name(sender))
            elif ticket.status in WORKING:
                # Closed as completed while still being worked on: in practice a
                # merged "Closes #N". Not live yet, so this is news, not a
                # request to verify — the label does that once it is deployed.
                last = ticket.events.order_by("-created_at", "-id").first()
                if not last or last.kind != EventKind.FIX_READY:
                    _event(ticket, EventKind.FIX_READY, Actor.SUPPORT, agent_name=conf.agent_name(sender))

        elif action == "reopened":
            if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
                _reopen_from_support(ticket, sender)

    return ticket


def _label_is_resolved(payload: dict) -> bool:
    return ((payload.get("label") or {}).get("name") or "").lower() == conf.resolved_label().lower()


def _reply_text(body: str | None) -> str | None:
    """The customer-facing text of a ``/reply`` comment, or None if it is internal."""
    text = (body or "").lstrip()
    prefix = conf.reply_prefix()
    if not text.lower().startswith(prefix.lower()):
        return None
    rest = text[len(prefix) :]
    # "/replying to …" is not the command.
    if rest and not rest[0].isspace():
        return None
    return rest.strip() or None


def handle_comment_event(payload: dict) -> SupportTicket | None:
    action = payload.get("action")
    comment = payload.get("comment") or {}
    ticket, _ = upsert_ticket(payload.get("issue") or {})
    if ticket is None or not comment.get("id"):
        return ticket
    _apply_comment(ticket, comment, deleted=action == "deleted")
    return ticket


def _apply_comment(ticket, comment: dict, *, deleted: bool = False):
    existing = SupportTicketEvent.objects.filter(github_comment_id=comment["id"]).first()
    text = None if deleted else _reply_text(comment.get("body"))

    if text is None:
        # Deleted, or edited so it is no longer a reply: take it back off the
        # customer's screen.
        if existing:
            existing.delete()
        return
    if existing:
        if existing.body != text:
            existing.body = text
            existing.save(update_fields=["body"])
        return

    with transaction.atomic():
        _event(
            ticket,
            EventKind.REPLY,
            Actor.SUPPORT,
            agent_name=conf.agent_name((comment.get("user") or {}).get("login")),
            body=text,
            comment_id=comment["id"],
            at=_ts(comment.get("created_at")),
        )
        if ticket.status == TicketStatus.OPEN:
            ticket.status = TicketStatus.IN_PROGRESS
            ticket.save(update_fields=["status", "updated_at"])


# ── ticket → GitHub ─────────────────────────────────────────────────────────


def _who(user) -> str:
    name = (user.get_full_name() or "").strip()
    return f"{name} ({user.email})" if name and user.email else (name or user.email or f"user {user.pk}")


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.strip().splitlines())


def _host_note() -> str:
    hosts = sorted(conf.report_hosts())
    return f" on {hosts[0]}" if hosts else ""


def customer_comment(ticket: SupportTicket, user, text: str) -> SupportTicketEvent:
    """More detail from the customer on a ticket that is still being worked on."""
    if ticket.status == TicketStatus.CLOSED:
        raise TicketStateError("This ticket is closed. Reopen it to add more details.")
    github.comment(ticket.github_number, f"**{_who(user)}** added{_host_note()}:\n\n{_quote(text)}")
    return _event(ticket, EventKind.CUSTOMER_COMMENT, Actor.CUSTOMER, author=user, body=text.strip())


def customer_reopen(ticket: SupportTicket, user, text: str) -> SupportTicket:
    """ "Not fixed" — back to support, with what the customer is still seeing."""
    if ticket.status in WORKING:
        raise TicketStateError("This ticket is already open.")
    # GitHub first: if it cannot be told, the ticket must not look reopened to
    # a customer while nobody on the support side can see it.
    github.remove_label(ticket.github_number, conf.resolved_label())
    github.set_state(ticket.github_number, "open")
    github.comment(
        ticket.github_number,
        f"**Reopened by {_who(user)}**{_host_note()} — not fixed for them:\n\n{_quote(text)}",
    )
    with transaction.atomic():
        ticket.status = _working_status(ticket)
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.close_reason = ""
        ticket.save(update_fields=["status", "resolved_at", "closed_at", "close_reason", "updated_at"])
        _event(ticket, EventKind.REOPENED, Actor.CUSTOMER, author=user, body=text.strip())
    return ticket


def customer_confirm(ticket: SupportTicket, user) -> SupportTicket:
    """ "Yes, it is fixed" — close now rather than waiting out the countdown."""
    if ticket.status != TicketStatus.RESOLVED:
        raise TicketStateError("Only a resolved ticket can be confirmed.")
    github.comment(ticket.github_number, f"Confirmed fixed by **{_who(user)}**{_host_note()}.")
    github.set_state(ticket.github_number, "closed", "completed")
    with transaction.atomic():
        _close(ticket, CloseReason.CONFIRMED, Actor.CUSTOMER, author=user)
    return ticket


def _close(ticket, reason, actor, *, agent_name="", author=None):
    ticket.status = TicketStatus.CLOSED
    ticket.close_reason = reason
    ticket.closed_at = timezone.now()
    ticket.save(update_fields=["status", "close_reason", "closed_at", "updated_at"])
    _event(ticket, EventKind.CLOSED, actor, agent_name=agent_name, author=author)


def auto_close_due(now: datetime | None = None) -> int:
    """Close resolved tickets nobody has answered within ``SUPPORT_AUTO_CLOSE_HOURS``.

    The customer was promised this, so the ticket closes even if GitHub cannot
    be reached; the issue is then left open with its resolved label, where a
    developer will see it.
    """
    cutoff = (now or timezone.now()) - timedelta(hours=conf.auto_close_hours())
    closed = 0
    for ticket in SupportTicket.objects.filter(status=TicketStatus.RESOLVED, resolved_at__lte=cutoff):
        with transaction.atomic():
            locked = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
            if locked.status != TicketStatus.RESOLVED:
                continue
            _close(locked, CloseReason.AUTO, Actor.SYSTEM)
        closed += 1
        try:
            github.comment(
                ticket.github_number,
                f"Closed automatically: no reply{_host_note()} within {conf.auto_close_hours()} hours of being "
                "marked resolved.",
            )
            github.set_state(ticket.github_number, "closed", "completed")
        except github.GitHubError:
            logger.exception("support: auto-closed ticket %s locally but could not close the issue", ticket.pk)
    return closed


def sync_from_github() -> dict[str, int]:
    """Bring in every BugDrop issue for this deployment, and the ``/reply`` comments on them.

    For the first deploy, and for catching up after the webhook was down. Safe
    to rerun: tickets and replies are matched to what is already stored.
    """
    counts = {"seen": 0, "created": 0, "replies": 0}
    for issue in github.list_issues(bugdrop.BUGDROP_LABEL):
        ticket, created = upsert_ticket(issue)
        if ticket is None:
            continue
        counts["seen"] += 1
        counts["created"] += int(created)
        before = ticket.events.filter(kind=EventKind.REPLY).count()
        for comment in github.list_comments(issue["number"]):
            _apply_comment(ticket, comment)
        counts["replies"] += ticket.events.filter(kind=EventKind.REPLY).count() - before
    return counts
