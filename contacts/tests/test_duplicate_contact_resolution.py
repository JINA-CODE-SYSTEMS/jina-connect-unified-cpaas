"""An inbound message from a number with two contacts goes to one of them.

Found on production (jina-connect-web#696): an operator tested a chat flow on
their own number, tapped a quick-reply button, and the flow never moved.

Nothing stops two contacts sharing a phone number. Once a second row existed,
``get_or_create`` raised ``MultipleObjectsReturned`` on every inbound message
and the fallback answered by creating another row — so the duplicates grew by
one per message (the log showed 4, 5, 6, 7) and the tap was filed against a
contact that had just been made. The flow's session was on a different row, so
``wa.tasks._handle_chatflow_routing`` found no session and dropped the tap.

HOW TO RUN:
    python -m pytest contacts/tests/test_duplicate_contact_resolution.py -v
"""

from __future__ import annotations

import itertools

import pytest

from chat_flow.models import ChatFlow, UserChatFlowSession
from contacts.models import AssigneeTypeChoices, ContactSource, TenantContact
from contacts.services import resolve_or_create_contact
from tenants.models import Tenant

pytestmark = pytest.mark.django_db

_seq = itertools.count(1)


def _tenant():
    return Tenant.objects.create(name=f"Org {next(_seq)}")


def _phone():
    return f"+2779{next(_seq):07d}"


def _contact(tenant, phone, **fields):
    return TenantContact.objects.create(tenant=tenant, phone=phone, **fields)


def _resolve(tenant, phone):
    return resolve_or_create_contact(tenant=tenant, source=ContactSource.WHATSAPP, phone=phone)


def test_a_duplicated_number_does_not_get_another_row():
    tenant, phone = _tenant(), _phone()
    _contact(tenant, phone)
    _contact(tenant, phone)

    for _ in range(3):
        _resolve(tenant, phone)

    assert TenantContact.objects.filter(tenant=tenant, phone=phone).count() == 2


def test_the_reply_goes_to_the_contact_the_flow_is_waiting_on():
    """The #696 case: the session is on a newer row than the oldest one."""
    tenant, phone = _tenant(), _phone()
    _contact(tenant, phone)
    in_flow = _contact(tenant, phone)
    flow = ChatFlow.objects.create(tenant=tenant, name="test", flow_data={"nodes": [], "edges": []})
    UserChatFlowSession.objects.create(contact=in_flow, flow=flow, current_node_id="template-1")

    assert _resolve(tenant, phone).pk == in_flow.pk


def test_a_finished_session_does_not_claim_the_reply():
    tenant, phone = _tenant(), _phone()
    oldest = _contact(tenant, phone)
    finished = _contact(tenant, phone)
    flow = ChatFlow.objects.create(tenant=tenant, name="done", flow_data={"nodes": [], "edges": []})
    UserChatFlowSession.objects.create(
        contact=finished, flow=flow, current_node_id="end-1", is_active=False, is_complete=True
    )

    assert _resolve(tenant, phone).pk == oldest.pk


def test_a_chat_flow_assignment_wins_without_a_session():
    tenant, phone = _tenant(), _phone()
    _contact(tenant, phone)
    assigned = _contact(tenant, phone, assigned_to_type=AssigneeTypeChoices.CHATFLOW, assigned_to_id=1)

    assert _resolve(tenant, phone).pk == assigned.pk


def test_otherwise_an_active_contact_then_the_oldest():
    tenant, phone = _tenant(), _phone()
    _contact(tenant, phone, is_active=False)
    oldest_active = _contact(tenant, phone)
    _contact(tenant, phone)

    assert _resolve(tenant, phone).pk == oldest_active.pk


def test_a_new_number_still_gets_a_contact():
    tenant, phone = _tenant(), _phone()

    contact = resolve_or_create_contact(
        tenant=tenant, source=ContactSource.WHATSAPP, phone=phone, defaults={"first_name": "Ada"}
    )

    assert contact.pk is not None
    assert contact.first_name == "Ada"
    assert contact.source == ContactSource.WHATSAPP
