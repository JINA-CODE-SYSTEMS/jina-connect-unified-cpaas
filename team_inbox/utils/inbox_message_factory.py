"""
Shared inbox message factory — creates Messages entries from any channel.

Extracted from broadcast/tasks.py _create_team_inbox_message_from_broadcast()
so that both broadcast outbound and Telegram/SMS inbound can share the same
creation path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from contacts.models import TenantContact
    from tenants.models import Tenant
    from users.models import User

logger = logging.getLogger(__name__)


# ── Broadcast correlation (#658) ────────────────────────────────────────────
#
# A client that POSTs a broadcast gets a 201 and draws a pending bubble for
# each recipient. Until the inbox row arrives it has nothing to match that
# bubble against, so the web app matched on the rendered *body text* — which
# makes two identical templates to one contact indistinguishable, and hands
# the delivery state to whichever pending bubble is oldest. With a broadcast
# that sends the same template to the same person twice, that is wrong every
# time.
#
# The fix is to say, on the row itself, exactly which send produced it.
# ``content["_meta"]`` is where this table already keeps provider and
# correlation metadata (``wa.tasks._build_team_inbox_content`` stamps
# ``wa_message_id``/``webhook_event_id`` for inbound, and
# ``team_inbox.utils.read_receipts`` reads them back).
#
# Which identifiers, and why these three:
#
# * ``broadcast_message_id`` — ``BroadcastMessage.pk``. This is the answer:
#   ``BroadcastMessage`` is the per-recipient row, so its pk names one send to
#   one person and nothing else. Every later status update already travels
#   under this id (``message_status_update`` carries ``broadcast_message_id``),
#   so the bubble and its updates end up keyed the same way.
# * ``broadcast_id`` and ``contact_id`` — the pair the *client* already holds
#   at 201 time, before any ``BroadcastMessage`` row exists to be named.
#   Neither alone identifies a bubble: a broadcast id does not separate two
#   recipients, and a contact id does not separate two broadcasts. Together
#   they are unique by construction — ``BroadcastMessage`` carries a
#   ``unique_broadcast_contact`` constraint on exactly this pair — so
#   ``(broadcast_id, contact_id)`` is the same identity as
#   ``broadcast_message_id``, spelled in the terms the client can use first.
#
# Deliberately *not* promoted to a column, unlike ``provider_message_id``
# (#330, team_inbox/0012). That promotion bought a partial unique index,
# because inbound ingestion needed an idempotency key the database could
# enforce against Meta's webhook redeliveries. Nothing here needs enforcing:
# the row is created once, in the send path, behind the ``_already_sent``
# guard, and a duplicate would be a cosmetic double bubble rather than a
# double charge or a re-fired chat flow. Nor is it a query path — the server
# looks a row up by these ids exactly once, on the failure branch below, for
# one row at a time. It is correlation metadata for a client, so it lives
# where the other correlation metadata lives.
#
# Status is deliberately absent: whether the send failed is read from
# ``outgoing_status``/``outgoing_failed_at``/``outgoing_error``, which resolve
# through ``BroadcastMessage`` and cannot go stale the way a copy in the JSON
# would.

#: Value of ``_meta["source"]`` on rows the broadcast sender created.
BROADCAST_META_SOURCE = "broadcast"

#: ``_meta`` key holding ``BroadcastMessage.pk``.
BROADCAST_MESSAGE_META_KEY = "broadcast_message_id"


def broadcast_correlation_meta(broadcast_message) -> dict:
    """The ``content["_meta"]`` block for a row created by a broadcast send.

    Args:
        broadcast_message: ``broadcast.models.BroadcastMessage`` instance.

    Returns:
        dict — see the module comment above for what each key is for.
    """
    return {
        "source": BROADCAST_META_SOURCE,
        "broadcast_id": broadcast_message.broadcast_id,
        BROADCAST_MESSAGE_META_KEY: broadcast_message.pk,
        "contact_id": broadcast_message.contact_id,
    }


def find_inbox_message_for_broadcast(broadcast_message):
    """The inbox row this ``BroadcastMessage`` produced, or ``None``.

    Two lookups because either can be the only one available: the provider
    message id is what a *sent* row carries in ``external_message_id``, and a
    send that never reached the provider has none — that row is findable only
    by the ``_meta`` stamp.

    Scoped to the tenant: a ``BroadcastMessage`` pk is global, but a row
    belonging to another tenant must never be named in an event.
    """
    from team_inbox.models import Messages

    tenant_id = getattr(broadcast_message.broadcast, "tenant_id", None)
    if not tenant_id:
        return None

    if broadcast_message.message_id:
        row = Messages.objects.filter(tenant_id=tenant_id, external_message_id=broadcast_message.message_id).first()
        if row:
            return row

    return Messages.objects.filter(
        tenant_id=tenant_id,
        **{f"content___meta__{BROADCAST_MESSAGE_META_KEY}": broadcast_message.pk},
    ).first()


def create_inbox_message(
    *,
    tenant: "Tenant",
    contact: "TenantContact",
    platform: str,
    direction: str,
    author: str,
    content: dict,
    external_message_id: Optional[str] = None,
    tenant_user: Optional["User"] = None,
    is_read: bool = False,
):
    """
    Create a single Messages row in team_inbox.

    Args:
        tenant: Tenant instance.
        contact: TenantContact instance.
        platform: One of MessagePlatformChoices values (e.g. "WHATSAPP", "TELEGRAM").
        direction: "INCOMING" or "OUTGOING".
        author: "USER", "CONTACT", or "BOT".
        content: Dict matching the team_inbox content schema
                 (e.g. {"type": "text", "body": {"text": "Hello"}}).
        external_message_id: Optional provider message ID for status tracking.
        tenant_user: Optional user who sent the message (for outgoing).
        is_read: Whether the message should be marked as read.

    Returns:
        The created Messages instance.
    """
    from team_inbox.models import MessageEventIds, Messages

    event_id = MessageEventIds.objects.create()

    message = Messages.objects.create(
        tenant=tenant,
        message_id=event_id,
        content=content,
        direction=direction,
        platform=platform,
        author=author,
        contact=contact,
        tenant_user=tenant_user,
        is_read=is_read,
        external_message_id=external_message_id or "",
    )

    logger.info(
        "[create_inbox_message] Created Messages %s (%s/%s) for contact %s",
        message.pk,
        platform,
        direction,
        contact.pk,
    )
    return message
