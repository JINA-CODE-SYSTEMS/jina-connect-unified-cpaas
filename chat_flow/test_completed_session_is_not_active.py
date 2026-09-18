"""A conversation that finished is not one that is still running.

Found on production while checking why a flow could not be edited. The session
blocking it was sitting on the flow's **end** node with ``is_complete=True`` —
and ``is_active=True``. It had run to completion and was still being counted as
live.

``save_session_to_db`` passes ``is_active=True`` as a *lookup* argument to
``update_or_create`` rather than as a default, so completing a flow set
``is_complete`` and ``ended_at`` and never touched ``is_active``. The model's
own lifecycle notes say step 5 is "Session marked as completed
(is_active=False)"; nothing did it.

The cost is not an untidy table. Editing a flow is refused while a session is
active, so any flow somebody had run to the end could never be edited again —
which is the report that started this.

HOW TO RUN:
    python -m pytest chat_flow/test_completed_session_is_not_active.py -v
"""

from __future__ import annotations

import itertools

import pytest

from chat_flow.models import ChatFlow, UserChatFlowSession
from chat_flow.services.graph_executor import save_session_to_db
from contacts.models import TenantContact
from tenants.models import Tenant

pytestmark = pytest.mark.django_db

_seq = itertools.count(1)


def _setup():
    tenant = Tenant.objects.create(name=f"Org {next(_seq)}")
    flow = ChatFlow.objects.create(tenant=tenant, name=f"F{next(_seq)}", flow_data={"nodes": [], "edges": []})
    n = next(_seq)
    contact = TenantContact.objects.create(tenant=tenant, phone=f"+2782{n:07d}", first_name=f"C{n}")
    return flow, contact


def _state(flow, contact, node, *, complete):
    return {
        "flow_id": flow.pk,
        "contact_id": contact.pk,
        "current_node_id": node,
        "is_complete": complete,
    }


def test_reaching_an_end_node_ends_the_session():
    flow, contact = _setup()

    save_session_to_db(_state(flow, contact, "end-1", complete=True))

    session = UserChatFlowSession.objects.get(flow=flow, contact=contact)
    assert session.is_complete is True
    assert session.is_active is False, "a finished conversation is not an active one"
    assert session.ended_at is not None


def test_a_conversation_still_in_progress_stays_active():
    flow, contact = _setup()

    save_session_to_db(_state(flow, contact, "middle", complete=False))

    session = UserChatFlowSession.objects.get(flow=flow, contact=contact)
    assert session.is_active is True
    assert session.is_complete is False
    assert session.ended_at is None


def test_a_session_that_completes_after_running_is_ended_too():
    """The ordinary path: several steps, then the end node."""
    flow, contact = _setup()

    save_session_to_db(_state(flow, contact, "step-1", complete=False))
    save_session_to_db(_state(flow, contact, "end-1", complete=True))

    sessions = UserChatFlowSession.objects.filter(flow=flow, contact=contact)
    assert sessions.filter(is_active=True).count() == 0
    assert sessions.filter(is_complete=True).count() == 1


def test_a_completed_flow_can_be_edited_again():
    """The reported symptom, stated as the consequence rather than the field.

    The guard counts active sessions, so a completed conversation that stayed
    active blocked editing for good.
    """
    flow, contact = _setup()
    save_session_to_db(_state(flow, contact, "end-1", complete=True))

    blocking = UserChatFlowSession.objects.filter(flow=flow, is_active=True).count()

    assert blocking == 0
