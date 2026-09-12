"""Resolve-or-create WaConversation for inbound messages (#189).

Single entry point for every WA inbound-handler that needs a
conversation FK. Reuses an existing open conversation if the contact
sent something within the last 24h on the same WA app; otherwise
spawns a fresh row (closing the previous one if it expired).

This is the only place we encode the per-conversation 24h
service-window semantics; flow runtime, CTWA ingestion, and the
inbox-grouping query all read state off the returned conversation.

Multi-worker safety (#201 third review Blocker #2):
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two simultaneous first-inbound webhooks for the same
``(wa_app, contact)`` would otherwise both see "no open conversation"
and both create a row. The model now has a partial UniqueConstraint
on ``(wa_app, contact)`` where ``closed_at IS NULL`` — the second
``INSERT`` raises ``IntegrityError``. ``resolve_or_create`` catches it
inside a retry loop: re-read, return the row that the racing worker
just committed. A ``transaction.atomic`` boundary scopes the catch so
no half-state leaks if anything else in the function later raises.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from wa.models import WaConversation

logger = logging.getLogger(__name__)

# The 24h service window is always measured in UTC because Meta's
# WhatsApp policy clock is UTC. ``timezone.now()`` returns a UTC-aware
# datetime under Django's default ``USE_TZ=True``; if a tenant ever
# disables ``USE_TZ`` they break this contract. (#201 review)
SERVICE_WINDOW = timedelta(hours=24)

SERVICE_WINDOW_CLOSED_CODE = "SERVICE_WINDOW_CLOSED"
"""``WAMessage.error_code`` for a send this module refused before dispatch.

Not Meta's own 131047: that code means Meta rejected the request, and a
message that never left is a different fact about the same condition.
"""


def resolve_or_create(*, wa_app, contact, now=None) -> WaConversation:
    """Return the conversation that *inbound* messages on (wa_app,
    contact) should attach to. Always extends the service window to
    ``now + 24h``.

    Race-safe: protected by the partial unique index on
    ``(wa_app, contact) WHERE closed_at IS NULL``. A simultaneous
    first-inbound from another worker is caught via IntegrityError
    and resolved by re-reading the winning row.

    Outbound senders should NOT touch the service window — call
    :func:`resolve_for_outbound` instead if you need the FK for an
    outbound row.
    """
    now = now or timezone.now()

    # First pass: read existing, extend if valid.
    latest = _latest_open(wa_app=wa_app, contact=contact)
    if latest and latest.service_window_expires_at > now:
        latest.last_inbound_at = now
        latest.service_window_expires_at = now + SERVICE_WINDOW
        latest.save(update_fields=["last_inbound_at", "service_window_expires_at", "updated_at"])
        return latest

    # Either no open conversation or the open one's window has expired.
    # Wrap close-then-create in a single transaction; on the IntegrityError
    # race, re-read and return the winning row.
    try:
        with transaction.atomic():
            if latest:
                latest.closed_at = now
                latest.save(update_fields=["closed_at", "updated_at"])
            return WaConversation.objects.create(
                wa_app=wa_app,
                contact=contact,
                first_message_at=now,
                last_inbound_at=now,
                service_window_expires_at=now + SERVICE_WINDOW,
            )
    except IntegrityError:
        # Race: another worker committed an open conversation for this
        # (wa_app, contact) between our SELECT and our INSERT. The
        # partial unique index rejected our INSERT. Return the winning
        # row — and extend its window (we DID receive an inbound).
        logger.info(
            "[wa.conversations] race on first-message create for wa_app=%s contact=%s — reusing winner",
            wa_app.id,
            contact.id,
        )
        winner = _latest_open(wa_app=wa_app, contact=contact)
        if winner is None:
            # Extremely unlikely — the winning row was closed between
            # the IntegrityError and our re-read. Surface; the caller
            # can retry.
            raise
        winner.last_inbound_at = now
        winner.service_window_expires_at = now + SERVICE_WINDOW
        winner.save(update_fields=["last_inbound_at", "service_window_expires_at", "updated_at"])
        return winner


def _latest_open(*, wa_app, contact) -> WaConversation | None:
    return (
        WaConversation.objects.filter(wa_app=wa_app, contact=contact, closed_at__isnull=True)
        .order_by("-last_inbound_at")
        .first()
    )


def resolve_for_outbound(*, wa_app, contact) -> WaConversation | None:
    """Return the latest open conversation for outbound attachment, or
    ``None``. Never extends the service window."""
    return _latest_open(wa_app=wa_app, contact=contact)


def outbound_window_error(*, wa_app, contact, now=None) -> str | None:
    """Why a *free-form* message to this contact cannot be sent, or ``None``.

    The sender had no pre-flight at all (#274): an agent's reply typed after
    the window shut was POSTed to Graph, rejected with error 131047 and
    shown as FAILED, while the inbox UI had already greyed the composer out
    from its own independent countdown.

    Only a window we can *prove* is shut blocks the send. A contact with no
    conversation row — everyone who predates #189, and anyone whose only
    inbound arrived before it shipped — is unknown, not closed, and Meta
    remains the authority for those. Callers must exempt templates
    themselves; the window says nothing about them.
    """
    now = now or timezone.now()
    conversation = resolve_for_outbound(wa_app=wa_app, contact=contact)
    if conversation is None or conversation.service_window_expires_at > now:
        return None

    return (
        "The 24-hour customer service window closed at "
        f"{conversation.service_window_expires_at.isoformat()}. WhatsApp only "
        "accepts free-form messages within 24h of the contact's last inbound "
        "message (error 131047) — send an approved template instead."
    )


__all__ = [
    "SERVICE_WINDOW",
    "SERVICE_WINDOW_CLOSED_CODE",
    "resolve_or_create",
    "resolve_for_outbound",
    "outbound_window_error",
]
