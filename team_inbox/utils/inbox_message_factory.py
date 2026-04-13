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
