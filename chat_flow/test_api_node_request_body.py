"""What an API node actually puts on the wire, and what it says it will.

Reported from the canvas: "can you check if all things work here — also
variables is tough to note from here what to put", and then "does the api
request support form and raw as it says?".

Four defects, all of the same family — the editor makes a promise the
executor does not keep:

1. **FORM was advertised and not implemented.** The modal offers JSON / FORM /
   RAW and faithfully saves ``api_body_type``, but the executor branched on
   ``"json"`` and sent everything else as ``data=<str>``. So FORM and RAW were
   the same code path: no URL-encoding, and no ``Content-Type`` header at all,
   because requests only sets one for ``json=`` or a dict ``data=``.

2. **A JSON body that does not parse was silently downgraded** to raw text and
   sent anyway, with a warning nobody reads. The endpoint gets a body it
   cannot parse and answers 400, which looks like the endpoint's fault.

3. **API_004 never parsed the body.** It checked ``isinstance(body, (dict,
   str, list))`` — a string always passes — so "body must be valid JSON" was
   never once tested. The rule listed in the sidebar did nothing.

4. **API_008 validated a key nothing writes.** It reads ``response_mapping``;
   the editor sends ``response_variables`` and the executor reads
   ``response_variables``. Every response-variable mapping was unvalidated.

And the question behind all of them: the body hint says "Use {{variable}} to
insert contact attributes" without ever saying which. The set was a closure
inside the executor, so nothing could list it. It is now one table that both
the executor and the ``variables`` endpoint read.

HOW TO RUN:
    python -m pytest chat_flow/test_api_node_request_body.py -v
"""

from __future__ import annotations

import itertools
from unittest.mock import patch

import pytest

from chat_flow.api_request_body import FORM_CONTENT_TYPE, build_request_body
from chat_flow.rules.api import APINodeBodyFormat, APINodeResponseMapping
from chat_flow.services.flow_variables import CONTACT_VARIABLES, build_placeholder_vars

_seq = itertools.count(1)


# ─────────────────────────────────────────────────────────────────────────────
# 1. FORM means form-encoded, RAW means verbatim
# ─────────────────────────────────────────────────────────────────────────────


def test_json_body_is_sent_as_json():
    built = build_request_body("json", '{"a": 1}')

    assert built.error is None
    assert built.kwargs == {"json": {"a": 1}}


def test_form_body_written_as_an_object_is_form_encoded():
    """A dict reaches requests as a dict, so requests URL-encodes it."""
    built = build_request_body("form", '{"name": "Ada", "plan": "pro"}')

    assert built.error is None
    assert built.kwargs == {"data": {"name": "Ada", "plan": "pro"}}
    assert built.headers["Content-Type"] == FORM_CONTENT_TYPE


def test_form_encoding_escapes_values_that_would_break_the_body():
    """The reason a dict matters: a substituted name can contain & or =.

    As a hand-written ``a=1&b=2`` string those characters silently split the
    body into extra fields. Handed to requests as a dict, they are escaped.
    """
    built = build_request_body("form", '{"note": "tea & biscuits", "eq": "a=b"}')

    assert built.kwargs["data"] == {"note": "tea & biscuits", "eq": "a=b"}


def test_form_body_already_url_encoded_is_kept_and_declared():
    built = build_request_body("form", "name=Ada&plan=pro")

    assert built.error is None
    assert built.kwargs == {"data": "name=Ada&plan=pro"}
    assert built.headers["Content-Type"] == FORM_CONTENT_TYPE


def test_raw_body_is_sent_verbatim_and_invents_no_content_type():
    built = build_request_body("raw", "<xml>hello</xml>")

    assert built.error is None
    assert built.kwargs == {"data": "<xml>hello</xml>"}
    assert built.headers == {}


def test_form_and_raw_are_not_the_same_thing():
    """The defect in one line: these two used to produce identical kwargs."""
    body = '{"a": 1}'

    assert build_request_body("form", body).kwargs != build_request_body("raw", body).kwargs


# ─────────────────────────────────────────────────────────────────────────────
# 2. A JSON body that does not parse is an error, not a raw-text fallback
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_json_body_is_an_error_not_a_downgrade():
    built = build_request_body("json", "{'a': 1}")  # single quotes: not JSON

    assert built.kwargs == {}
    assert built.error is not None
    assert "JSON" in built.error


def test_empty_body_builds_nothing():
    assert build_request_body("json", "").kwargs == {}
    assert build_request_body("json", "").error is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. The executor uses it — nothing is sent when the body cannot be built
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_executor_does_not_send_a_json_body_it_could_not_parse():
    from chat_flow.services.graph_executor import create_api_call_node_handler

    node, state = _api_node(api_body='{"name": "{{first_name}}"', api_body_type="json")
    handler = create_api_call_node_handler(node, [])

    with patch("chat_flow.services.graph_executor._requests") as requests_mock:
        with patch("chat_flow.services.graph_executor._send_api_error_email"):
            new_state = handler(state)

    requests_mock.request.assert_not_called()
    assert new_state["user_input"] == "status-0"


@pytest.mark.django_db
def test_executor_form_body_reaches_requests_as_a_dict():
    from chat_flow.services.graph_executor import create_api_call_node_handler

    node, state = _api_node(api_body='{"name": "{{first_name}}"}', api_body_type="form")
    handler = create_api_call_node_handler(node, [])

    with patch("chat_flow.services.graph_executor._requests") as requests_mock:
        requests_mock.request.return_value = _response(200, '{"ok": true}')
        requests_mock.RequestException = Exception
        handler(state)

    kwargs = requests_mock.request.call_args.kwargs
    assert kwargs["data"] == {"name": "Ada"}
    assert kwargs["headers"]["Content-Type"] == FORM_CONTENT_TYPE
    assert "json" not in kwargs


@pytest.mark.django_db
def test_a_content_type_the_operator_set_is_not_overwritten():
    from chat_flow.services.graph_executor import create_api_call_node_handler

    node, state = _api_node(
        api_body="a=1",
        api_body_type="form",
        api_headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
    )
    handler = create_api_call_node_handler(node, [])

    with patch("chat_flow.services.graph_executor._requests") as requests_mock:
        requests_mock.request.return_value = _response(200, "{}")
        requests_mock.RequestException = Exception
        handler(state)

    sent = requests_mock.request.call_args.kwargs["headers"]["Content-Type"]
    assert sent == "application/x-www-form-urlencoded; charset=utf-8"


# ─────────────────────────────────────────────────────────────────────────────
# 4. The rules check what the node actually stores
# ─────────────────────────────────────────────────────────────────────────────


def _body_violations(**data):
    node = {"id": "api-1", "type": "api", "data": {"api_method": "POST", **data}}
    return APINodeBodyFormat().validate_node(node, {"nodes": [node], "edges": []})


def test_api_004_flags_a_json_body_that_cannot_parse():
    violations = _body_violations(api_body_type="json", api_body='{"name": "{{first_name}}"')

    assert [v.rule_id for v in violations] == ["API_004"]
    assert "api-1" == violations[0].node_id


def test_api_004_accepts_placeholders_inside_a_valid_document():
    assert _body_violations(api_body_type="json", api_body='{"name": "{{first_name}}", "age": {{age}}}') == []


def test_api_004_does_not_parse_a_raw_body_as_json():
    assert _body_violations(api_body_type="raw", api_body="<xml>hello</xml>") == []


def _mapping_violations(**data):
    node = {"id": "api-1", "type": "api", "data": data}
    return APINodeResponseMapping().validate_node(node, {"nodes": [node], "edges": []})


def test_api_008_reads_the_key_the_editor_writes():
    """``response_variables`` is what the modal saves and the executor reads."""
    violations = _mapping_violations(response_variables=[{"json_path": "data.id", "variable_name": "order id"}])

    assert [v.rule_id for v in violations] == ["API_008"]
    assert "order id" in violations[0].message


def test_api_008_flags_a_mapping_the_executor_would_drop():
    """A path with no variable name is discarded in silence at run time."""
    violations = _mapping_violations(response_variables=[{"json_path": "data.id", "variable_name": ""}])

    assert [v.rule_id for v in violations] == ["API_008"]


def test_api_008_accepts_a_usable_mapping():
    assert _mapping_violations(response_variables=[{"json_path": "data.id", "variable_name": "order_id"}]) == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. The variable list has one home
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_published_variable_list_is_the_one_the_executor_substitutes():
    contact = _contact()
    resolved = build_placeholder_vars({"contact_id": contact.id, "context": {}})

    assert set(resolved) == {v.key for v in CONTACT_VARIABLES}


@pytest.mark.django_db
def test_flow_context_wins_over_the_contact_row():
    """A variable an earlier API node stored is not overwritten by the contact."""
    contact = _contact()
    resolved = build_placeholder_vars({"contact_id": contact.id, "context": {"first_name": "from the api"}})

    assert resolved["first_name"] == "from the api"


@pytest.mark.django_db
def test_variables_endpoint_publishes_every_variable_with_a_description(client_with_tenant):
    client, _tenant = client_with_tenant
    response = client.get("/chat-flow/flows/variables/")

    assert response.status_code == 200
    published = response.json()["contact"]
    assert [v["key"] for v in published] == [v.key for v in CONTACT_VARIABLES]
    assert all(v["description"] for v in published)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Condition nodes read that same table
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_condition_can_read_a_variable_the_api_node_could_always_read():
    """Condition nodes kept their own field map, and it had no ``full_name``.

    So ``{{full_name}}`` substituted correctly in an API body and compared
    against an empty string in a condition on the same canvas.
    """
    from chat_flow.services.graph_executor import create_condition_node_handler

    node, state = _condition_node(variable="full_name", value="Ada L")

    assert create_condition_node_handler(node)(state)["user_input"] == "IF TRUE"


@pytest.mark.django_db
def test_a_condition_still_matches_on_the_last_message():
    """``last_message`` comes from the session, not the contact row."""
    from chat_flow.services.graph_executor import create_condition_node_handler

    node, state = _condition_node(variable="last_message", value="yes")
    state["user_input"] = "yes"

    assert create_condition_node_handler(node)(state)["user_input"] == "IF TRUE"


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _condition_node(*, variable: str, value: str):
    from chat_flow.models import ChatFlow, ChatFlowNode
    from tenants.models import Tenant

    tenant = Tenant.objects.create(name=f"Org {next(_seq)}")
    flow = ChatFlow.objects.create(tenant=tenant, name=f"Flow {next(_seq)}", flow_data={"nodes": [], "edges": []})
    contact = _contact(tenant)
    node = ChatFlowNode.objects.create(
        flow=flow,
        node_id="cond-1",
        node_type="condition",
        position_x=0,
        position_y=0,
        node_data={
            "condition_groups": [
                {"logic": "and", "rules": [{"variable": variable, "operator": "equals", "value": value}]}
            ],
            "outer_logic": "or",
        },
    )
    state = {
        "flow_id": flow.id,
        "contact_id": contact.id,
        "current_node_id": "cond-1",
        "context": {},
        "messages_sent": [],
    }
    return node, state


def _response(status_code: int, text: str):
    import json as _json
    from unittest.mock import Mock

    response = Mock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = _json.loads(text)
    return response


def _contact(tenant=None, first_name="Ada"):
    from contacts.models import TenantContact
    from tenants.models import Tenant

    tenant = tenant or Tenant.objects.create(name=f"Org {next(_seq)}")
    return TenantContact.objects.create(
        tenant=tenant, phone=f"+2782{next(_seq):07d}", first_name=first_name, last_name="L"
    )


def _api_node(*, api_body: str, api_body_type: str, api_headers=None):
    from chat_flow.models import ChatFlow, ChatFlowNode
    from tenants.models import Tenant

    tenant = Tenant.objects.create(name=f"Org {next(_seq)}")
    flow = ChatFlow.objects.create(tenant=tenant, name=f"Flow {next(_seq)}", flow_data={"nodes": [], "edges": []})
    contact = _contact(tenant)
    node = ChatFlowNode.objects.create(
        flow=flow,
        node_id="api-1",
        node_type="api",
        position_x=0,
        position_y=0,
        node_data={
            "api_url": "https://example.test/hook",
            "api_method": "POST",
            "api_body": api_body,
            "api_body_type": api_body_type,
            "api_headers": api_headers or {},
            "api_response_codes": [200],
        },
    )
    state = {
        "flow_id": flow.id,
        "contact_id": contact.id,
        "current_node_id": "api-1",
        "context": {},
        "messages_sent": [],
    }
    return node, state


@pytest.fixture
def client_with_tenant(db):
    """An authenticated client for the tenant-scoped chat-flow endpoints."""
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIClient

    from tenants.models import Tenant, TenantRole, TenantUser

    tenant = Tenant.objects.create(name=f"Org {next(_seq)}")
    n = next(_seq)
    user = get_user_model().objects.create_user(
        username=f"owner{n}", email=f"owner{n}@example.test", password="pw"  # noqa: S106
    )
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, tenant
