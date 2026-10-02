"""Support tickets: BugDrop reports, mirrored from GitHub for the people who filed them.

BugDrop files a GitHub issue and stops there. The person who reported the
problem never hears back unless someone emails them, and the issue lives in a
private repository they cannot see. These tables are the customer-facing copy:
what was reported, where it stands, and what support has said about it.

GitHub stays the source of truth for the work. Developers do nothing here —
they assign, comment and label on GitHub as usual, and the webhook in
``support.views`` turns the parts meant for the customer into timeline events.
Only three things reach a customer, and each is an explicit act:

  * being assigned (shown as "<name> from our support team is looking into it")
  * a comment starting with ``/reply`` (everything else on the issue is internal)
  * the ``support:resolved`` label, added once the fix is *live* — not when the
    PR merges, which closes the issue before anything is deployed

Developers appear under a support name, never their GitHub login. See
``support.conf.agent_name``.
"""

from django.conf import settings
from django.db import models


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    # The fix is live and the customer has been asked to check it. Closes on
    # its own after ``SUPPORT_AUTO_CLOSE_HOURS`` unless they reopen it.
    RESOLVED = "resolved", "Resolved — awaiting confirmation"
    CLOSED = "closed", "Closed"


class CloseReason(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmed fixed by the customer"
    AUTO = "auto", "Closed automatically after no reply"
    # Closed on GitHub as "not planned" (duplicate, cannot reproduce, …).
    SUPPORT = "support", "Closed by support"


class SupportTicket(models.Model):
    github_repo = models.CharField(max_length=200, help_text="owner/repo the issue lives in.")
    github_number = models.PositiveIntegerField()

    title = models.CharField(max_length=500)
    # Only what the reporter wrote. The rest of the issue body — screenshot
    # links into a private repo, browser and viewport table — is for developers.
    description = models.TextField(blank=True)
    page_url = models.URLField(max_length=2000, blank=True)
    reporter_name = models.CharField(max_length=200, blank=True)
    reporter_email = models.EmailField(blank=True)

    status = models.CharField(max_length=20, choices=TicketStatus.choices, default=TicketStatus.OPEN, db_index=True)
    close_reason = models.CharField(max_length=20, choices=CloseReason.choices, blank=True)
    # Internal. Read to pick the support name and never serialized: a customer
    # is shown "Ananya", not a GitHub login.
    assignee_login = models.CharField(max_length=100, blank=True)

    reported_at = models.DateTimeField(help_text="When the issue was opened on GitHub.")
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-reported_at"]
        constraints = [
            models.UniqueConstraint(fields=["github_repo", "github_number"], name="support_ticket_unique_issue"),
        ]

    def __str__(self):
        return f"#{self.github_number} {self.title}"


class EventKind(models.TextChoices):
    RECEIVED = "received", "Report received"
    ASSIGNED = "assigned", "Assigned to support"
    REPLY = "reply", "Reply from support"
    # The issue was closed as completed on GitHub — normally a merged PR. The
    # fix exists but is not live yet, so the customer is told it is on its way
    # and is NOT asked to verify anything.
    FIX_READY = "fix_ready", "Fix on its way"
    RESOLVED = "resolved", "Marked resolved"
    REOPENED = "reopened", "Reopened"
    CUSTOMER_COMMENT = "customer_comment", "Comment from the customer"
    CLOSED = "closed", "Closed"


class Actor(models.TextChoices):
    SUPPORT = "support", "Support"
    CUSTOMER = "customer", "Customer"
    SYSTEM = "system", "System"


class SupportTicketEvent(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20, choices=EventKind.choices)
    actor = models.CharField(max_length=10, choices=Actor.choices)
    # The support name shown for a support-side event, fixed when the event is
    # recorded so a later change to the name map does not rewrite history.
    agent_name = models.CharField(max_length=100, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    body = models.TextField(blank=True)
    # Set for ``/reply`` comments, so an edit or delete on GitHub finds its
    # event and a redelivered webhook does not post the reply twice.
    github_comment_id = models.BigIntegerField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.ticket_id}:{self.kind}"
