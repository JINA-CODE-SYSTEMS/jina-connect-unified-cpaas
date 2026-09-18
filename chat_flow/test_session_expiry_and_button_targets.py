"""Three defects behind one report: "Cannot edit flow while 1 session(s) are active".

An operator could not save a flow. The message named no way out, and the
session blocking them could never have gone away on its own.

1. **Sessions never expired.** ``UserChatFlowSession`` ended at an end node, on
   an explicit reset, or when its flow was deactivated — nowhere else. The
   model docstring says "until end node reached or session expires",
   ``ended_at`` documents an "expired" state, and the index on
   ``("is_active", "started_at")`` exists for a sweep nobody wrote. So one
   contact who started a flow and never replied blocked that flow from being
   edited permanently.

2. **The guard refused every edit, not the dangerous ones.** Editing runs
   ``flow.nodes.all().delete()`` and rebuilds, and a session stores
   ``current_node_id`` as a string — so the risk is real, but only for a
   session standing on a node the edit removes.

3. **Two edges from one button silently discarded one.** ``button_routes`` is
   built by plain dict assignment, so the last edge wins and the other branch
   is unreachable while the canvas keeps drawing it.

HOW TO RUN:
    python -m pytest chat_flow/test_session_expiry_and_button_targets.py -v
"""

from __future__ import annotations

import itertools
from datetime import timedelta

import pytest
from django.utils import timezone

from chat_flow.models import ChatFlow, ChatFlowNode, UserChatFlowSession
from chat_flow.services.session_expiry import EXPIRY_REASON, expire_idle_sessions, stale_sessions
from contacts.models import TenantContact
from tenants.models import Tenant

pytestmark = pytest.mark.django_db

_seq = itertools.count(1)


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _flow(name="Flow"):
    tenant = Tenant.objects.create(name=f"Org {next(_seq)}")
    return ChatFlow.objects.create(tenant=tenant, name=f"{name} {next(_seq)}", flow_data={"nodes": [], "edges": []})


def _contact(tenant):
    n = next(_seq)
    return TenantContact.objects.create(tenant=tenant, phone=f"+2782{n:07d}", first_name=f"C{n}")


def _session(flow, *, node="node-a", idle_hours=0):
    session = UserChatFlowSession.objects.create(
        tenant=flow.tenant,
        flow=flow,
        contact=_contact(flow.tenant),
        current_node_id=node,
        is_active=True,
    )
    if idle_hours:
        # ``updated_at`` is auto_now, so it cannot be set through save() — the
        # only way to age a row is to write the column directly.
        UserChatFlowSession.objects.filter(pk=session.pk).update(
            updated_at=timezone.now() - timedelta(hours=idle_hours)
        )
        session.refresh_from_db()
    return session


def _node(flow, node_id, node_type="template"):
    return ChatFlowNode.objects.create(flow=flow, node_id=node_id, node_type=node_type, position_x=0, position_y=0)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Sessions expire
# ─────────────────────────────────────────────────────────────────────────────


def test_a_session_that_stopped_advancing_is_ended():
    flow = _flow()
    stale = _session(flow, idle_hours=100)

    assert expire_idle_sessions() == 1

    stale.refresh_from_db()
    assert stale.is_active is False
    assert stale.ended_at is not None
    assert stale.cancellation_reason == EXPIRY_REASON


def test_a_session_still_being_answered_is_left_alone():
    flow = _flow()
    live = _session(flow, idle_hours=1)

    assert expire_idle_sessions() == 0

    live.refresh_from_db()
    assert live.is_active is True


def test_a_session_parked_at_a_delay_node_is_never_swept():
    """The failure this sweep could most easily have caused.

    A delay node parks the session and schedules a Celery task to resume it —
    ``trigger_at`` can be weeks out — so an idle sweep that did not know about
    delay nodes would cancel every scheduled flow on the platform. That is a
    worse bug than the one being fixed.
    """
    flow = _flow()
    _node(flow, "wait-here", node_type="delay")
    parked = _session(flow, node="wait-here", idle_hours=1000)

    assert expire_idle_sessions() == 0

    parked.refresh_from_db()
    assert parked.is_active is True


def test_a_delay_node_in_another_flow_does_not_shelter_this_one():
    """``node_id`` is unique per flow, not globally.

    Matching on the id alone would let a delay node called "wait" in any flow
    protect a stuck session sitting on a template called "wait" in another.
    """
    sheltered, other = _flow("A"), _flow("B")
    _node(other, "wait", node_type="delay")
    stuck = _session(sheltered, node="wait", idle_hours=500)

    assert expire_idle_sessions() == 1

    stuck.refresh_from_db()
    assert stuck.is_active is False


def test_an_already_ended_session_is_not_counted_twice():
    flow = _flow()
    _session(flow, idle_hours=200)
    expire_idle_sessions()

    assert expire_idle_sessions() == 0
    assert stale_sessions().count() == 0


def test_the_timeout_can_be_overridden():
    flow = _flow()
    _session(flow, idle_hours=30)

    assert expire_idle_sessions(hours=48) == 0, "30h idle is not stale at a 48h timeout"
    assert expire_idle_sessions(hours=24) == 1, "and is stale at a 24h one"


def test_one_flow_can_be_swept_without_touching_others():
    mine, theirs = _flow("Mine"), _flow("Theirs")
    _session(mine, idle_hours=200)
    untouched = _session(theirs, idle_hours=200)

    assert expire_idle_sessions(flow=mine) == 1

    untouched.refresh_from_db()
    assert untouched.is_active is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. One target per button
# ─────────────────────────────────────────────────────────────────────────────


def _graph(edges):
    return {
        "nodes": [
            {"id": "tpl", "type": "template", "position": {"x": 0, "y": 0}, "data": {"label": "T"}},
            {"id": "end-1", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "End"}},
            {"id": "tpl-2", "type": "template", "position": {"x": 2, "y": 0}, "data": {"label": "T2"}},
        ],
        "edges": edges,
    }


def _edge(edge_id, handle, target, text):
    return {
        "id": edge_id,
        "source": "tpl",
        "target": target,
        "sourceHandle": handle,
        "data": {"button_id": f"btn-{text}", "button_text": text, "button_type": "QUICK_REPLY"},
    }


def _violations(graph):
    from chat_flow.rules.structural import OneTargetPerButtonRule

    return OneTargetPerButtonRule().validate(graph)


def test_one_button_pointing_at_two_nodes_is_reported():
    """The reported flow's actual shape: button-0 wired to two targets."""
    graph = _graph(
        [
            _edge("e1", "button-0", "end-1", "Tell me more"),
            _edge("e2", "button-0", "tpl-2", "Tell me more"),
        ]
    )

    violations = _violations(graph)

    assert len(violations) == 1
    message = violations[0].message
    assert "Tell me more" in message, "which button"
    assert "end-1" in message and "tpl-2" in message, "and where both lines go"


def test_different_buttons_going_to_different_nodes_are_fine():
    graph = _graph(
        [
            _edge("e1", "button-0", "end-1", "Tell me more"),
            _edge("e2", "button-1", "tpl-2", "Let's have a call"),
        ]
    )

    assert _violations(graph) == []


def test_two_buttons_may_share_a_destination():
    """Ordinary: several replies that all end the conversation."""
    graph = _graph(
        [
            _edge("e1", "button-0", "end-1", "Tell me more"),
            _edge("e2", "button-1", "end-1", "Not Interested"),
        ]
    )

    assert _violations(graph) == []


def test_the_same_handle_on_two_different_nodes_is_not_a_clash():
    """Every node has a ``button-0``; they are different buttons."""
    graph = _graph([_edge("e1", "button-0", "end-1", "Yes")])
    graph["edges"].append(
        {"id": "e2", "source": "tpl-2", "target": "end-1", "sourceHandle": "button-0", "data": {"button_text": "Yes"}}
    )

    assert _violations(graph) == []


def test_a_passthrough_edge_is_not_a_button():
    graph = _graph(
        [
            {"id": "e1", "source": "tpl", "target": "end-1", "sourceHandle": "bottom"},
            {"id": "e2", "source": "tpl", "target": "tpl-2", "sourceHandle": "bottom"},
        ]
    )

    assert _violations(graph) == [], "bottom-handle edges are STRUCT_010's business, not this rule's"


# ─────────────────────────────────────────────────────────────────────────────
# 2. The guard refuses the dangerous edit, not every edit
# ─────────────────────────────────────────────────────────────────────────────

FLOW_URL = "/chat-flow/flows/{pk}/"


def _editor(tenant):
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIClient

    from tenants.models import TenantRole, TenantUser

    user_model = get_user_model()
    n = next(_seq)
    user = user_model.objects.create_user(username=f"ed{n}", email=f"ed{n}@example.test", password="pw")  # noqa: S106
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _body(node_ids):
    return {
        "flow_data": {
            "nodes": [
                {"id": nid, "type": "template", "position": {"x": 0, "y": 0}, "data": {"label": "N"}}
                for nid in node_ids
            ],
            "edges": [],
        }
    }


def test_an_edit_that_keeps_the_node_is_allowed():
    """The case that used to be refused for no reason.

    A live session exists, but the node it is standing on survives the edit, so
    nobody is stranded and there is nothing to protect them from.
    """
    flow = _flow()
    _session(flow, node="keep-me", idle_hours=1)
    api = _editor(flow.tenant)

    response = api.patch(FLOW_URL.format(pk=flow.pk), _body(["keep-me", "new-node"]), format="json")

    assert response.status_code != 409, response.data


def test_an_edit_that_removes_the_node_somebody_is_on_is_refused():
    flow = _flow()
    _session(flow, node="about-to-vanish", idle_hours=1)
    api = _editor(flow.tenant)

    response = api.patch(FLOW_URL.format(pk=flow.pk), _body(["something-else"]), format="json")

    assert response.status_code == 409, response.data
    assert response.data["active_session_count"] == 1
    assert response.data["stranded_nodes"] == ["about-to-vanish"]


def test_the_refusal_names_the_endpoint_that_clears_it():
    """The original message said "Deactivate the flow first" and stopped there.

    The one control that does it is an API call the editor does not surface, so
    a reader had the instruction and no way to carry it out.
    """
    flow = _flow()
    _session(flow, node="stuck", idle_hours=1)
    api = _editor(flow.tenant)

    response = api.patch(FLOW_URL.format(pk=flow.pk), _body(["other"]), format="json")

    assert f"/chat-flow/flows/{flow.pk}/deactivate/" in response.data["message"]
    assert response.data["resolution"]["deactivate"].endswith(f"/flows/{flow.pk}/deactivate/")
    assert response.data["resolution"]["reactivate"].endswith(f"/flows/{flow.pk}/activate/")


def test_a_session_that_stopped_advancing_does_not_block_the_edit():
    """The reported bug, end to end.

    One contact who never replied held a session open forever, and the guard
    counted it. The sweep will end it on the hour, but an operator refused now
    should not have to wait for that.
    """
    flow = _flow()
    _session(flow, node="abandoned", idle_hours=500)
    api = _editor(flow.tenant)

    response = api.patch(FLOW_URL.format(pk=flow.pk), _body(["rebuilt"]), format="json")

    assert response.status_code != 409, response.data


def test_an_edit_that_does_not_touch_flow_data_is_never_blocked():
    flow = _flow()
    _session(flow, node="anywhere", idle_hours=1)
    api = _editor(flow.tenant)

    response = api.patch(FLOW_URL.format(pk=flow.pk), {"description": "renamed"}, format="json")

    assert response.status_code != 409, response.data
