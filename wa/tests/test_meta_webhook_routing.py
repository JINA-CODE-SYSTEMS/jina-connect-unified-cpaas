"""META webhook routing precedence (#309).

The view matched ``waba_id`` first and took ``.first()``:

    wa_app = meta_apps.filter(waba_id=waba_id).first()
    if wa_app is None and phone_number_id:
        wa_app = meta_apps.filter(phone_number_id=phone_number_id).first()

One ``TenantWAApp`` holds one number, so a tenant with several numbers holds
several rows — and those rows may share a ``waba_id``. ``waba_id`` is not a
unique routing key, and the ``phone_number_id`` fallback could never correct a
wrong WABA match because it was guarded on that match having failed. Every
event for every number on a shared WABA was filed against whichever row the
database returned first: inbound messages against the wrong number, delivery
statuses applied to the wrong app, nothing reporting the mismatch.

The test that matters is the second-number one: it fails on the old code for
the right reason, and a "the happy path still works" test does not.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_meta_webhook_routing.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest

URL = "/wa/v2/webhooks/meta/"
SECRET = "routing-app-secret-never-logged"

SHARED_WABA = "waba-shared-1"
PHONE_A = "pn-first-number"
PHONE_B = "pn-second-number"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _message_payload(waba_id: str, phone_number_id: str | None) -> dict:
    value: dict = {
        "messaging_product": "whatsapp",
        "messages": [
            {
                "id": f"wamid.{uuid.uuid4().hex[:8]}",
                "from": "919000000002",
                "timestamp": "1700000000",
                "type": "text",
                "text": {"body": "hi"},
            }
        ],
    }
    if phone_number_id is not None:
        value["metadata"] = {"display_phone_number": "919000000099", "phone_number_id": phone_number_id}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": waba_id, "changes": [{"field": "messages", "value": value}]}],
    }


def _account_payload(waba_id: str) -> dict:
    """An account-level update. These legitimately carry no phone_number_id."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
                "changes": [
                    {
                        "field": "account_update",
                        "value": {"event": "PARTNER_ADDED", "ban_info": {}},
                    }
                ],
            }
        ],
    }


def _post(client, payload: dict):
    raw = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return client.post(URL, data=raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)


def _tenant():
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"RouteTenant-{uuid.uuid4().hex[:6]}", is_active=True)


def _wa_app(tenant, *, waba_id: str, phone_number_id: str | None):
    from wa.models import WAApp

    return WAApp.objects.create(
        tenant=tenant,
        app_name=f"app-{uuid.uuid4().hex[:6]}",
        app_id=f"a-{uuid.uuid4().hex[:6]}",
        app_secret="s",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=waba_id,
        phone_number_id=phone_number_id,
        bsp="META",
        bsp_credentials={"access_token": "tok"},
        is_active=True,
    )


class _Collector(logging.Handler):
    """``caplog`` cannot be used — the ``wa`` logger sets ``propagate: False``."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):  # noqa: D102
        self.lines.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@contextmanager
def _view_logs():
    handler = _Collector()
    view_logger = logging.getLogger("wa.views")
    previous_level = view_logger.level
    view_logger.addHandler(handler)
    view_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        view_logger.removeHandler(handler)
        view_logger.setLevel(previous_level)


@pytest.fixture(autouse=True)
def _signed_and_undispatched(settings):
    """Signatures are #306's subject; here they only have to be valid."""
    settings.META_APP_SECRET = SECRET
    settings.META_WEBHOOK_ALLOW_UNSIGNED = False
    with patch("wa.signals._dispatch"):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# The regression: a shared WABA must not swallow the second number
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_event_for_the_second_number_routes_to_the_second_app(client):
    """#309's central case. ``app_a`` is created first, so it is the row the
    old ``waba_id``-first ``.first()`` returned for *both* numbers."""
    from wa.models import WAWebhookEvent

    tenant = _tenant()
    app_a = _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
    app_b = _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_B)

    response = _post(client, _message_payload(SHARED_WABA, PHONE_B))

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    event = WAWebhookEvent.objects.get(pk=response.json()["event_id"])
    assert event.wa_app_id == app_b.pk
    assert event.wa_app_id != app_a.pk, "the shared waba_id must not win over the number"


@pytest.mark.django_db
def test_an_event_for_the_first_number_still_routes_to_the_first_app(client):
    """Inverting the precedence must not simply move the error to the other row."""
    from wa.models import WAWebhookEvent

    tenant = _tenant()
    app_a = _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_B)

    response = _post(client, _message_payload(SHARED_WABA, PHONE_A))

    event = WAWebhookEvent.objects.get(pk=response.json()["event_id"])
    assert event.wa_app_id == app_a.pk


@pytest.mark.django_db
def test_each_number_on_a_shared_waba_gets_its_own_events(client):
    """Three numbers, three events, three apps — no crosstalk."""
    from wa.models import WAWebhookEvent

    tenant = _tenant()
    apps = {phone: _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=phone) for phone in ("p1", "p2", "p3")}

    for phone in apps:
        _post(client, _message_payload(SHARED_WABA, phone))

    for phone, app in apps.items():
        events = WAWebhookEvent.objects.filter(wa_app=app)
        assert events.count() == 1, f"{phone} should own exactly one event"
        assert events.get().payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] == phone


@pytest.mark.django_db
def test_the_number_wins_even_when_it_sits_on_a_different_waba(client):
    """``phone_number_id`` is the specific identifier, so it decides — even if
    the payload's ``waba_id`` happens to match some other app."""
    from wa.models import WAWebhookEvent

    tenant = _tenant()
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
    owner = _wa_app(tenant, waba_id="waba-other", phone_number_id=PHONE_B)

    response = _post(client, _message_payload(SHARED_WABA, PHONE_B))

    event = WAWebhookEvent.objects.get(pk=response.json()["event_id"])
    assert event.wa_app_id == owner.pk


# ─────────────────────────────────────────────────────────────────────────────
# The waba_id fallback still has to work
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_account_level_event_with_no_number_still_routes_by_waba_id(client):
    from wa.models import WAWebhookEvent

    app = _wa_app(_tenant(), waba_id="waba-solo-1", phone_number_id="pn-solo-1")

    response = _post(client, _account_payload("waba-solo-1"))

    assert response.json()["status"] == "received"
    assert response.json()["event_type"] == "ACCOUNT"
    event = WAWebhookEvent.objects.get(pk=response.json()["event_id"])
    assert event.wa_app_id == app.pk
    assert event.is_processed is False
    assert not event.error_message


@pytest.mark.django_db
def test_an_unknown_number_falls_back_to_an_unambiguous_waba(client):
    """One app on the WABA and a number we do not hold (not yet synced, or
    renumbered) is still attributable — the same latitude
    ``fetch_waba_info`` allows itself for a single-number WABA."""
    from wa.models import WAWebhookEvent

    app = _wa_app(_tenant(), waba_id="waba-solo-2", phone_number_id=None)

    response = _post(client, _message_payload("waba-solo-2", "pn-not-stored-yet"))

    event = WAWebhookEvent.objects.get(pk=response.json()["event_id"])
    assert event.wa_app_id == app.pk


@pytest.mark.django_db
def test_an_unknown_waba_and_number_is_still_an_unknown_app(client):
    from wa.models import WAWebhookEvent

    _wa_app(_tenant(), waba_id="waba-solo-3", phone_number_id="pn-solo-3")

    response = _post(client, _message_payload("waba-nobody-has-this", "pn-nobody-has-this"))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "unknown_app"}
    assert not WAWebhookEvent.objects.exists()


# ─────────────────────────────────────────────────────────────────────────────
# The ambiguous case: recorded, never attributed
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_ambiguous_waba_with_no_number_is_recorded_not_attributed(client):
    """Two apps share the WABA and the payload carries no number, so there is
    no non-arbitrary answer. The event is kept, but marked so nothing applies
    it to an app it may not belong to."""
    from wa.models import WAWebhookEvent

    tenant = _tenant()
    app_a = _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
    app_b = _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_B)

    response = _post(client, _account_payload(SHARED_WABA))

    assert response.status_code == 200
    assert response.json()["status"] == "recorded"
    assert response.json()["reason"] == "ambiguous_waba_id"

    event = WAWebhookEvent.objects.get(pk=response.json()["event_id"])
    # Kept in full, so nothing is lost.
    assert event.payload["entry"][0]["id"] == SHARED_WABA
    # Marked, so it is findable and not silently applied.
    assert event.is_processed is True
    assert "ambiguous" in event.error_message.lower()
    assert str(app_a.pk) in event.error_message
    assert str(app_b.pk) in event.error_message


@pytest.mark.django_db
def test_an_ambiguous_event_is_not_queued_for_processing(client):
    """``is_processed=True`` is what keeps the pipeline off it — the post_save
    receiver dispatches only unprocessed rows."""
    tenant = _tenant()
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_B)

    with patch("wa.signals._dispatch") as dispatch:
        _post(client, _account_payload(SHARED_WABA))

    assert dispatch.call_count == 0


@pytest.mark.django_db
def test_an_ambiguous_event_is_reported_at_error_level(client):
    """An operator has to be able to find these; 200 hides them from META."""
    tenant = _tenant()
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_B)

    with _view_logs() as logs:
        _post(client, _account_payload(SHARED_WABA))

    assert "ambiguous" in logs.text.lower()
    assert SHARED_WABA in logs.text


@pytest.mark.django_db
def test_ambiguity_is_resolved_as_soon_as_the_payload_names_a_number(client):
    """The ambiguity is a property of the payload, not of the apps."""
    from wa.models import WAWebhookEvent

    tenant = _tenant()
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
    app_b = _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_B)

    response = _post(client, _message_payload(SHARED_WABA, PHONE_B))

    assert response.json()["status"] == "received"
    event = WAWebhookEvent.objects.get(pk=response.json()["event_id"])
    assert event.wa_app_id == app_b.pk
    assert not event.error_message


# ─────────────────────────────────────────────────────────────────────────────
# Contract with META, and no leaks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["unknown_app", "ambiguous", "missing_waba_id"])
def test_meta_always_gets_a_200(client, case):
    tenant = _tenant()
    if case == "unknown_app":
        payload = _message_payload("waba-nobody", "pn-nobody")
    elif case == "ambiguous":
        _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
        _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_B)
        payload = _account_payload(SHARED_WABA)
    else:
        payload = {"object": "whatsapp_business_account", "entry": []}

    assert _post(client, payload).status_code == 200


@pytest.mark.django_db
def test_routing_logs_carry_no_secret(client):
    """Routing logs name ids, which is the point; they must not name keys."""
    tenant = _tenant()
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_A)
    _wa_app(tenant, waba_id=SHARED_WABA, phone_number_id=PHONE_B)

    with _view_logs() as logs:
        _post(client, _message_payload(SHARED_WABA, PHONE_B))
        _post(client, _account_payload(SHARED_WABA))
        _post(client, _message_payload("waba-nobody", "pn-nobody"))

    assert logs.lines
    assert SECRET not in logs.text
