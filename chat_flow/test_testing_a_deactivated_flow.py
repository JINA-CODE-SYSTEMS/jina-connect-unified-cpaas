"""You have to be able to test a flow you are still building.

Reported: *"if flow is deactivated - at least let me test."*

The Test button assigns a contact to the flow and lets the assignment signal
start the session. That signal refuses an inactive flow, and so does the task
behind it — correctly, because that path is how *automatic* assignment works
and an inactive flow should not grab contacts. But it leaves an operator with
no way to try a flow they have deactivated in order to edit it, which is the
state a flow is in for the whole time it is being built.

Worse, the refusal was invisible. ``POST /contacts/{id}/assign/`` answered
200, the signal logged a warning nobody reads, and the modal showed
"assigned" — a green success state for a message that was never sent.

So testing gets its own door: an explicit operator action, gated on
``chatflow.edit``, that runs the session synchronously and reports what
actually happened. Inbound replies already work without the flow being
active — ``wa.tasks`` routes on the session, not the flow's flag — so a test
conversation continues normally.

HOW TO RUN:
    python -m pytest chat_flow/test_testing_a_deactivated_flow.py -v
"""

from __future__ import annotations

import itertools
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from chat_flow.models import ChatFlow
from contacts.models import AssigneeTypeChoices, TenantContact
from tenants.models import Tenant, TenantRole, TenantUser

pytestmark = pytest.mark.django_db

_seq = itertools.count(1)


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _tenant():
    return Tenant.objects.create(name=f"Org {next(_seq)}")


def _owner_client(tenant):
    n = next(_seq)
    user = get_user_model().objects.create_user(
        username=f"owner{n}", email=f"owner{n}@example.test", password="pw"  # noqa: S106
    )
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _flow(tenant, *, is_active=False):
    return ChatFlow.objects.create(
        tenant=tenant,
        name=f"Flow {next(_seq)}",
        flow_data={"nodes": [], "edges": []},
        is_active=is_active,
    )


def _contact(tenant):
    n = next(_seq)
    return TenantContact.objects.create(tenant=tenant, phone=f"+2782{n:07d}", first_name=f"C{n}")


class _StubExecutor:
    """Stands in for the compiled graph — the executor has its own tests."""

    def __init__(self, state=None, raises=None):
        self.state = state or {
            "current_node_id": "msg-1",
            "awaiting_input": True,
            "is_complete": False,
            "messages_sent": [{"node_id": "msg-1"}],
            "error": None,
        }
        self.raises = raises
        self.started_with = None

    def start_session(self, contact_id, context=None):
        self.started_with = {"contact_id": contact_id, "context": context}
        if self.raises:
            raise self.raises
        return self.state


def _test_flow(client, flow, contact, executor=None):
    executor = executor or _StubExecutor()
    with patch("chat_flow.viewsets.chat_flow.get_executor", return_value=executor) as get_executor:
        response = client.post(f"/chat-flow/flows/{flow.id}/test/", {"contact_id": contact.id}, format="json")
    return response, executor, get_executor


# ─────────────────────────────────────────────────────────────────────────────
# The ask
# ─────────────────────────────────────────────────────────────────────────────


def test_a_deactivated_flow_can_still_be_tested():
    tenant = _tenant()
    flow = _flow(tenant, is_active=False)
    contact = _contact(tenant)

    response, executor, _ = _test_flow(_owner_client(tenant), flow, contact)

    assert response.status_code == 200
    assert response.json()["status"] == "started"
    assert executor.started_with["contact_id"] == contact.id


def test_testing_does_not_publish_the_flow():
    """A test is a test. Activating is a separate, deliberate action."""
    tenant = _tenant()
    flow = _flow(tenant, is_active=False)

    response, _, _ = _test_flow(_owner_client(tenant), flow, _contact(tenant))

    flow.refresh_from_db()
    assert flow.is_active is False
    assert response.json()["flow_active"] is False


def test_the_contact_is_assigned_so_their_reply_comes_back_to_the_flow():
    """``wa.tasks`` routes an inbound message on assignment or an open session."""
    tenant = _tenant()
    flow = _flow(tenant, is_active=False)
    contact = _contact(tenant)

    _test_flow(_owner_client(tenant), flow, contact)

    contact.refresh_from_db()
    assert contact.assigned_to_type == AssigneeTypeChoices.CHATFLOW
    assert contact.assigned_to_id == flow.id


def test_a_contact_already_assigned_to_this_flow_can_be_tested_again():
    """Reported on the live box, with the flow active: nothing happened.

    The assignment signal fires only when the assignment *changes*. A contact
    already sitting on this flow re-assigned to it is not a change, so the
    signal returned, no task was queued, and no message was sent — while the
    request answered 200 and the modal went green. Celery had not received a
    single task all day.
    """
    tenant = _tenant()
    flow = _flow(tenant, is_active=True)
    contact = _contact(tenant)
    TenantContact.objects.filter(pk=contact.pk).update(
        assigned_to_type=AssigneeTypeChoices.CHATFLOW, assigned_to_id=flow.id
    )

    response, executor, _ = _test_flow(_owner_client(tenant), flow, contact)

    assert response.status_code == 200
    assert executor.started_with["contact_id"] == contact.id


def test_testing_does_not_queue_a_second_session_behind_the_one_it_runs():
    """Assigning through the ORM would fire the signal, which queues a start.

    Two starts means two first messages to the same contact.
    """
    tenant = _tenant()
    flow = _flow(tenant, is_active=True)
    contact = _contact(tenant)  # not yet assigned: the signal would fire

    with patch("chat_flow.tasks.start_chatflow_session_task.delay") as queued:
        _test_flow(_owner_client(tenant), flow, contact)

    queued.assert_not_called()


def test_the_graph_is_recompiled_so_the_test_runs_the_saved_flow():
    """An operator tests immediately after editing; a cached graph is the old one."""
    tenant = _tenant()
    flow = _flow(tenant, is_active=True)

    with patch("chat_flow.viewsets.chat_flow.clear_graph_cache") as cleared:
        with patch("chat_flow.viewsets.chat_flow.get_executor", return_value=_StubExecutor()):
            _owner_client(tenant).post(
                f"/chat-flow/flows/{flow.id}/test/", {"contact_id": _contact(tenant).id}, format="json"
            )

    cleared.assert_called_once_with(flow.id)


def test_an_active_flow_is_tested_the_same_way():
    tenant = _tenant()
    flow = _flow(tenant, is_active=True)

    response, _, _ = _test_flow(_owner_client(tenant), flow, _contact(tenant))

    assert response.status_code == 200
    assert response.json()["flow_active"] is True


# ─────────────────────────────────────────────────────────────────────────────
# It has to say what happened
# ─────────────────────────────────────────────────────────────────────────────


def test_the_answer_says_what_the_flow_did():
    """The old path answered 200 for a message it never sent."""
    tenant = _tenant()

    response, _, _ = _test_flow(_owner_client(tenant), _flow(tenant), _contact(tenant))

    body = response.json()
    assert body["current_node_id"] == "msg-1"
    assert body["messages_sent"] == 1
    assert body["awaiting_input"] is True


def test_a_flow_that_errors_reports_the_error():
    tenant = _tenant()
    executor = _StubExecutor(
        state={"current_node_id": "", "messages_sent": [], "error": "No start node", "is_complete": False}
    )

    response, _, _ = _test_flow(_owner_client(tenant), _flow(tenant), _contact(tenant), executor=executor)

    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "No start node"


def test_an_executor_that_raises_is_not_reported_as_success():
    tenant = _tenant()
    executor = _StubExecutor(raises=RuntimeError("template 42 is not approved"))

    response, _, _ = _test_flow(_owner_client(tenant), _flow(tenant), _contact(tenant), executor=executor)

    assert response.status_code == 500
    assert "template 42 is not approved" in response.json()["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Scope
# ─────────────────────────────────────────────────────────────────────────────


def test_a_contact_from_another_tenant_cannot_be_used():
    tenant = _tenant()
    stranger = _contact(_tenant())
    flow = _flow(tenant)

    response, executor, _ = _test_flow(_owner_client(tenant), flow, stranger)

    assert response.status_code == 400
    assert executor.started_with is None
    stranger.refresh_from_db()
    assert stranger.assigned_to_type != AssigneeTypeChoices.CHATFLOW


def test_another_tenants_flow_is_not_visible():
    tenant = _tenant()
    other_flow = _flow(_tenant())
    contact = _contact(tenant)

    response, executor, _ = _test_flow(_owner_client(tenant), other_flow, contact)

    assert response.status_code == 404
    assert executor.started_with is None


def test_a_contact_is_required():
    tenant = _tenant()
    client = _owner_client(tenant)
    flow = _flow(tenant)

    with patch("chat_flow.viewsets.chat_flow.get_executor") as get_executor:
        response = client.post(f"/chat-flow/flows/{flow.id}/test/", {}, format="json")

    assert response.status_code == 400
    get_executor.assert_not_called()
