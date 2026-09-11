"""Batched META inbound webhooks (#268).

`_parse_meta_message_payload` read `entry[0] / changes[0] / messages[0]`, while
Meta batches on all three levels. Every message but the first was discarded
silently — no error raised, the event still marked processed — so customer
messages simply never appeared in the inbox, and the loss got worse under load.

That it was an oversight rather than a policy is visible in the same module:
the *status* path has always iterated all three levels.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_batched_inbound.py -v
"""

from __future__ import annotations

import uuid

import pytest

from wa.tasks import _parse_meta_message_payload, _split_meta_message_payloads

WABA = "waba-1"


def _msg(mid: str, text: str) -> dict:
    return {
        "id": mid,
        "from": "27821234567",
        "timestamp": "1789000000",
        "type": "text",
        "text": {"body": text},
    }


def _value(*messages: dict) -> dict:
    return {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "27820000000", "phone_number_id": "pn-1"},
        "contacts": [{"wa_id": "27821234567", "profile": {"name": "Thandi"}}],
        "messages": list(messages),
    }


def _payload(*values: dict, entries: int = 1) -> dict:
    changes = [{"field": "messages", "value": v} for v in values]
    return {"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": changes}] * entries}


# ─────────────────────────────────────────────────────────────────────────────
# Splitting
# ─────────────────────────────────────────────────────────────────────────────


def test_several_messages_in_one_value_become_several_slices():
    """The exact shape that was losing data."""
    payload = _payload(_value(_msg("wamid.1", "one"), _msg("wamid.2", "two"), _msg("wamid.3", "three")))

    slices = _split_meta_message_payloads(payload)

    assert len(slices) == 3
    ids = [s["entry"][0]["changes"][0]["value"]["messages"][0]["id"] for s in slices]
    assert ids == ["wamid.1", "wamid.2", "wamid.3"]


def test_several_changes_are_all_read():
    payload = _payload(_value(_msg("wamid.1", "a")), _value(_msg("wamid.2", "b")))
    assert len(_split_meta_message_payloads(payload)) == 2


def test_several_entries_are_all_read():
    payload = _payload(_value(_msg("wamid.1", "a")), entries=2)
    assert len(_split_meta_message_payloads(payload)) == 2


def test_each_slice_carries_the_shared_context():
    """Meta sends contacts and metadata once per batch; every slice needs them."""
    payload = _payload(_value(_msg("wamid.1", "a"), _msg("wamid.2", "b")))

    for sliced in _split_meta_message_payloads(payload):
        value = sliced["entry"][0]["changes"][0]["value"]
        assert value["contacts"][0]["wa_id"] == "27821234567"
        assert value["metadata"]["phone_number_id"] == "pn-1"
        assert sliced["entry"][0]["id"] == WABA
        assert len(value["messages"]) == 1


def test_a_slice_does_not_alias_the_original_message_list():
    """Mutating a slice must not corrupt its siblings or the stored payload."""
    payload = _payload(_value(_msg("wamid.1", "a"), _msg("wamid.2", "b")))

    slices = _split_meta_message_payloads(payload)
    slices[0]["entry"][0]["changes"][0]["value"]["messages"].append(_msg("wamid.X", "injected"))

    assert len(slices[1]["entry"][0]["changes"][0]["value"]["messages"]) == 1
    assert len(payload["entry"][0]["changes"][0]["value"]["messages"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Nothing to split
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("empty", {}),
        ("no entry", {"object": "whatsapp_business_account"}),
        ("entry is None", {"entry": None}),
        ("no changes", {"entry": [{"id": WABA}]}),
        ("no messages key", {"entry": [{"id": WABA, "changes": [{"value": {"statuses": []}}]}]}),
        ("messages empty", {"entry": [{"id": WABA, "changes": [{"value": {"messages": []}}]}]}),
        ("messages not a list", {"entry": [{"id": WABA, "changes": [{"value": {"messages": "x"}}]}]}),
        ("junk entry", {"entry": [None, "nope", 7]}),
        ("junk change", {"entry": [{"id": WABA, "changes": [None, "nope"]}]}),
        ("value not a dict", {"entry": [{"id": WABA, "changes": [{"value": "nope"}]}]}),
    ],
)
def test_unsplittable_payloads_yield_nothing_rather_than_raising(label, payload):
    assert _split_meta_message_payloads(payload) == [], label


def test_good_messages_survive_a_junk_sibling_change():
    payload = {"entry": [{"id": WABA, "changes": [None, {"field": "messages", "value": _value(_msg("w.1", "a"))}]}]}
    assert len(_split_meta_message_payloads(payload)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Each slice is what the existing parser expects
# ─────────────────────────────────────────────────────────────────────────────


def test_every_slice_parses_to_its_own_message():
    """The point of slicing rather than changing the parser's contract."""
    payload = _payload(_value(_msg("wamid.1", "first"), _msg("wamid.2", "second")))

    parsed = [_parse_meta_message_payload(s) for s in _split_meta_message_payloads(payload)]

    assert [p["message_id"] for p in parsed] == ["wamid.1", "wamid.2"]
    assert [p["text"] for p in parsed] == ["first", "second"]
    for p in parsed:
        assert p["waba_id"] == WABA
        assert p["contact_phone"] == "+27821234567"
        assert p["contact_name"] == "Thandi"


# ─────────────────────────────────────────────────────────────────────────────
# End to end: a batched webhook creates every message
# ─────────────────────────────────────────────────────────────────────────────


def _wa_app():
    from tenants.models import Tenant
    from wa.models import WAApp

    tenant = Tenant.objects.create(name=f"BatchTenant-{uuid.uuid4().hex[:6]}", is_active=True)
    return WAApp.objects.create(
        tenant=tenant,
        app_name=f"app-{uuid.uuid4().hex[:6]}",
        app_id=f"app-{uuid.uuid4().hex[:6]}",
        app_secret="s",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=WABA,
        phone_number_id="pn-1",
        bsp="META",
        is_active=True,
    )


def _event(wa_app, payload):
    """Create the event without letting the post_save signal process it.

    ``wa/signals.py`` dispatches on create, so a row built normally is already
    ingested by the time the test calls the task — and calling it again would
    double every message. Creating with ``is_processed=True`` skips the signal
    (``if created and not instance.is_processed``) and leaves the test driving
    the code under change, deterministically, with no dependency on whether
    Celery happens to be eager.

    The signal's own gate is #269's subject and deliberately out of scope here.
    """
    from wa.models import WAWebhookEvent

    return WAWebhookEvent.objects.create(
        wa_app=wa_app,
        bsp="META",
        event_type="MESSAGE",
        payload=payload,
        is_processed=True,
    )


@pytest.mark.django_db
def test_a_three_message_webhook_creates_three_inbox_rows():
    """The regression. One row was created and two messages vanished."""
    from team_inbox.models import Messages
    from wa.tasks import process_message_webhook

    wa_app = _wa_app()
    event = _event(wa_app, _payload(_value(_msg("wamid.1", "one"), _msg("wamid.2", "two"), _msg("wamid.3", "three"))))

    process_message_webhook(str(event.pk))

    bodies = sorted(m.content["body"]["text"] for m in Messages.objects.filter(tenant=wa_app.tenant))
    assert bodies == ["one", "three", "two"]


@pytest.mark.django_db
def test_a_single_message_webhook_still_creates_exactly_one():
    """The common case must not regress into duplicates."""
    from team_inbox.models import Messages
    from wa.tasks import process_message_webhook

    wa_app = _wa_app()
    event = _event(wa_app, _payload(_value(_msg("wamid.1", "solo"))))

    process_message_webhook(str(event.pk))

    messages = Messages.objects.filter(tenant=wa_app.tenant)
    assert messages.count() == 1
    assert messages.first().content["body"]["text"] == "solo"


@pytest.mark.django_db
def test_one_unparseable_message_does_not_drop_its_siblings():
    """Previously irrelevant — there was only ever one message to lose."""
    from team_inbox.models import Messages
    from wa.tasks import process_message_webhook

    wa_app = _wa_app()
    broken = {"id": "wamid.bad"}  # no type, no from, no timestamp
    event = _event(wa_app, _payload(_value(_msg("wamid.1", "good"), broken, _msg("wamid.3", "also good"))))

    process_message_webhook(str(event.pk))

    bodies = sorted(m.content["body"]["text"] for m in Messages.objects.filter(tenant=wa_app.tenant))
    assert "good" in bodies and "also good" in bodies


@pytest.mark.django_db
def test_a_payload_with_no_messages_is_recorded_not_silently_dropped():
    from wa.models import WAWebhookEvent
    from wa.tasks import process_message_webhook

    wa_app = _wa_app()
    event = _event(wa_app, {"entry": [{"id": WABA, "changes": [{"value": {"messages": []}}]}]})

    process_message_webhook(str(event.pk))

    event = WAWebhookEvent.objects.get(pk=event.pk)
    assert event.is_processed is True
    assert "no messages" in (event.error_message or "")
