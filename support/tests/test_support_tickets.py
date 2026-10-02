"""Support tickets: BugDrop reports mirrored from GitHub for platform admins.

What these defend, roughly in order of what it would cost to get wrong:

1. **Nothing internal reaches a customer.** Only ``/reply`` comments are shown;
   a developer's GitHub login never appears in a response, only a support name.
2. **"Resolved" means live.** A merged PR closes the issue before deploy; that
   must not ask the customer to verify. Only the label does.
3. **A deployment sees only its own reports.** Several deployments can share
   one repository and all of them receive every webhook.
4. **Webhooks are signed and idempotent.** GitHub redelivers, and every change
   made from this side echoes back as a delivery.
5. **A customer action that cannot reach GitHub changes nothing.**

HOW TO RUN:
    .venv/bin/python -m pytest support/tests/ -v
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from support import bugdrop, conf, services
from support.github import GitHubError
from support.models import EventKind, SupportTicket, TicketStatus
from tenants.models import Tenant, TenantRole, TenantUser

pytestmark = pytest.mark.django_db

User = get_user_model()
_seq = itertools.count(1)

REPO = "acme/web"
SECRET = "whsec-test"
WEBHOOK = "/support/github/webhook/"
TICKETS = "/support/tickets/"

# Shaped like jain-t/jina-connect-web#691, with the submitter section BugDrop
# adds when the prefill provider supplies a name and email.
BODY = """## Submitted by
**Piyush Sharma** (piyush@partner.test)

## Description
Hi Team, I am testing the system. When I tried to add a number it gave me the error.

## Screenshot
![Screenshot](https://github.com/acme/web/blob/bugdrop-screenshots/.bugdrop/screenshots/1.png?raw=true)

<details>
<summary>System Info</summary>

| Property | Value |
|----------|-------|
| **Browser** | Chrome 155.0.0.0 |
| **Page** | https://app.partner.test/host/orgManagement |

</details>

---
*Submitted via [BugDrop](https://github.com/mean-weasel/bugdrop)*
"""

# Shaped like #692: no description, no submitter — only a screenshot and the table.
BARE_BODY = """## Screenshot
![Screenshot](https://example.invalid/s.png)

<details>
<summary>System Info</summary>

| Property | Value |
|----------|-------|
| **Page** | https://app.partner.test/host/orgManagement |

</details>
"""


@pytest.fixture(autouse=True)
def _configured(settings):
    settings.SUPPORT_GITHUB_REPO = REPO
    settings.SUPPORT_GITHUB_TOKEN = "ghp-test"
    settings.SUPPORT_GITHUB_WEBHOOK_SECRET = SECRET
    settings.SUPPORT_REPORT_HOSTS = "app.partner.test"
    settings.SUPPORT_AGENT_NAMES = "dev-one:Ananya"
    settings.SUPPORT_RESOLVED_LABEL = "support:resolved"
    settings.SUPPORT_REPLY_PREFIX = "/reply"
    settings.SUPPORT_AUTO_CLOSE_HOURS = 24


@pytest.fixture
def gh():
    """Every outbound GitHub call, recorded and answered successfully."""
    with (
        mock.patch("support.github.comment", return_value={}) as comment,
        mock.patch("support.github.set_state", return_value={}) as set_state,
        mock.patch("support.github.remove_label", return_value=None) as remove_label,
    ):
        yield mock.Mock(comment=comment, set_state=set_state, remove_label=remove_label)


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _issue(number=691, body=BODY, labels=("bug", "bugdrop"), assignee=None, state="open", state_reason=None):
    return {
        "number": number,
        "title": "Unable to onboard an organization",
        "body": body,
        "labels": [{"name": n} for n in labels],
        "assignee": {"login": assignee} if assignee else None,
        "state": state,
        "state_reason": state_reason,
        "created_at": "2026-09-30T14:36:51Z",
        "closed_at": None,
    }


def _deliver(event, payload, *, secret=SECRET, client=None):
    payload = {"repository": {"full_name": REPO}, "sender": {"login": "dev-one"}, **payload}
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return (client or APIClient()).post(
        WEBHOOK,
        data=body,
        content_type="application/json",
        HTTP_X_GITHUB_EVENT=event,
        HTTP_X_HUB_SIGNATURE_256=signature,
    )


def _opened(**kw):
    return _deliver("issues", {"action": "opened", "issue": _issue(**kw)})


def _comment(body, *, comment_id=1, login="dev-one", action="created", issue=None):
    return _deliver(
        "issue_comment",
        {
            "action": action,
            "issue": issue or _issue(),
            "comment": {"id": comment_id, "body": body, "user": {"login": login}, "created_at": "2026-10-01T10:00:00Z"},
        },
    )


def _ticket(number=691) -> SupportTicket:
    return SupportTicket.objects.get(github_repo=REPO, github_number=number)


def _kinds(ticket) -> list[str]:
    return list(ticket.events.values_list("kind", flat=True))


def _user(*, superuser=False, staff=False, first_name=""):
    n = next(_seq)
    user = User.objects.create_user(
        username=f"s{n}",
        email=f"s{n}@test.invalid",
        password="x",  # noqa: S106
        mobile=f"+9191000{n:05d}",
        first_name=first_name,
    )
    user.is_superuser = superuser
    user.is_staff = staff
    user.save(update_fields=["is_superuser", "is_staff"])
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Reading a BugDrop issue
# ─────────────────────────────────────────────────────────────────────────────


class TestBugDropBody:
    def test_reads_reporter_description_and_page(self):
        report = bugdrop.parse(BODY)
        assert report.reporter_name == "Piyush Sharma"
        assert report.reporter_email == "piyush@partner.test"
        assert report.description.startswith("Hi Team, I am testing the system.")
        assert report.page_url == "https://app.partner.test/host/orgManagement"
        assert report.page_host == "app.partner.test"

    def test_description_stops_before_the_screenshot_and_system_info(self):
        # Screenshot links point into a private repository; the table is for developers.
        description = bugdrop.parse(BODY).description
        assert "Screenshot" not in description
        assert "System Info" not in description

    def test_a_report_with_only_a_screenshot_still_has_a_page(self):
        report = bugdrop.parse(BARE_BODY)
        assert report.description == ""
        assert report.reporter_name == ""
        assert report.page_host == "app.partner.test"

    def test_a_name_containing_an_at_sign_is_unwrapped(self):
        # BugDrop code-spans such a name so GitHub does not make it a mention.
        report = bugdrop.parse("## Submitted by\n**`ops@partner`** (ops@partner.test)\n")
        assert report.reporter_name == "ops@partner"
        assert report.reporter_email == "ops@partner.test"

    def test_escaped_markdown_is_unescaped(self):
        report = bugdrop.parse("## Submitted by\n**R\\_K &amp; Co**\n")
        assert report.reporter_name == "R_K & Co"

    def test_an_empty_body_is_not_an_error(self):
        assert bugdrop.parse(None) == bugdrop.BugDropReport()


# ─────────────────────────────────────────────────────────────────────────────
# Webhook: trust and routing
# ─────────────────────────────────────────────────────────────────────────────


class TestWebhookTrust:
    def test_a_wrong_signature_is_refused_and_creates_nothing(self):
        response = _deliver("issues", {"action": "opened", "issue": _issue()}, secret="not-the-secret")
        assert response.status_code == 401
        assert not SupportTicket.objects.exists()

    def test_a_missing_signature_is_refused(self):
        response = APIClient().post(WEBHOOK, data=b"{}", content_type="application/json", HTTP_X_GITHUB_EVENT="issues")
        assert response.status_code == 401

    def test_ping_is_answered(self):
        assert _deliver("ping", {"zen": "Keep it logically awesome."}).status_code == 200

    def test_unconfigured_deployment_does_not_expose_the_endpoint(self, settings):
        settings.SUPPORT_GITHUB_WEBHOOK_SECRET = ""
        assert _opened().status_code == 404

    def test_deliveries_for_another_repository_are_ignored(self):
        body = json.dumps({"action": "opened", "issue": _issue(), "repository": {"full_name": "other/repo"}}).encode()
        signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        APIClient().post(
            WEBHOOK,
            data=body,
            content_type="application/json",
            HTTP_X_GITHUB_EVENT="issues",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        assert not SupportTicket.objects.exists()


class TestRouting:
    def test_a_report_from_this_deployment_becomes_a_ticket(self):
        assert _opened().status_code == 200
        ticket = _ticket()
        assert ticket.status == TicketStatus.OPEN
        assert ticket.reporter_name == "Piyush Sharma"
        assert _kinds(ticket) == [EventKind.RECEIVED]

    def test_a_report_from_another_deployment_is_not_ours(self):
        _opened(body=BODY.replace("app.partner.test", "app.someone-else.test"))
        assert not SupportTicket.objects.exists()

    def test_an_issue_bugdrop_did_not_file_is_not_a_ticket(self):
        # Developers' own issues live in the same repository.
        _opened(labels=("bug",))
        assert not SupportTicket.objects.exists()

    def test_hosts_default_to_the_frontend(self, settings):
        settings.SUPPORT_REPORT_HOSTS = ""
        settings.FRONTEND_URL = "https://app.partner.test"
        assert conf.report_hosts() == {"app.partner.test"}

    def test_a_redelivered_opened_event_does_not_duplicate(self):
        _opened()
        _opened()
        assert SupportTicket.objects.count() == 1
        assert _kinds(_ticket()) == [EventKind.RECEIVED]


# ─────────────────────────────────────────────────────────────────────────────
# The lifecycle, from GitHub
# ─────────────────────────────────────────────────────────────────────────────


class TestFromGitHub:
    def test_assignment_is_announced_under_a_support_name(self):
        _opened()
        _deliver(
            "issues", {"action": "assigned", "issue": _issue(assignee="dev-one"), "assignee": {"login": "dev-one"}}
        )

        ticket = _ticket()
        assert ticket.status == TicketStatus.IN_PROGRESS
        assigned = ticket.events.get(kind=EventKind.ASSIGNED)
        assert assigned.agent_name == "Ananya"

    def test_reassigning_to_the_same_person_says_nothing_new(self):
        _opened()
        for _ in range(2):
            _deliver(
                "issues", {"action": "assigned", "issue": _issue(assignee="dev-one"), "assignee": {"login": "dev-one"}}
            )
        assert _kinds(_ticket()).count(EventKind.ASSIGNED) == 1

    def test_only_a_reply_comment_reaches_the_customer(self):
        _opened()
        _comment("Root cause: IsAdminUser reads is_staff; see #396.", comment_id=1)
        _comment("/reply Fixed and live — please try again.", comment_id=2)

        events = list(_ticket().events.filter(kind=EventKind.REPLY))
        assert [e.body for e in events] == ["Fixed and live — please try again."]
        assert events[0].agent_name == "Ananya"

    def test_a_word_that_merely_starts_with_reply_is_internal(self):
        _opened()
        _comment("/replying to the above: internal note", comment_id=3)
        assert not _ticket().events.filter(kind=EventKind.REPLY).exists()

    def test_a_redelivered_reply_is_shown_once(self):
        _opened()
        _comment("/reply On it.", comment_id=4)
        _comment("/reply On it.", comment_id=4)
        assert _ticket().events.filter(kind=EventKind.REPLY).count() == 1

    def test_editing_a_reply_updates_it_and_un_replying_withdraws_it(self):
        _opened()
        _comment("/reply First draft", comment_id=5)
        _comment("/reply Corrected", comment_id=5, action="edited")
        assert _ticket().events.get(kind=EventKind.REPLY).body == "Corrected"

        _comment("Actually keep this internal", comment_id=5, action="edited")
        assert not _ticket().events.filter(kind=EventKind.REPLY).exists()

    def test_deleting_a_reply_withdraws_it(self):
        _opened()
        _comment("/reply Oops", comment_id=6)
        _comment("/reply Oops", comment_id=6, action="deleted")
        assert not _ticket().events.filter(kind=EventKind.REPLY).exists()

    def test_a_merged_fix_is_news_not_a_request_to_verify(self):
        # "Closes #N" closes the issue on merge, before anything is deployed.
        _opened(assignee="dev-one")
        _deliver(
            "issues",
            {"action": "closed", "issue": _issue(assignee="dev-one", state="closed", state_reason="completed")},
        )

        ticket = _ticket()
        assert ticket.status == TicketStatus.IN_PROGRESS
        assert EventKind.FIX_READY in _kinds(ticket)
        assert ticket.resolved_at is None

    def test_the_resolved_label_asks_the_customer_to_verify(self):
        _opened(assignee="dev-one")
        _deliver("issues", {"action": "labeled", "issue": _issue(), "label": {"name": "support:resolved"}})

        ticket = _ticket()
        assert ticket.status == TicketStatus.RESOLVED
        assert ticket.resolved_at is not None
        assert ticket.events.get(kind=EventKind.RESOLVED).agent_name == "Ananya"

    def test_removing_the_label_takes_resolved_back(self):
        _opened(assignee="dev-one")
        _deliver("issues", {"action": "labeled", "issue": _issue(), "label": {"name": "support:resolved"}})
        _deliver("issues", {"action": "unlabeled", "issue": _issue(), "label": {"name": "support:resolved"}})

        ticket = _ticket()
        assert ticket.status == TicketStatus.IN_PROGRESS
        assert ticket.resolved_at is None

    def test_closed_as_not_planned_closes_the_ticket(self):
        _opened()
        _deliver("issues", {"action": "closed", "issue": _issue(state="closed", state_reason="not_planned")})
        ticket = _ticket()
        assert ticket.status == TicketStatus.CLOSED
        assert ticket.close_reason == "support"


# ─────────────────────────────────────────────────────────────────────────────
# The API a platform admin uses
# ─────────────────────────────────────────────────────────────────────────────


def _resolved_ticket():
    _opened(assignee="dev-one")
    _deliver("issues", {"action": "labeled", "issue": _issue(), "label": {"name": "support:resolved"}})
    return _ticket()


class TestAccess:
    @pytest.mark.parametrize("flags", [{"superuser": True}, {"staff": True}])
    def test_platform_admins_can_list(self, flags):
        _opened()
        response = _client(_user(**flags)).get(TICKETS)
        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_an_organisation_owner_cannot(self):
        tenant = Tenant.objects.create(name="Org")
        owner = _user()
        role, _ = TenantRole.objects.get_or_create(
            tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100}
        )
        TenantUser.objects.create(user=owner, tenant=tenant, role=role, is_active=True)
        assert _client(owner).get(TICKETS).status_code == 403

    def test_anonymous_cannot(self):
        assert APIClient().get(TICKETS).status_code == 401


class TestWhatTheCustomerSees:
    def test_no_github_login_or_comment_id_anywhere_in_the_ticket(self):
        _opened(assignee="dev-one")
        _comment("/reply Looking now.", comment_id=77)
        ticket = _ticket()

        raw = json.dumps(_client(_user(superuser=True)).get(f"{TICKETS}{ticket.pk}/").data, default=str)
        assert "dev-one" not in raw
        assert "77" not in raw.replace(str(ticket.pk), "")
        assert "Ananya" in raw

    def test_a_developer_without_a_configured_name_still_gets_a_stable_one(self):
        name = conf.agent_name("someone-new")
        assert name in conf.DEFAULT_AGENT_NAMES
        assert conf.agent_name("Someone-New") == name

    def test_a_resolved_ticket_says_when_it_will_close(self):
        ticket = _resolved_ticket()
        data = _client(_user(superuser=True)).get(f"{TICKETS}{ticket.pk}/").data
        assert data["auto_close_at"] == ticket.resolved_at + timedelta(hours=24)

    def test_active_filter_leaves_out_closed_tickets(self):
        _opened(number=1)
        _opened(number=2)
        _deliver("issues", {"action": "closed", "issue": _issue(number=2, state="closed", state_reason="not_planned")})
        data = _client(_user(superuser=True)).get(TICKETS, {"status": "active"}).data
        assert [t["number"] for t in data["results"]] == [1]

    def test_summary_counts(self):
        _resolved_ticket()
        data = _client(_user(superuser=True)).get(f"{TICKETS}summary/").data
        assert data == {"configured": True, "active": 1, "awaiting_confirmation": 1, "auto_close_hours": 24}


class TestCustomerActions:
    def test_reopen_tells_github_and_puts_the_ticket_back_to_work(self, gh):
        ticket = _resolved_ticket()
        admin = _user(superuser=True, first_name="Piyush")

        response = _client(admin).post(f"{TICKETS}{ticket.pk}/reopen/", {"text": "Still failing on Save."})

        assert response.status_code == 200
        assert response.data["status"] == TicketStatus.IN_PROGRESS
        gh.remove_label.assert_called_once_with(691, "support:resolved")
        gh.set_state.assert_called_once_with(691, "open")
        posted = gh.comment.call_args.args[1]
        assert "Still failing on Save." in posted
        assert "Piyush" in posted

    def test_reopen_needs_details(self, gh):
        ticket = _resolved_ticket()
        response = _client(_user(superuser=True)).post(f"{TICKETS}{ticket.pk}/reopen/", {"text": "  "})
        assert response.status_code == 400
        gh.comment.assert_not_called()

    def test_the_reopen_echo_from_github_changes_nothing_further(self, gh):
        ticket = _resolved_ticket()
        _client(_user(superuser=True)).post(f"{TICKETS}{ticket.pk}/reopen/", {"text": "Still broken."})
        before = _kinds(_ticket())

        # Removing the label and reopening both come back as webhooks.
        _deliver("issues", {"action": "unlabeled", "issue": _issue(), "label": {"name": "support:resolved"}})
        _deliver("issues", {"action": "reopened", "issue": _issue()})

        assert _kinds(_ticket()) == before

    def test_confirm_closes_here_and_on_github(self, gh):
        ticket = _resolved_ticket()
        response = _client(_user(superuser=True)).post(f"{TICKETS}{ticket.pk}/confirm/")

        assert response.status_code == 200
        assert response.data["status"] == TicketStatus.CLOSED
        assert response.data["close_reason"] == "confirmed"
        gh.set_state.assert_called_once_with(691, "closed", "completed")

    def test_confirm_is_only_for_a_resolved_ticket(self, gh):
        _opened()
        response = _client(_user(superuser=True)).post(f"{TICKETS}{_ticket().pk}/confirm/")
        assert response.status_code == 409
        gh.set_state.assert_not_called()

    def test_a_comment_goes_to_github_and_onto_the_timeline(self, gh):
        _opened()
        response = _client(_user(superuser=True)).post(
            f"{TICKETS}{_ticket().pk}/comment/", {"text": "Happens on Safari too."}
        )
        assert response.status_code == 200
        assert response.data["events"][-1]["kind"] == EventKind.CUSTOMER_COMMENT
        assert "Happens on Safari too." in gh.comment.call_args.args[1]

    def test_if_github_cannot_be_reached_nothing_changes(self, gh):
        ticket = _resolved_ticket()
        gh.remove_label.side_effect = GitHubError("boom")

        response = _client(_user(superuser=True)).post(f"{TICKETS}{ticket.pk}/reopen/", {"text": "Still broken."})

        assert response.status_code == 502
        assert "try again" in response.data["detail"]
        assert "boom" not in response.data["detail"]
        assert _ticket().status == TicketStatus.RESOLVED


# ─────────────────────────────────────────────────────────────────────────────
# Auto-close
# ─────────────────────────────────────────────────────────────────────────────


class TestAutoClose:
    def test_closes_after_the_window_and_tells_github(self, gh):
        ticket = _resolved_ticket()
        closed = services.auto_close_due(now=ticket.resolved_at + timedelta(hours=24, minutes=1))

        assert closed == 1
        ticket = _ticket()
        assert ticket.status == TicketStatus.CLOSED
        assert ticket.close_reason == "auto"
        gh.set_state.assert_called_once_with(691, "closed", "completed")

    def test_leaves_a_ticket_inside_the_window_alone(self, gh):
        ticket = _resolved_ticket()
        assert services.auto_close_due(now=ticket.resolved_at + timedelta(hours=23)) == 0
        assert _ticket().status == TicketStatus.RESOLVED

    def test_keeps_its_promise_even_when_github_is_down(self, gh):
        ticket = _resolved_ticket()
        gh.comment.side_effect = GitHubError("down")
        services.auto_close_due(now=ticket.resolved_at + timedelta(hours=25))
        assert _ticket().status == TicketStatus.CLOSED

    def test_the_close_echo_from_github_changes_nothing(self, gh):
        ticket = _resolved_ticket()
        services.auto_close_due(now=timezone.now() + timedelta(hours=25))
        before = _kinds(_ticket())
        _deliver("issues", {"action": "closed", "issue": _issue(state="closed", state_reason="completed")})
        assert _kinds(_ticket()) == before
        assert ticket.pk == _ticket().pk


# ─────────────────────────────────────────────────────────────────────────────
# Backfill
# ─────────────────────────────────────────────────────────────────────────────


class TestSync:
    def test_imports_this_deployments_issues_and_their_replies(self):
        issues = [
            _issue(number=691, state="closed", state_reason="completed"),
            _issue(number=692, body=BARE_BODY, assignee="dev-one"),
            _issue(number=700, body=BODY.replace("app.partner.test", "elsewhere.test")),
        ]
        comments = {691: [], 692: [{"id": 9, "body": "/reply Looking into it.", "user": {"login": "dev-one"}}], 700: []}
        with (
            mock.patch("support.github.list_issues", return_value=issues),
            mock.patch("support.github.list_comments", side_effect=lambda n: comments[n]),
        ):
            counts = services.sync_from_github()
            again = services.sync_from_github()

        assert counts == {"seen": 2, "created": 2, "replies": 1}
        assert again == {"seen": 2, "created": 0, "replies": 0}
        assert _ticket(691).status == TicketStatus.CLOSED
        assert _ticket(692).status == TicketStatus.IN_PROGRESS
