"""Contact resolution helpers shared across all channel inbound handlers (#108)."""

from __future__ import annotations

import logging

from contacts.models import TenantContact

logger = logging.getLogger(__name__)


def resolve_or_create_contact(
    *,
    tenant,
    source: str,
    phone: str = "",
    telegram_chat_id: int | None = None,
    defaults: dict | None = None,
) -> TenantContact:
    """Resolve an existing contact or create a minimal fallback (#108).

    Ensures an inbound message is **never** dropped due to a contact
    resolution failure (duplicate race, missing phone format, etc.).

    Args:
        tenant: Tenant instance.
        source: ContactSource value (e.g. ``"TELEGRAM"``, ``"SMS"``).
        phone: Phone number for phone-based channels.
        telegram_chat_id: Telegram chat ID for Telegram channel.
        defaults: Extra defaults passed to ``get_or_create``.

    Returns:
        TenantContact instance (existing or newly created).
    """
    from contacts.models import ContactSource

    defaults = defaults or {}
    defaults.setdefault("source", source)

    try:
        if telegram_chat_id is not None:
            contact, _ = TenantContact.objects.get_or_create(
                tenant=tenant,
                telegram_chat_id=telegram_chat_id,
                defaults=defaults,
            )
        elif phone:
            contact, _ = TenantContact.objects.get_or_create(
                tenant=tenant,
                phone=phone,
                defaults=defaults,
            )
        else:
            raise ValueError("Either phone or telegram_chat_id must be provided")
        return contact
    except Exception:
        logger.warning(
            "[resolve_or_create_contact] Primary lookup failed for tenant=%s source=%s phone=%s tg_chat=%s — creating fallback",
            tenant.pk,
            source,
            phone or "",
            telegram_chat_id or "",
            exc_info=True,
        )
        # Fallback: create a minimal contact so the message is not lost
        try:
            kwargs = {"tenant": tenant, "source": source}
            if phone:
                kwargs["phone"] = phone
            if telegram_chat_id is not None:
                kwargs["telegram_chat_id"] = telegram_chat_id
            return TenantContact.objects.create(**kwargs)
        except Exception:
            # Last resort — try to find any existing contact with this identifier
            logger.exception("[resolve_or_create_contact] Fallback creation also failed")
            if telegram_chat_id is not None:
                existing = TenantContact.objects.filter(tenant=tenant, telegram_chat_id=telegram_chat_id).first()
            elif phone:
                existing = TenantContact.objects.filter(tenant=tenant, phone=phone).first()
            else:
                existing = None
            if existing:
                return existing
            raise
