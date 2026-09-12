"""chat_flow, Meta path: an inbound WhatsApp message spawns a flow (#277, #270).

The trigger subsystem shipped inert on WhatsApp and every component passed
its own tests. The emission site in ``wa.tasks`` handed the trigger a
``{"text": …}`` dict where a string was required; the trigger's own tests
build a ``TriggerEvent`` by hand, with a string, and the dispatcher's tests
build one too. Nothing ran the two together, so nothing noticed that the
subsystem had never fired.

This crosses the join, from the outside in:

    signed POST /wa/v2/webhooks/meta/
        → MetaWebhookView → WAWebhookEvent → process_message_webhook
            → _ingest_inbound_message → team_inbox.Messages (persisted)
                → emit(TriggerEvent(...))        ← the site #270 broke
                    → dispatch → InboundKeywordMatch.matches()
                        → start_chatflow_session_task

The event under assertion is the one the production code built from a real
Meta payload. The epic names asserting on a hand-built event as the blind
spot that let #270 ship, so the event is never constructed here — it is
captured at the far side of the emission site and checked against the row
the same run persisted.

HOW TO RUN:
    python -m pytest chat_flow/test_meta_path_trigger_dispatch.py -v
"""

from __future__ import annotations

import uuid

import pytest

from wa.tests.meta_path import (
    APP_SECRET,
    FakeGraph,
    inbound_envelope,
    messages_value,
    meta_wa_app,
    run_webhooks_in_process,
    sign_meta_webhook,
    tenant,
    text_message,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def app():
    return meta_wa_app(tenant("Flow"), access_token="flow-tenant-token")


@pytest.fixture(autouse=True)
def _meta_env(settings, tmp_path):
    settings.META_APP_SECRET = APP_SECRET
    settings.META_PERM_TOKEN = ""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    run_webhooks_in_process(settings)


@pytest.fixture()
def graph(monkeypatch):
    """Installed on every test so an unexpected Graph call fails loudly."""
    return FakeGraph().install(monkeypatch)


@pytest.fixture()
def spawns(monkeypatch):
    """Capture what the dispatcher queues, without running the flow.

    Patched at ``chat_flow.tasks.start_chatflow_session_task.delay`` — the far
    side of the boundary under test. Everything up to and including
    ``dispatch`` is the real code: the real emission site, the real registry,
    the real trigger, matched against real ``ChatFlow`` rows.
    """
    from chat_flow.tasks import start_chatflow_session_task

    calls: list[dict] = []
    monkeypatch.setattr(start_chatflow_session_task, "delay", lambda **kw: calls.append(kw))
    return calls


def _flow(owner, *, triggers, is_active=True, name="Keyword flow"):
    from chat_flow.models import ChatFlow

    return ChatFlow.objects.create(
        tenant=owner,
        name=f"{name} {uuid.uuid4().hex[:6]}",
        flow_data={"nodes": [], "edges": []},
        triggers=triggers,
        is_active=is_active,
    )


def _keyword_trigger(*keywords, channel=None, case_sensitive=False):
    config: dict = {"keywords": list(keywords), "case_sensitive": case_sensitive}
    if channel is not None:
        config["channel"] = channel
    return [{"type": "inbound_keyword_match", "config": config}]


def _inbox_row(app):
    from team_inbox.models import Messages

    rows = list(Messages.objects.filter(tenant=app.tenant))
    assert len(rows) == 1, f"expected one inbox row, got {len(rows)}"
    return rows[0]


def _deliver(client, app, body: str, *, wamid: str = "wamid.kw1"):
    return sign_meta_webhook(client, inbound_envelope(app, messages_value(app, text_message(wamid, body))))


# ─────────────────────────────────────────────────────────────────────────────
# The headline: the subsystem fires at all
# ─────────────────────────────────────────────────────────────────────────────


def test_a_keyword_in_a_real_meta_inbound_spawns_the_flow(client, app, graph, spawns):
    """One POST in, one session spawn out.

    Before #270 this produced nothing: the dict ``body_text`` cleared the
    trigger's own empty-check, then either raised inside a deliberately broad
    handler or tested membership against the dict's keys. Either way zero
    flows matched and no error surfaced, which is why the subsystem could
    ship having never fired.
    """
    flow = _flow(app.tenant, triggers=_keyword_trigger("sales", "support"))

    response = _deliver(client, app, "I need SUPPORT with my order")

    assert response.status_code == 200
    assert len(spawns) == 1, "the inbound matched no flow"
    assert spawns[0]["chatflow_id"] == str(flow.id)


def test_the_event_carries_the_row_the_same_run_persisted(client, app, graph, spawns):
    """``inbound_row_id`` is the real ``Messages`` pk, and ``body_text`` the
    real message body — both read back off the database, not reconstructed.

    This is the assertion a hand-built event cannot make. #270 was precisely
    a mismatch between what the emission site put in this field and what the
    trigger expected to find, so the field's *provenance* is the test.
    """
    from contacts.models import TenantContact

    _flow(app.tenant, triggers=_keyword_trigger("refund"))

    _deliver(client, app, "can I get a refund please")

    row = _inbox_row(app)
    contact = TenantContact.objects.get(tenant=app.tenant)
    spawn = spawns[0]
    event = spawn["context"]["trigger_event"]

    assert spawn["contact_id"] == contact.id
    assert event["inbound_row_id"] == str(row.pk)
    assert event["inbound_row_model"] == "team_inbox.Messages"
    assert event["body_text"] == "can I get a refund please"
    assert event["channel"] == "wa"
    assert event["tenant_id"] == app.tenant.id
    assert event["extra"]["external_message_id"] == "wamid.kw1"


def test_body_text_reaches_the_trigger_as_a_string(client, app, graph, spawns):
    """The exact type #270 got wrong, asserted on the event as emitted.

    ``TriggerEvent.__post_init__`` now refuses a non-string, and emitters wrap
    ``emit()`` in try/except so ingestion cannot break — which means the dict
    would be converted into a logged error and a silent non-match, with the
    inbox row still written. Only an assertion on the value that crossed the
    boundary distinguishes that from a working dispatch.
    """
    _flow(app.tenant, triggers=_keyword_trigger("hello"))

    _deliver(client, app, "hello there")

    body_text = spawns[0]["context"]["trigger_event"]["body_text"]
    assert isinstance(body_text, str), f"body_text crossed the boundary as {type(body_text).__name__}"
    assert body_text == "hello there"


def test_a_button_reply_matches_on_the_button_title(client, app, graph, spawns):
    """An interactive reply is text as far as a keyword trigger is concerned.

    ``_build_team_inbox_content`` files a button reply under
    ``content["body"]["text"]`` but types it ``button_reply``, so the text a
    trigger should match against is not in the same place for every message
    type — the reason ``_trigger_body_text`` exists at all.
    """
    _flow(app.tenant, triggers=_keyword_trigger("agent"))

    payload = inbound_envelope(
        app,
        messages_value(
            app,
            {
                "id": "wamid.btn",
                "from": "27821234567",
                "timestamp": "1789000000",
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": "btn-1", "title": "Talk to an agent"},
                },
            },
        ),
    )
    sign_meta_webhook(client, payload)

    assert len(spawns) == 1
    assert spawns[0]["context"]["trigger_event"]["body_text"] == "Talk to an agent"


def test_an_image_caption_is_what_the_customer_wrote(client, app, graph, spawns):
    """Media types put the words in the media object's caption, not in
    ``body``. A trigger reading ``body`` would see an empty string here and
    never match a photo sent with a complaint attached."""
    from wa.tests.meta_path import FakeResponse, image_message

    graph.get("/media-77", {"url": "https://lookaside.fbsbx.com/x?mid=media-77", "mime_type": "image/jpeg"})
    graph.get("lookaside.fbsbx.com", FakeResponse(content=b"\xff\xd8jpeg"))

    _flow(app.tenant, triggers=_keyword_trigger("broken"))

    payload = inbound_envelope(
        app,
        messages_value(app, image_message("wamid.img", "media-77", caption="my screen is broken")),
    )
    sign_meta_webhook(client, payload)

    assert len(spawns) == 1
    assert spawns[0]["context"]["trigger_event"]["body_text"] == "my screen is broken"


# ─────────────────────────────────────────────────────────────────────────────
# Selectivity — a subsystem that fires for everything is no better
# ─────────────────────────────────────────────────────────────────────────────


def test_a_non_matching_message_spawns_nothing(client, app, graph, spawns):
    _flow(app.tenant, triggers=_keyword_trigger("refund"))

    _deliver(client, app, "just saying hi")

    assert spawns == []


def test_an_inactive_flow_is_not_spawned(client, app, graph, spawns):
    _flow(app.tenant, triggers=_keyword_trigger("help"), is_active=False)

    _deliver(client, app, "help please")

    assert spawns == []


def test_another_tenants_flow_is_not_spawned(client, app, graph, spawns):
    """Tenant isolation across the whole path, not just in the dispatcher.

    ``tenant_id`` on the event comes from ``instance.wa_app.tenant`` — the app
    the webhook view routed to. A mis-routed delivery would spawn another
    customer's flow, which is why this is worth asserting from the webhook
    rather than from a constructed event.
    """
    other = tenant("Other")
    _flow(other, triggers=_keyword_trigger("help"))
    mine = _flow(app.tenant, triggers=_keyword_trigger("help"))

    _deliver(client, app, "help please")

    assert [s["chatflow_id"] for s in spawns] == [str(mine.id)]


def test_every_message_in_a_batch_gets_its_own_dispatch(client, app, graph, spawns):
    """#268 and #270 compound: a batch that was collapsing to one message also
    collapsed to one dispatch, so a flow keyed on the second customer message
    in a burst never fired even once the trigger itself worked."""
    _flow(app.tenant, triggers=_keyword_trigger("order"))

    payload = inbound_envelope(
        app,
        messages_value(
            app,
            text_message("wamid.b1", "my order is late"),
            text_message("wamid.b2", "nothing to match here"),
            text_message("wamid.b3", "order number 4471"),
        ),
    )
    sign_meta_webhook(client, payload)

    assert len(spawns) == 2, "each matching message in the batch must dispatch once"
    assert sorted(s["context"]["trigger_event"]["extra"]["external_message_id"] for s in spawns) == [
        "wamid.b1",
        "wamid.b3",
    ]
    # Each dispatch names its own inbox row, so a support engineer can tell
    # which of the three messages spawned which session.
    assert len({s["context"]["trigger_event"]["inbound_row_id"] for s in spawns}) == 2


# ─────────────────────────────────────────────────────────────────────────────
# All the way to a persisted session
# ─────────────────────────────────────────────────────────────────────────────


def test_the_spawned_session_is_persisted_with_its_trigger_context(client, app, graph, monkeypatch):
    """The far end of the path: a ``UserChatFlowSession`` row in the database.

    The spawn task is left unpatched here, so ``start_chatflow_session_task``
    really runs, really builds the flow's graph and really persists the
    session. Only ``send_template_message`` is stubbed — the outbound half is
    the broadcast area's test, and running it here would make this test fail
    for billing reasons rather than trigger reasons.

    The trigger context is asserted *on the row* because that JSON is the only
    audit trail the dispatcher leaves: "which inbound message started this
    session" is answerable from the database or not at all.
    """
    from chat_flow.models import ChatFlowEdge, ChatFlowNode, UserChatFlowSession
    from chat_flow.services import graph_executor
    from wa.tests.meta_path import wa_template

    flow = _flow(app.tenant, triggers=_keyword_trigger("start"))
    template = wa_template(app, element_name="flow_welcome")
    start = ChatFlowNode.objects.create(
        flow=flow, node_id="start-1", node_type="start", node_data={}, position_x=0, position_y=0
    )
    greet = ChatFlowNode.objects.create(
        flow=flow,
        node_id="greet-1",
        node_type="template",
        template=template,
        node_data={},
        position_x=200,
        position_y=0,
    )
    ChatFlowEdge.objects.create(flow=flow, source_node=start, target_node=greet, button_text="__PASSTHROUGH__")

    sent: list[dict] = []
    monkeypatch.setattr(
        graph_executor,
        "send_template_message",
        lambda **kw: sent.append(kw) or {"success": True, "status": "scheduled"},
    )

    _deliver(client, app, "start please")

    row = _inbox_row(app)
    session = UserChatFlowSession.objects.get(flow=flow)
    assert session.is_active is True
    assert session.current_node_id == "greet-1"
    assert session.tenant_id == app.tenant.id

    event = session.context_data["context"]["trigger_event"]
    assert event["inbound_row_id"] == str(row.pk)
    assert event["body_text"] == "start please"
    assert event["channel"] == "wa"

    assert [c["template_id"] for c in sent] == [template.id], "the flow's first template was not sent"


# ─────────────────────────────────────────────────────────────────────────────
# Finding: the replay guard does not survive a redelivered webhook
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING, not covered by any ticket in #277. The dispatcher documents "
        "itself as 'idempotent across webhook replays (Redis SETNX keyed on "
        "(tenant, channel, inbound_row_id))', but `inbound_row_id` is the pk of "
        "the `team_inbox.Messages` row this run just created. Inbound ingestion "
        "has no dedup of its own (see the sibling finding in "
        "team_inbox/tests/test_meta_path_inbound.py), so a redelivered Meta "
        "webhook creates a second row, produces a second key, claims it and "
        "spawns the flow again. A key derived from the provider's `wamid` — "
        "already carried in `extra.external_message_id` — would survive the "
        "replay; the row pk cannot. Note this test does not exercise the Redis "
        "claim itself: under the test cache backend SETNX is unsupported and "
        "`claim_dispatch` fails open, so it demonstrates the duplicate "
        "dispatch, not the key comparison. Strict xfail so a fix flips it."
    ),
)
def test_a_redelivered_webhook_does_not_spawn_the_flow_twice(client, app, graph, spawns):
    _flow(app.tenant, triggers=_keyword_trigger("help"))

    payload = inbound_envelope(app, messages_value(app, text_message("wamid.replay", "help me")))
    sign_meta_webhook(client, payload)
    sign_meta_webhook(client, payload)

    assert len(spawns) == 1, f"the same wamid spawned {len(spawns)} sessions"
