"""Contact resolution helpers shared across all channel inbound handlers (#108)."""

from __future__ import annotations

import logging

from django.db.models import BooleanField, Case, Exists, OuterRef, Value, When

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


def _pick_existing(matches):
    """The one contact an inbound message belongs to, when there may be several.

    Nothing stops two rows sharing a phone number, and once there are two,
    ``get_or_create`` raises ``MultipleObjectsReturned`` on every message. The
    fallback below used to answer that by creating a *third* row — so each
    inbound message added another duplicate, and none of them was the contact
    a running chat flow was waiting on. A button tap landed on a fresh row with
    no session and the flow never heard it (jina-connect-web#696).

    So when there are several, pick the one the conversation is already
    attached to: an open chat flow session first, then a chat flow assignment,
    then an active contact over an archived one, then the oldest.
    """
    from chat_flow.models import UserChatFlowSession
    from contacts.models import AssigneeTypeChoices

    open_session = UserChatFlowSession.objects.filter(contact=OuterRef("pk"), is_active=True, is_complete=False)
    return (
        matches.annotate(
            _in_flow=Exists(open_session),
            _flow_assigned=Case(
                When(assigned_to_type=AssigneeTypeChoices.CHATFLOW, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
        )
        .order_by("-_in_flow", "-_flow_assigned", "-is_active", "created_at", "pk")
        .first()
    )


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
        defaults: Extra fields for a contact that has to be created.

    Returns:
        TenantContact instance (existing or newly created).
    """
    defaults = defaults or {}
    defaults.setdefault("source", source)

    if telegram_chat_id is not None:
        lookup = {"telegram_chat_id": telegram_chat_id}
    elif phone:
        lookup = {"phone": phone}
    else:
        lookup = {}

    try:
        if not lookup:
            raise ValueError("Either phone or telegram_chat_id must be provided")

        contact = _pick_existing(TenantContact.objects.filter(tenant=tenant, **lookup))
        if contact is None:
            contact = TenantContact.objects.create(tenant=tenant, **lookup, **defaults)

        _reactivate_if_archived(contact)
        return contact
    except Exception:
        logger.warning(
            "[resolve_or_create_contact] Primary lookup failed for tenant=%s source=%s phone=%s tg_chat=%s — falling back",
            tenant.pk,
            source,
            phone or "",
            telegram_chat_id or "",
            exc_info=True,
        )
        # Fallback: keep the message. Reuse a row that already has this
        # identifier before making one — creating here is how one duplicate
        # used to become seven.
        if lookup:
            existing = TenantContact.objects.filter(tenant=tenant, **lookup).order_by("created_at", "pk").first()
            if existing:
                return existing
        return TenantContact.objects.create(tenant=tenant, source=source, **lookup)
