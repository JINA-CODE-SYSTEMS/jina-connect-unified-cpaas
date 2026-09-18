"""Contact resolution helpers shared across all channel inbound handlers (#108)."""

from __future__ import annotations

import logging

from contacts.models import TenantContact

logger = logging.getLogger(__name__)


def _reactivate_if_archived(contact) -> bool:
    """Bring an archived contact back when they message in.

    Archiving means "I am not working this contact". An inbound message is the
    clearest possible signal that they are back, so leaving them archived
    produces the worst of the three available states: the conversation appears
    in the team inbox while the person is absent from Contacts, so an agent can
    read and answer them but cannot find, tag or assign them.

    The alternative — a second contact row — is worse still. The phone number
    is how this table is keyed, so the history would split in two and, more
    seriously, ``marketing_opt_out`` lives on the row: a fresh row is a contact
    who never opted out. Archiving must not become a way to lose a STOP.

    Returns:
        True if the contact was reactivated, False if it was already active.
    """
    if contact is None or contact.is_active:
        return False

    contact.is_active = True
    contact.save(update_fields=["is_active", "updated_at"])
    logger.info(
        "[resolve_or_create_contact] Reactivated archived contact %s — inbound message received",
        contact.pk,
    )
    return True


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

        _reactivate_if_archived(contact)
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
