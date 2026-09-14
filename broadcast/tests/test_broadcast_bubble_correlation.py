"""Telling one broadcast bubble from another, and failing one out loud (#658).

Both halves of this file are the same defect: the inbox could not say *which
send* a bubble belonged to.

**Correlation.** The create endpoint answers 201 long before anything is sent,
so the web app draws a pending bubble per recipient and waits for the inbox row
to catch up. It had nothing to match on but the rendered body text — which for
two identical templates to the same contact is the same string — so the oldest
pending bubble won, and every delivery state landed on the wrong one. The rows
now carry ``content["_meta"]`` naming the ``BroadcastMessage`` that produced
them.

**Failure.** If the send then failed, there was no row and no event: nothing
ever moved that bubble out of pending. A terminal failure now produces both,
the way every other outbound failure in this codebase already does.

The tests below run the real batch task against a stubbed Graph — the send,
the adapter, the status transitions and the WebSocket group are all
production code — and assert consequences: two identical sends are told apart,
and a failure that never reaches Meta still reaches the inbox.

HOW TO RUN:
    python -m pytest broadcast/tests/test_broadcast_bubble_correlation.py -v
"""

from __future__ import annotations

import itertools

import pytest
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.utils import timezone

from wa.tests.meta_path import (
    FakeGraph,
    FakeResponse,
    meta_wa_app,
    tenant,
    wa_template,
)

pytestmark = pytest.mark.django_db

TOKEN = "bubble-tenant-token"

_phone = itertools.count(1)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_cache():
    """The pacing window and cooldowns live in the cache, which outlives a test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _no_global_token(settings):
    """No deployment-wide token, so a send that resolves the wrong app fails
    loudly instead of quietly borrowing credentials."""
    settings.META_PERM_TOKEN = ""


@pytest.fixture()
def app():
    return meta_wa_app(tenant("Bubbles"), access_token=TOKEN)


@pytest.fixture()
def graph(monkeypatch):
    return FakeGraph().install(monkeypatch)


class _Socket:
    """A team-inbox WebSocket client, minus the WebSocket.

    Joins the same channel-layer group ``TeamInboxConsumer`` joins, so what
    lands here is exactly what a connected agent's browser would be sent.
    """

    def __init__(self, layer, tenant_id):
        self.layer = layer
        self.channel = "bubble-test-listener"
        async_to_sync(layer.group_add)(f"team_inbox_{tenant_id}", self.channel)

    def received(self, message_type: str) -> list:
        """Every message of *message_type* delivered so far, oldest first."""
        queue = self.layer.channels.get(self.channel)
        out = []
        while queue is not None and not queue.empty():
            _expiry, envelope = queue.get_nowait()
            out.append(envelope)
        self._seen = getattr(self, "_seen", []) + out
        return [
            e
            for e in self._seen
            if e.get("type") == message_type or (e.get("message") or {}).get("type") == message_type
        ]


@pytest.fixture()
def socket(settings, app):
    """A listener on this tenant's team-inbox group.

    An in-memory channel layer rather than Redis: the assertion is about what
    the sender puts on the group, and an in-memory layer lets the test read it
    without a broker.
    """
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

    from channels.layers import get_channel_layer

    return _Socket(get_channel_layer(), app.tenant.pk)


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _contact(app, name="Repeat"):
    from contacts.models import TenantContact

    n = next(_phone)
    return TenantContact.objects.create(tenant=app.tenant, phone=f"+2782{n:07d}", first_name=f"{name}{n}")


def _broadcast(app, template, name="Campaign"):
    from broadcast.models import Broadcast, BroadcastPlatformChoices, BroadcastStatusChoices

    return Broadcast.objects.create(
        tenant=app.tenant,
        name=name,
        platform=BroadcastPlatformChoices.WHATSAPP,
        status=BroadcastStatusChoices.SENDING,
        template_number=template.number,
        scheduled_time=timezone.now(),
    )


def _message(broadcast, contact):
    from broadcast.models import BroadcastMessage, MessageStatusChoices

    return BroadcastMessage.objects.create(
        broadcast=broadcast,
        contact=contact,
        status=MessageStatusChoices.PENDING,
    )


def _accepted(wamid: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "27821234567", "wa_id": "27821234567"}],
        "messages": [{"id": wamid, "message_status": "accepted"}],
    }


def _rejected(message: str = "(#132001) Template name does not exist in the translation") -> FakeResponse:
    """What Graph returns for a template that cannot be sent — terminal, not
    transient, so the batch loop fails the row rather than re-queueing it."""
    return FakeResponse({"error": {"message": message, "code": 132001}}, status_code=400)


def _run(message_ids):
    from broadcast import tasks

    return tasks.process_broadcast_messages_batch(list(message_ids))


def _inbox_rows(contact):
    from team_inbox.models import Messages

    return list(Messages.objects.filter(contact=contact).order_by("pk"))


def _meta(row) -> dict:
    return (row.content or {}).get("_meta") or {}


# ─────────────────────────────────────────────────────────────────────────────
# One bubble per send, even when the bubbles are identical
# ─────────────────────────────────────────────────────────────────────────────


def test_the_same_template_sent_twice_to_one_contact_produces_two_tellable_rows(app, graph):
    """The headline. Two sends, one template, one contact — byte-identical
    bodies — and the rows can still be told apart.

    This is the case the body-text match cannot do: the assertion deliberately
    proves the bodies *are* identical first, so that the rows being
    distinguishable cannot be an accident of differing text.
    """
    template = wa_template(app, element_name="loyalty_reminder")
    contact = _contact(app)

    graph.post(f"/{app.phone_number_id}/messages", lambda call: _accepted(f"wamid.{len(graph.calls)}"))

    first = _message(_broadcast(app, template, "Morning send"), contact)
    _run([first.id])
    second = _message(_broadcast(app, template, "Evening send"), contact)
    _run([second.id])

    rows = _inbox_rows(contact)
    assert len(rows) == 2, "one inbox row per send"
    assert rows[0].content["body"] == rows[1].content["body"], (
        "the premise: the rendered bodies are identical, so body text cannot correlate"
    )

    assert _meta(rows[0]).get("broadcast_message_id") == first.pk
    assert _meta(rows[1]).get("broadcast_message_id") == second.pk
    assert _meta(rows[0]).get("broadcast_id") == first.broadcast_id
    assert _meta(rows[1]).get("broadcast_id") == second.broadcast_id
    assert _meta(rows[0]).get("contact_id") == _meta(rows[1]).get("contact_id") == contact.pk


def test_each_send_resolves_to_its_own_row_from_either_direction(app, graph):
    """Both lookups a client and the server use agree on the pairing.

    ``broadcast_message_id`` is the canonical handle; ``(broadcast_id,
    contact_id)`` is the same identity spelled in what the client already
    holds at 201 time, before any ``BroadcastMessage`` row exists to be named.
    Neither half of that pair identifies a bubble alone, which is why both are
    stamped — and the two broadcasts here share a contact, the two rows share
    a broadcast-shaped body, so a lookup that dropped either half would return
    the wrong row.
    """
    from team_inbox.utils.inbox_message_factory import find_inbox_message_for_broadcast

    template = wa_template(app)
    contact = _contact(app)
    graph.post(f"/{app.phone_number_id}/messages", lambda call: _accepted(f"wamid.{len(graph.calls)}"))

    first = _message(_broadcast(app, template, "A"), contact)
    second = _message(_broadcast(app, template, "B"), contact)
    _run([first.id])
    _run([second.id])

    assert find_inbox_message_for_broadcast(first).pk != find_inbox_message_for_broadcast(second).pk

    for sent in (first, second):
        row = find_inbox_message_for_broadcast(sent)
        assert _meta(row).get("broadcast_message_id") == sent.pk
        assert (_meta(row).get("broadcast_id"), _meta(row).get("contact_id")) == (
            sent.broadcast_id,
            sent.contact_id,
        )


def test_a_failure_lands_on_the_send_that_failed_not_on_the_oldest_bubble(app, graph):
    """The defect's actual consequence, reproduced end to end.

    The same template goes to the same contact twice; the first is accepted
    and the second rejected. Correlating on body text hands the failure to
    whichever pending bubble is oldest — the successful one. The rows here
    each carry their own send's state: the *second* row is the failed one, and
    the first is still fine.
    """
    from broadcast.models import MessageStatusChoices

    template = wa_template(app)
    contact = _contact(app)

    graph.post(f"/{app.phone_number_id}/messages", _accepted("wamid.accepted"))
    accepted = _message(_broadcast(app, template, "First"), contact)
    _run([accepted.id])

    graph.reset_routes()
    graph.post(f"/{app.phone_number_id}/messages", _rejected())
    rejected = _message(_broadcast(app, template, "Second"), contact)
    _run([rejected.id])

    accepted.refresh_from_db()
    rejected.refresh_from_db()
    assert accepted.status == MessageStatusChoices.SENT
    assert rejected.status == MessageStatusChoices.FAILED

    rows = _inbox_rows(contact)
    assert len(rows) == 2
    named = [_meta(r).get("broadcast_message_id") for r in rows]
    assert sorted(named) == sorted([accepted.pk, rejected.pk]), "each row names the send that produced it"
    by_send = dict(zip(named, rows))

    assert by_send[accepted.pk].outgoing_status == MessageStatusChoices.SENT
    assert by_send[rejected.pk].outgoing_status == MessageStatusChoices.FAILED
    assert by_send[accepted.pk].outgoing_failed_at is None, "the accepted send did not fail"
    assert by_send[rejected.pk].outgoing_failed_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# A failure after the 201 reaches the inbox
# ─────────────────────────────────────────────────────────────────────────────


def test_a_send_that_never_reaches_meta_still_produces_an_inbox_row(app, graph):
    """No provider message id, no delivery report — and still a bubble.

    Before this, a broadcast that failed here produced nothing at all: the
    contact's timeline was empty and the client's optimistic bubble had no
    state to move to. The row is the durable half, the one a client that was
    not connected finds on reload.
    """
    from broadcast.models import MessageStatusChoices

    graph.post(f"/{app.phone_number_id}/messages", _rejected())
    contact = _contact(app)
    message = _message(_broadcast(app, wa_template(app)), contact)

    result = _run([message.id])

    assert result["failed"] == 1
    rows = _inbox_rows(contact)
    assert len(rows) == 1, "a failed send is one bubble, not zero and not two"

    row = rows[0]
    assert _meta(row).get("broadcast_message_id") == message.pk
    assert row.external_message_id in ("", None), "nothing was accepted, so there is no provider id"
    assert row.outgoing_status == MessageStatusChoices.FAILED
    assert row.outgoing_failed_at is not None
    assert "132001" in (row.outgoing_error or ""), "the failed bubble carries Meta's own wording"


def test_a_failed_send_is_announced_on_the_team_inbox_group(app, graph, socket):
    """The live half: a connected client is told, and told which bubble.

    ``message_status_update`` is the event the status webhook already uses for
    provider-reported transitions, so this is the same mechanism rather than a
    second one — the difference is that this failure has no provider id, and
    the broadcast identifiers are what names the bubble instead.
    """
    from broadcast.models import MessageStatusChoices

    graph.post(f"/{app.phone_number_id}/messages", _rejected())
    contact = _contact(app)
    message = _message(_broadcast(app, wa_template(app)), contact)

    _run([message.id])

    updates = socket.received("message_status_update")
    assert len(updates) == 1, "exactly one failure announcement for one failed send"

    event = updates[0]
    assert event["status"] == MessageStatusChoices.FAILED
    assert event["outgoing_status"] == MessageStatusChoices.FAILED
    assert event["broadcast_message_id"] == message.pk
    assert event.get("broadcast_id") == message.broadcast_id
    assert event["contact_id"] == contact.pk
    assert event["external_message_id"] in ("", None)
    assert event.get("id") == _inbox_rows(contact)[0].pk, "the event names the row it just created"
    assert "132001" in (event.get("error") or "")
    assert event["failed_at"] is not None


def test_the_new_bubble_itself_carries_the_identifiers(app, graph, socket):
    """The ``new_message`` a client receives is enough on its own.

    A client need not wait for the status event to place the bubble: the row
    broadcast by ``team_inbox.signals`` carries the same ``_meta``, so the
    optimistic bubble can be replaced the moment the row exists.
    """
    graph.post(f"/{app.phone_number_id}/messages", _rejected())
    contact = _contact(app)
    message = _message(_broadcast(app, wa_template(app)), contact)

    _run([message.id])

    new_messages = socket.received("new_message")
    assert len(new_messages) == 1
    payload = new_messages[0]["message"]["message"]
    stamped = (payload["content"] or {}).get("_meta") or {}
    assert stamped.get("broadcast_message_id") == message.pk
    assert stamped.get("broadcast_id") == message.broadcast_id
    assert payload["outgoing_status"] == "FAILED"


def test_a_processing_error_is_reported_the_same_way(app, graph, socket, monkeypatch):
    """The other terminal failure: the send blew up rather than being refused.

    A broadcast that dies inside the batch loop leaves the same silent pending
    bubble as one Meta refuses, so it takes the same path out.
    """
    from broadcast import tasks
    from broadcast.models import MessageStatusChoices

    contact = _contact(app)
    message = _message(_broadcast(app, wa_template(app)), contact)

    def _explode(_message):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(tasks, "route_to_platform_handler", _explode)

    _run([message.id])

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.FAILED
    assert len(_inbox_rows(contact)) == 1
    updates = socket.received("message_status_update")
    assert len(updates) == 1
    assert updates[0]["broadcast_message_id"] == message.pk
    assert "adapter exploded" in (updates[0].get("error") or "")


def test_a_transient_failure_draws_no_failed_bubble(app, graph, socket):
    """A 429 is "not now", not "not ever" (#271).

    The row goes back to PENDING for another attempt, so announcing a failure
    here would put a permanent error on a bubble that is about to send.
    """
    from broadcast.models import MessageStatusChoices

    graph.post(
        f"/{app.phone_number_id}/messages",
        FakeResponse({"error": {"message": "Too many requests", "code": 130429}}, status_code=429),
    )
    contact = _contact(app)
    message = _message(_broadcast(app, wa_template(app)), contact)

    _run([message.id])

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.PENDING, "still retryable"
    assert message.failed_at is None
    assert _inbox_rows(contact) == []
    assert socket.received("message_status_update") == []


def test_re_processing_a_failed_send_does_not_draw_a_second_bubble(app, graph):
    """One send, one bubble, however many times the sweep touches it.

    A FAILED row is not in ``ALREADY_SENT_STATUSES``, so a re-run of the batch
    attempts it again; if it fails again, the bubble it already has must be
    reused rather than duplicated.
    """
    graph.post(f"/{app.phone_number_id}/messages", _rejected())
    contact = _contact(app)
    message = _message(_broadcast(app, wa_template(app)), contact)

    _run([message.id])
    _run([message.id])

    assert len(_inbox_rows(contact)) == 1
