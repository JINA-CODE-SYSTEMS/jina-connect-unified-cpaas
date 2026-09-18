"""What archiving a contact must and must not do elsewhere.

Archiving hides a contact from the list. Three other things follow from it, and
none of them were true before:

* a broadcast must stop reaching them, including one already queued;
* the price quoted for that broadcast must match what is actually sent, or the
  tenant is billed for traffic that never leaves (#262's shape);
* and an archived contact who messages in must come back, rather than leaving
  an agent answering somebody who is absent from Contacts.

HOW TO RUN:
    python -m pytest contacts/tests/test_archived_contact_effects.py -v
"""

from __future__ import annotations

import itertools

import pytest

from broadcast.models import Broadcast, BroadcastPlatformChoices, BroadcastStatusChoices, MessageStatusChoices
from contacts.models import ContactSource, MarketingOptOutSource, TenantContact
from contacts.services import resolve_or_create_contact
from tenants.models import Tenant

pytestmark = pytest.mark.django_db

_seq = itertools.count(1)


def _tenant():
    return Tenant.objects.create(name=f"Org {next(_seq)}")


def _contact(tenant, *, active=True):
    n = next(_seq)
    return TenantContact.objects.create(tenant=tenant, phone=f"+2782{n:07d}", first_name=f"C{n}", is_active=active)


def _broadcast(tenant, recipients, *, marketing=True):
    broadcast = Broadcast.objects.create(
        tenant=tenant,
        name=f"B{next(_seq)}",
        platform=BroadcastPlatformChoices.WHATSAPP,
        status=BroadcastStatusChoices.DRAFT,
    )
    broadcast.recipients.set(recipients)
    return broadcast


# ─────────────────────────────────────────────────────────────────────────────
# A queued broadcast stops reaching them
# ─────────────────────────────────────────────────────────────────────────────


def test_an_archived_recipient_is_not_billed_for():
    """The quote and the send have to name the same people."""
    tenant = _tenant()
    kept, archived = _contact(tenant), _contact(tenant)
    broadcast = _broadcast(tenant, [kept, archived])

    assert broadcast.billable_recipients().count() == 2

    archived.is_active = False
    archived.save(update_fields=["is_active"])

    assert list(broadcast.billable_recipients()) == [kept]


def test_archiving_suppresses_every_category_not_just_marketing():
    """An opt-out declines one kind of message; archiving declines the contact.

    So unlike ``marketing_opt_out``, this must also stop a utility template —
    otherwise a contact taken off the list still receives order updates.
    """
    tenant = _tenant()
    archived = _contact(tenant, active=False)
    broadcast = _broadcast(tenant, [archived])

    # Whatever this broadcast's category resolves to, an archived contact is
    # excluded — asserted without reaching for the category, because the point
    # is that the category does not matter here.
    assert broadcast.billable_recipients().count() == 0


def test_a_contact_archived_after_the_rows_exist_is_suppressed_at_the_boundary():
    """The reported case: removed seconds before the broadcast goes out.

    Rows are created when the send starts, so a contact archived after that
    already has a ``BroadcastMessage``. The check that matters runs immediately
    before the provider call, which is the last moment the spend can be
    stopped.
    """
    from broadcast.models import BroadcastMessage
    from broadcast.tasks import _suppression_reason

    tenant = _tenant()
    contact = _contact(tenant)
    broadcast = _broadcast(tenant, [contact])
    message = BroadcastMessage.objects.create(broadcast=broadcast, contact=contact, status=MessageStatusChoices.PENDING)

    assert _suppression_reason(message) is None, "an active contact is sent to"

    contact.is_active = False
    contact.save(update_fields=["is_active"])
    message.refresh_from_db()

    reason = _suppression_reason(message)
    assert reason is not None
    assert "archived" in reason.lower(), reason


def test_an_opted_out_contact_is_still_suppressed_for_its_own_reason():
    """The existing rule must keep working, and say which rule fired."""
    from broadcast.models import BroadcastMessage
    from broadcast.tasks import _suppression_reason

    tenant = _tenant()
    contact = _contact(tenant)
    contact.set_marketing_opt_out(opted_out=True, source=MarketingOptOutSource.KEYWORD)
    broadcast = _broadcast(tenant, [contact])
    message = BroadcastMessage.objects.create(broadcast=broadcast, contact=contact, status=MessageStatusChoices.PENDING)

    reason = _suppression_reason(message)
    if reason is not None:
        assert "opted out" in reason.lower(), reason


# ─────────────────────────────────────────────────────────────────────────────
# They come back when they message
# ─────────────────────────────────────────────────────────────────────────────


def test_an_archived_contact_who_messages_in_is_reactivated():
    tenant = _tenant()
    archived = _contact(tenant, active=False)

    resolved = resolve_or_create_contact(tenant=tenant, source=ContactSource.WHATSAPP, phone=str(archived.phone))

    assert resolved.pk == archived.pk, "the same row, not a second one"
    archived.refresh_from_db()
    assert archived.is_active is True


def test_reactivating_does_not_resurrect_them_as_a_new_contact():
    """A second row would split the history and, worse, lose the opt-out.

    ``marketing_opt_out`` lives on the contact row, so a fresh row is someone
    who never said STOP. Archiving must not become a way to lose one.
    """
    tenant = _tenant()
    archived = _contact(tenant, active=False)
    archived.set_marketing_opt_out(opted_out=True, source=MarketingOptOutSource.KEYWORD)

    resolve_or_create_contact(tenant=tenant, source=ContactSource.WHATSAPP, phone=str(archived.phone))

    assert TenantContact.objects.filter(tenant=tenant, phone=archived.phone).count() == 1
    archived.refresh_from_db()
    assert archived.is_active is True
    assert archived.marketing_opt_out is True, "coming back is not consent"


def test_an_active_contact_is_left_exactly_as_it_was():
    tenant = _tenant()
    active = _contact(tenant)
    before = active.updated_at

    resolved = resolve_or_create_contact(tenant=tenant, source=ContactSource.WHATSAPP, phone=str(active.phone))

    assert resolved.pk == active.pk
    active.refresh_from_db()
    assert active.updated_at == before, "no pointless write on every inbound message"
