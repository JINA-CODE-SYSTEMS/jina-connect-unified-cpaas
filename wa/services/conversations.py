"""Resolve-or-create WaConversation for inbound messages (#189).

Single entry point for every WA inbound-handler that needs a
conversation FK. Reuses an existing open conversation if the contact
sent something within the last 24h on the same WA app; otherwise
spawns a fresh row (closing the previous one if it expired).

This is the only place we encode the per-conversation 24h
service-window semantics; flow runtime, CTWA ingestion, and the
inbox-grouping query all read state off the returned conversation.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from wa.models import WaConversation

# The 24h service window is always measured in UTC because Meta's
# WhatsApp policy clock is UTC. ``timezone.now()`` returns a UTC-aware
# datetime under Django's default ``USE_TZ=True``; if a tenant ever
# disables ``USE_TZ`` they break this contract. (#201 review)
SERVICE_WINDOW = timedelta(hours=24)


def resolve_or_create(*, wa_app, contact, now=None) -> WaConversation:
    """Return the conversation that *inbound* messages on (wa_app,
    contact) should attach to. Always extends the service window to
    ``now + 24h``.

    Outbound senders should NOT touch the service window — call
    :func:`resolve_for_outbound` instead if you need the FK for an
    outbound row.
    """
    now = now or timezone.now()

    latest = (
        WaConversation.objects.filter(wa_app=wa_app, contact=contact, closed_at__isnull=True)
        .order_by("-last_inbound_at")
        .first()
    )

    if latest and latest.service_window_expires_at > now:
        # Reuse — extend the window.
        latest.last_inbound_at = now
        latest.service_window_expires_at = now + SERVICE_WINDOW
        latest.save(update_fields=["last_inbound_at", "service_window_expires_at", "updated_at"])
        return latest

    # Close the expired conversation if one exists.
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


def resolve_for_outbound(*, wa_app, contact) -> WaConversation | None:
    """Return the latest open conversation for outbound attachment, or
    ``None``. Never extends the service window."""
    return (
        WaConversation.objects.filter(wa_app=wa_app, contact=contact, closed_at__isnull=True)
        .order_by("-last_inbound_at")
        .first()
    )


__all__ = ["SERVICE_WINDOW", "resolve_or_create", "resolve_for_outbound"]
