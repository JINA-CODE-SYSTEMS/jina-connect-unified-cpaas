"""Authored node types and executor branches must agree (#273).

chat_flow had two independent lists of what a flow can contain: the authoring
layer validated against `constants.VALID_NODE_TYPES` / the session-message
rules, and `graph_executor` switched on its own hand-written set of branches.
Nothing asserted the two agreed, so types drifted in one at a time and each
one degraded quietly at runtime — an `interactive_list` node validated, routed
per row, and rendered in admin, then reached the customer as a paragraph with
no rows, and the flow stalled forever waiting for a row reply that could never
be sent.

These tests are the check that was missing. They walk the *authored* lists and
assert each entry reaches its own executor branch, so the next type added to
`constants.py` without a branch fails here instead of in a customer's chat.

HOW TO RUN:
    .venv/bin/python -m pytest chat_flow/test_node_type_coverage.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from chat_flow.constants import (
    SESSION_MESSAGE_TYPE_ALIASES,
    SESSION_MESSAGE_TYPES,
    VALID_NODE_TYPES,
    canonical_session_message_type,
)
from chat_flow.models import ChatFlow, ChatFlowNode
from chat_flow.services.graph_executor import ChatFlowExecutor, send_session_message
from contacts.models import TenantContact
from tenants.models import Tenant, TenantWAApp
from wa.models import MessageDirection, MessageStatus, MessageType, WAMessage

# ─────────────────────────────────────────────────────────────────────────────
# The contract: one payload shape per authored session-message type
#
# Each entry is the minimum node_data an author would configure, paired with
# the Cloud API message type it must produce. A type that degrades to text
# shows up as an expected/actual mismatch rather than as a passing test.
# ─────────────────────────────────────────────────────────────────────────────

_ORDER = {
    "reference_id": "order-12345",
    "type": "digital-goods",
    "currency": "INR",
    "total_amount": {"value": 30000, "offset": 100},
    "payment_settings": [
        {
            "type": "payment_gateway",
            "payment_gateway": {"type": "razorpay", "configuration_name": "test-config"},
        }
    ],
    "order": {
        "items": [{"name": "Earbuds", "amount": {"value": 25000, "offset": 100}, "quantity": 1}],
        "subtotal": {"value": 25000, "offset": 100},
        "tax": {"value": 5000, "offset": 100},
    },
}

SESSION_MESSAGE_EXPECTATIONS: dict[str, tuple[dict, str, str | None]] = {
    # message_type: (node_data, payload["type"], payload["interactive"]["type"])
    "text": ({"message_content": "Hello there"}, "text", None),
    "image": ({"message_content": "https://cdn.example.com/a.jpg"}, "image", None),
    "video": ({"message_content": "https://cdn.example.com/a.mp4"}, "video", None),
    "audio": ({"message_content": "https://cdn.example.com/a.ogg"}, "audio", None),
    "document": ({"message_content": "https://cdn.example.com/a.pdf"}, "document", None),
    "sticker": ({"message_content": "https://cdn.example.com/a.webp"}, "sticker", None),
    "location": (
        {"location": {"latitude": 51.5007, "longitude": -0.1246, "name": "The shop"}},
        "location",
        None,
    ),
    "contacts": ({"contacts": [{"name": {"formatted_name": "Ada Lovelace"}}]}, "contacts", None),
    "reaction": ({"reaction": {"message_id": "wamid.TESTID", "emoji": "👍"}}, "reaction", None),
    "interactive_button": (
        {"body": "Pick one", "buttons": [{"type": "QUICK_REPLY", "id": "yes", "title": "Yes"}]},
        "interactive",
        "button",
    ),
    "interactive_list": (
        {
            "body": "Pick a slot",
            "sections": [{"title": "Morning", "rows": [{"id": "r1", "title": "09:00"}]}],
        },
        "interactive",
        "list",
    ),
    "cta_url": (
        {"body": "See the catalogue", "cta_url": {"display_text": "Open", "url": "https://example.com"}},
        "interactive",
        "cta_url",
    ),
    "order_details": ({"body": "Pay up", "order_details": _ORDER}, "interactive", "order_details"),
    "order_status": (
        {"body": "On its way", "order_status": {"reference_id": "order-12345", "status": "shipped"}},
        "interactive",
        "order_status",
    ),
}


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name=f"NodeTypes-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def wa_app(tenant):
    return TenantWAApp.objects.create(
        tenant=tenant,
        app_name=f"app-{uuid.uuid4().hex[:6]}",
        app_id=f"id-{uuid.uuid4().hex[:6]}",
        app_secret="secret",
        wa_number="+14155550100",
        is_active=True,
    )


@pytest.fixture
def contact(tenant, wa_app):
    return TenantContact.objects.create(tenant=tenant, phone="+14155550101")


@pytest.fixture(autouse=True)
def no_real_sends():
    """Keep the WAMessage post_save signal from talking to a BSP.

    The signal runs the send task in-process when no broker is configured,
    which in tests means a real HTTP attempt.
    """
    with patch("wa.tasks.send_wa_message") as task:
        yield task


def _send(contact, message_type, node_data):
    """Send one session message and hand back its stored WAMessage."""
    result = send_session_message(contact_id=contact.id, node_data={"message_type": message_type, **node_data})
    assert result["success"], result["error"]
    return WAMessage.objects.get(id=result["outgoing_message_id"])


# ─────────────────────────────────────────────────────────────────────────────
# Every authored session-message type reaches its own branch
# ─────────────────────────────────────────────────────────────────────────────


def test_every_advertised_type_is_covered_by_this_test():
    """The expectations table must not fall behind constants.py.

    Without this, adding a type to SESSION_MESSAGE_TYPES and forgetting the
    branch would also mean forgetting the case that catches it.
    """
    assert set(SESSION_MESSAGE_EXPECTATIONS) == set(SESSION_MESSAGE_TYPES)


@pytest.mark.parametrize("message_type", sorted(SESSION_MESSAGE_EXPECTATIONS))
def test_each_session_message_type_sends_its_own_payload(contact, message_type):
    node_data, expected_type, expected_interactive = SESSION_MESSAGE_EXPECTATIONS[message_type]

    message = _send(contact, message_type, node_data)

    assert message.raw_payload["type"] == expected_type, (
        f"'{message_type}' was sent as a {message.raw_payload['type']} message, not {expected_type} — "
        f"send_session_message has no branch for it and fell through to the text default"
    )
    if expected_interactive:
        assert message.raw_payload["interactive"]["type"] == expected_interactive


@pytest.mark.parametrize("alias, canonical", sorted(SESSION_MESSAGE_TYPE_ALIASES.items()))
def test_editor_aliases_reach_the_same_branch(contact, alias, canonical):
    """'list' must behave exactly like 'interactive_list', and so on."""
    node_data, expected_type, expected_interactive = SESSION_MESSAGE_EXPECTATIONS[canonical]

    message = _send(contact, alias, node_data)

    assert message.raw_payload["type"] == expected_type
    if expected_interactive:
        assert message.raw_payload["interactive"]["type"] == expected_interactive


def test_an_unsupported_type_fails_instead_of_degrading(contact):
    """A type with no branch must fail the send, not arrive as a paragraph."""
    result = send_session_message(
        contact_id=contact.id,
        node_data={"message_type": "carousel", "message_content": "Look at these"},
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "carousel" in result["error"]
    assert not WAMessage.objects.filter(contact=contact).exists()


# ─────────────────────────────────────────────────────────────────────────────
# interactive_list — the one that stalled flows
# ─────────────────────────────────────────────────────────────────────────────


def test_list_rows_survive_into_the_payload(contact):
    """Rows, ids and section titles must reach WhatsApp, not just the body."""
    node_data, _, _ = SESSION_MESSAGE_EXPECTATIONS["interactive_list"]
    node_data = {
        **node_data,
        "button_text": "See slots",
        "sections": [
            {
                "title": "Morning",
                "rows": [{"id": "r1", "title": "09:00", "description": "Earliest"}],
            },
            {"title": "Afternoon", "rows": [{"id": "r2", "title": "14:00"}]},
        ],
    }

    message = _send(contact, "interactive_list", node_data)
    action = message.raw_payload["interactive"]["action"]

    assert action["button"] == "See slots"
    assert [s["title"] for s in action["sections"]] == ["Morning", "Afternoon"]
    rows = [row for section in action["sections"] for row in section["rows"]]
    # Row ids are what WhatsApp echoes back in the list_reply, and row titles
    # are what the per-row edges are keyed on — both have to be intact for the
    # reply to route anywhere.
    assert [r["id"] for r in rows] == ["r1", "r2"]
    assert [r["title"] for r in rows] == ["09:00", "14:00"]


def test_a_list_message_node_waits_for_the_row_reply(tenant, contact):
    """A list node must pause the graph; passing through skips the customer.

    Rows live under 'sections', so the handler's quick-reply check missed them
    entirely and the flow walked on while the menu was still open.
    """
    flow = ChatFlow.objects.create(name="List flow", tenant=tenant, flow_data={"nodes": [], "edges": []})
    node = ChatFlowNode.objects.create(
        flow=flow,
        node_id="list-node",
        node_type="message",
        position_x=0,
        position_y=0,
        node_data={
            "message_type": "interactive_list",
            "body": "Pick a slot",
            "sections": [{"title": "Morning", "rows": [{"id": "r1", "title": "09:00"}]}],
        },
    )

    handler = ChatFlowExecutor(flow)._create_node_handler(node, [])
    state = handler(
        {
            "flow_id": flow.id,
            "contact_id": contact.id,
            "current_node_id": "",
            "user_input": None,
            "messages_sent": [],
            "context": {},
            "is_complete": False,
            "awaiting_input": False,
            "error": None,
            "_resume_target": None,
            "_pending_user_input": None,
        }
    )

    assert state["awaiting_input"] is True
    assert state["user_input"] is None, "a list node that passes through never sees the row reply"


# ─────────────────────────────────────────────────────────────────────────────
# order_details currency (#263, #273)
# ─────────────────────────────────────────────────────────────────────────────


def test_order_details_currency_defaults_to_the_deployment(contact, settings):
    """An order priced in the deployment's currency is never relabelled INR."""
    settings.PLATFORM_DEFAULT_CURRENCY = "ZAR"
    order = {k: v for k, v in _ORDER.items() if k != "currency"}

    result = send_session_message(
        contact_id=contact.id,
        node_data={"message_type": "order_details", "body": "Pay up", "order_details": order},
    )

    assert result["success"] is False
    assert "ZAR" in result["error"]
    assert not WAMessage.objects.filter(contact=contact).exists()


def test_order_details_sends_on_an_inr_deployment(contact, settings):
    settings.PLATFORM_DEFAULT_CURRENCY = "INR"
    order = {k: v for k, v in _ORDER.items() if k != "currency"}

    message = _send(contact, "order_details", {"body": "Pay up", "order_details": order})

    params = message.raw_payload["interactive"]["action"]["parameters"]
    assert params["currency"] == "INR"


def test_an_explicit_order_currency_wins_over_the_default(contact, settings):
    """The node's own currency is honoured, the same way wallet stamping does."""
    settings.PLATFORM_DEFAULT_CURRENCY = "ZAR"

    message = _send(contact, "order_details", {"body": "Pay up", "order_details": _ORDER})

    assert message.raw_payload["interactive"]["action"]["parameters"]["currency"] == "INR"


# ─────────────────────────────────────────────────────────────────────────────
# reaction resolves its target
# ─────────────────────────────────────────────────────────────────────────────


def test_a_reaction_defaults_to_the_last_inbound_message(contact, wa_app):
    """An author cannot know a wamid, so the node reacts to what was just said."""
    WAMessage.objects.create(
        wa_app=wa_app,
        contact=contact,
        direction=MessageDirection.INBOUND,
        message_type=MessageType.TEXT,
        status=MessageStatus.DELIVERED,
        text="is it ready?",
        wa_message_id="wamid.INBOUND1",
    )

    result = send_session_message(
        contact_id=contact.id,
        node_data={"message_type": "reaction", "reaction": {"emoji": "👍"}},
    )

    assert result["success"], result["error"]
    message = WAMessage.objects.get(id=result["outgoing_message_id"])
    assert message.raw_payload["reaction"]["message_id"] == "wamid.INBOUND1"


def test_a_reaction_with_nothing_to_react_to_fails(contact):
    result = send_session_message(
        contact_id=contact.id,
        node_data={"message_type": "reaction", "reaction": {"emoji": "👍"}},
    )

    assert result["success"] is False
    assert "no inbound message" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Every authored node type reaches its own handler
# ─────────────────────────────────────────────────────────────────────────────

# The handler each node type must get. 'template' is the only type allowed to
# use the template handler — everything else landing there is the #273 bug:
# a template node with template_id=None is a bare passthrough that sends
# nothing and says nothing.
NODE_HANDLER_FACTORIES = {
    "start": "create_start_node_handler",
    "template": "create_template_node_handler",
    "end": "create_end_node_handler",
    "condition": "create_condition_node_handler",
    "action": "create_passthrough_node_handler",
    "delay": "create_delay_node_handler",
    "message": "create_message_node_handler",
    "handoff": "create_handoff_node_handler",
    "api": "create_api_call_node_handler",
}


def test_every_valid_node_type_has_an_expected_handler():
    """A node type added to constants.py must declare which handler runs it."""
    assert set(NODE_HANDLER_FACTORIES) == set(VALID_NODE_TYPES)


@pytest.mark.parametrize("node_type", sorted(NODE_HANDLER_FACTORIES))
def test_each_node_type_gets_its_own_handler(tenant, node_type):
    flow = ChatFlow.objects.create(name=f"Flow {node_type}", tenant=tenant, flow_data={"nodes": [], "edges": []})
    node = ChatFlowNode.objects.create(
        flow=flow,
        node_id=f"{node_type}-1",
        node_type=node_type,
        position_x=0,
        position_y=0,
        node_data={"label": node_type},
    )

    handler = ChatFlowExecutor(flow)._create_node_handler(node, [])

    # A closure's qualname names the factory that built it, which is the only
    # thing that distinguishes "handled" from "fell through to template".
    factory = handler.__qualname__.split(".")[0]
    assert factory == NODE_HANDLER_FACTORIES[node_type], (
        f"'{node_type}' nodes are run by {factory}, not {NODE_HANDLER_FACTORIES[node_type]} — "
        f"_create_node_handler has no branch for this type"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Canonical spellings
# ─────────────────────────────────────────────────────────────────────────────


def test_a_missing_message_type_is_text():
    assert canonical_session_message_type(None) == "text"
    assert canonical_session_message_type("") == "text"


def test_aliases_resolve_to_advertised_types():
    for alias, canonical in SESSION_MESSAGE_TYPE_ALIASES.items():
        assert canonical_session_message_type(alias) == canonical
        assert canonical in SESSION_MESSAGE_TYPES


# ─────────────────────────────────────────────────────────────────────────────
# handoff is no longer a runtime no-op
# ─────────────────────────────────────────────────────────────────────────────


def _run_handoff(flow, contact, node_data):
    node = ChatFlowNode.objects.create(
        flow=flow,
        node_id="handoff-1",
        node_type="handoff",
        position_x=0,
        position_y=0,
        node_data=node_data,
    )
    handler = ChatFlowExecutor(flow)._create_node_handler(node, [])
    return handler(
        {
            "flow_id": flow.id,
            "contact_id": contact.id,
            "current_node_id": "",
            "user_input": None,
            "messages_sent": [],
            "context": {},
            "is_complete": False,
            "awaiting_input": False,
            "error": None,
            "_resume_target": None,
            "_pending_user_input": None,
        }
    )


@pytest.fixture
def flow(tenant):
    return ChatFlow.objects.create(name="Handoff flow", tenant=tenant, flow_data={"nodes": [], "edges": []})


def test_a_handoff_takes_the_bot_off_the_conversation(flow, contact):
    """The whole point of a handoff: the ChatFlow stops owning the ticket."""
    from contacts.models import AssigneeTypeChoices

    contact.assigned_to_type = AssigneeTypeChoices.CHATFLOW
    contact.assigned_to_id = flow.id
    contact.save()

    state = _run_handoff(flow, contact, {"label": "To a human"})

    contact.refresh_from_db()
    assert contact.assigned_to_type == AssigneeTypeChoices.UNASSIGNED
    assert contact.assigned_by_type == AssigneeTypeChoices.CHATFLOW
    assert state["error"] is None


def test_a_handoff_sends_its_message_to_the_customer(flow, contact):
    state = _run_handoff(flow, contact, {"label": "To a human", "handoff_message": "One moment, fetching a colleague"})

    message = WAMessage.objects.get(contact=contact, direction=MessageDirection.OUTBOUND)
    assert message.text == "One moment, fetching a colleague"
    assert state["messages_sent"] == [f"session:{message.id}"]


def test_a_handoff_to_a_named_agent_assigns_them(flow, contact, tenant):
    from django.contrib.auth import get_user_model

    from contacts.models import AssigneeTypeChoices
    from tenants.models import TenantRole, TenantUser

    agent = get_user_model().objects.create_user(
        username=f"agent-{uuid.uuid4().hex[:6]}",
        email=f"agent-{uuid.uuid4().hex[:6]}@example.com",
        mobile=f"+9100000{uuid.uuid4().int % 10**5:05d}",
        password="testpass123",
    )
    TenantUser.objects.create(
        user=agent,
        tenant=tenant,
        role=TenantRole.objects.get(tenant=tenant, slug="owner"),
        is_active=True,
    )

    _run_handoff(flow, contact, {"label": "To Sam", "assignment_type": "agent", "agent_id": agent.pk})

    contact.refresh_from_db()
    assert contact.assigned_to_type == AssigneeTypeChoices.USER
    assert contact.assigned_to_user_id == agent.pk


def test_a_handoff_to_another_tenants_agent_does_not_cross_the_boundary(flow, contact):
    from django.contrib.auth import get_user_model

    from contacts.models import AssigneeTypeChoices

    outsider = get_user_model().objects.create_user(
        username=f"outsider-{uuid.uuid4().hex[:6]}",
        email=f"outsider-{uuid.uuid4().hex[:6]}@example.com",
        mobile=f"+9100001{uuid.uuid4().int % 10**5:05d}",
        password="testpass123",
    )

    _run_handoff(flow, contact, {"label": "To an outsider", "assignment_type": "agent", "agent_id": outsider.pk})

    contact.refresh_from_db()
    assert contact.assigned_to_type == AssigneeTypeChoices.UNASSIGNED
    assert contact.assigned_to_user_id is None


def test_a_handoff_records_a_team_inbox_event(flow, contact):
    from team_inbox.models import Event

    _run_handoff(flow, contact, {"label": "To a human"})

    event = Event.objects.get(contact=contact)
    assert event.event_data["reason"] == "handoff_node"
    assert event.event_data["chatflow_name"] == flow.name


# ─────────────────────────────────────────────────────────────────────────────
# Authoring rejects what the executor cannot send
# ─────────────────────────────────────────────────────────────────────────────


def _validate(nodes, edges=None):
    from chat_flow.services.flow_processor import ChatFlowProcessor

    return ChatFlowProcessor.validate_flow_rules(
        {"nodes": nodes, "edges": edges or []},
        skip_db_checks=True,
    )


def _message_node(node_id="msg-1", **data):
    return {
        "id": node_id,
        "type": "message",
        "position": {"x": 0, "y": 0},
        "data": {"label": "A message", **data},
    }


@pytest.mark.django_db
def test_an_unsupported_message_type_is_rejected_at_authoring():
    """The author hears about it before a customer does."""
    result = _validate([_message_node(message_type="carousel", message_content="Look")])

    violations = [v for v in result.errors if v.rule_id == "SESSION_006"]
    assert violations, "an unsendable message_type must be an authoring error"
    assert "carousel" in violations[0].message


@pytest.mark.django_db
@pytest.mark.parametrize("message_type", sorted(SESSION_MESSAGE_TYPES))
def test_every_advertised_type_passes_authoring(message_type):
    """SESSION_006 must not reject a type send_session_message can send."""
    result = _validate([_message_node(message_type=message_type)])

    assert not any(v.rule_id == "SESSION_006" for v in result.violations)


@pytest.mark.django_db
def test_a_list_with_no_rows_is_rejected_at_authoring():
    """A rowless list can only be sent as prose, which stalls the flow."""
    result = _validate([_message_node(message_type="interactive_list", sections=[])])

    violations = [v for v in result.errors if v.rule_id == "SESSION_004"]
    assert violations, "a list with no rows must be an authoring error"
    assert "no rows" in violations[0].message


@pytest.mark.django_db
def test_a_list_node_may_have_one_edge_per_row():
    """Row-routed list nodes are correctly authored and must validate.

    Rows live under 'sections', so the branching rules — which only looked at
    'buttons' — used to flag a per-row list node as over-connected (#273).
    """
    nodes = [
        _message_node(
            message_type="interactive_list",
            sections=[{"title": "Slots", "rows": [{"id": "r1", "title": "09:00"}, {"id": "r2", "title": "14:00"}]}],
        ),
        {"id": "end-1", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "End"}},
        {"id": "end-2", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "End"}},
    ]
    edges = [
        {"id": "e1", "source": "msg-1", "target": "end-1", "data": {"button_text": "09:00"}},
        {"id": "e2", "source": "msg-1", "target": "end-2", "data": {"button_text": "14:00"}},
    ]

    result = _validate(nodes, edges)

    assert not any(v.rule_id in ("SESSION_001", "STRUCT_010") for v in result.violations)


@pytest.mark.django_db
def test_team_based_handoff_routing_is_flagged_at_authoring():
    """There is no team routing, so saying so beats discovering it live."""
    nodes = [
        {
            "id": "handoff-1",
            "type": "handoff",
            "position": {"x": 0, "y": 0},
            "data": {"label": "To the sales team", "assignment_type": "team", "team_id": 7},
        }
    ]

    result = _validate(nodes)

    handoff_warnings = [v for v in result.warnings if v.rule_id == "HANDOFF_003"]
    assert any("not implemented" in v.message for v in handoff_warnings)
