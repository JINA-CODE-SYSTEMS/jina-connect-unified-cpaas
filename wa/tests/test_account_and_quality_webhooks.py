"""Account and quality webhooks (#267, push half).

The pull side now reads tier and quality from Meta on demand. This is the
other half: the events that say *it just changed*.

Both were being thrown away. `account_update` was classified correctly and
then dropped, because the dispatch had no ACCOUNT branch and fell through to
"unknown event type". `phone_number_quality_update` and
`message_template_quality_update` were worse — not classified at all, so the
push channels reporting a tier change, a flagged number and a dying template
never reached a handler.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_account_and_quality_webhooks.py -v
"""

from __future__ import annotations

import uuid

import pytest

from tenants.models import WABAInfo
from wa.views import _classify_cloud_api_event

WABA = "waba-acct-1"


def _payload(field: str, value: dict) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": field, "value": value}]}]}


# ─────────────────────────────────────────────────────────────────────────────
# Classification — three fields that were being discarded
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("account_update", "ACCOUNT"),
        ("account_alerts", "ACCOUNT"),
        ("phone_number_quality_update", "ACCOUNT"),
        ("phone_number_name_update", "ACCOUNT"),
        ("message_template_quality_update", "TEMPLATE"),
        ("message_template_status_update", "TEMPLATE"),
        ("template_category_update", "TEMPLATE"),
    ],
)
def test_the_field_is_classified(field, expected):
    assert _classify_cloud_api_event(_payload(field, {})) == expected


def test_a_genuinely_unknown_field_is_still_unknown():
    """Widening the classifier must not turn it into a catch-all."""
    assert _classify_cloud_api_event(_payload("some_future_field", {})) == "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _wa_app():
    from tenants.models import Tenant
    from wa.models import WAApp

    tenant = Tenant.objects.create(name=f"AcctTenant-{uuid.uuid4().hex[:6]}", is_active=True)
    return WAApp.objects.create(
        tenant=tenant,
        app_name=f"app-{uuid.uuid4().hex[:6]}",
        app_id=f"a-{uuid.uuid4().hex[:6]}",
        app_secret="s",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=WABA,
        phone_number_id="pn-1",
        bsp="META",
        is_active=True,
    )


def _event(wa_app, field, value, event_type="ACCOUNT"):
    from wa.models import WAWebhookEvent

    # is_processed=True so the post_save signal does not run it before we do.
    return WAWebhookEvent.objects.create(
        wa_app=wa_app,
        bsp="META",
        event_type=event_type,
        payload=_payload(field, value),
        is_processed=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Account events
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_tier_change_is_applied():
    """The push counterpart of the sync — a tier change lands immediately."""
    from wa.tasks import process_account_webhook

    wa_app = _wa_app()
    event = _event(wa_app, "phone_number_quality_update", {"event": "ONBOARDING", "current_limit": "TIER_10K"})

    process_account_webhook(str(event.pk))

    assert WABAInfo.objects.get(wa_app=wa_app).messaging_limit == "TIER_10K"


@pytest.mark.django_db
def test_a_flagged_number_drops_to_red():
    from wa.tasks import process_account_webhook

    wa_app = _wa_app()
    event = _event(wa_app, "phone_number_quality_update", {"event": "FLAGGED"})

    process_account_webhook(str(event.pk))

    assert WABAInfo.objects.get(wa_app=wa_app).phone_quality == "RED"


@pytest.mark.django_db
def test_unflagging_does_not_invent_a_quality_level():
    """UNFLAGGED says it recovered, not to what. Guessing GREEN would be a lie."""
    from wa.tasks import process_account_webhook

    wa_app = _wa_app()
    WABAInfo.objects.update_or_create(wa_app=wa_app, defaults={"phone_quality": "RED"})
    event = _event(wa_app, "phone_number_quality_update", {"event": "UNFLAGGED"})

    process_account_webhook(str(event.pk))

    assert WABAInfo.objects.get(wa_app=wa_app).phone_quality == "RED"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("event_name", "expected"),
    [
        ("ACCOUNT_RESTRICTION", "LIMITED"),
        ("ACCOUNT_VIOLATION", "BLOCKED"),
        ("DISABLED_UPDATE", "BLOCKED"),
        ("ACCOUNT_VERIFIED", "AVAILABLE"),
    ],
)
def test_account_events_map_to_send_state(event_name, expected):
    from wa.tasks import process_account_webhook

    wa_app = _wa_app()
    event = _event(wa_app, "account_update", {"event": event_name})

    process_account_webhook(str(event.pk))

    assert WABAInfo.objects.get(wa_app=wa_app).can_send_message == expected


@pytest.mark.django_db
def test_an_unmapped_event_changes_nothing():
    """Inventing a state from an event we do not understand is worse than none."""
    from wa.tasks import process_account_webhook

    wa_app = _wa_app()
    WABAInfo.objects.update_or_create(wa_app=wa_app, defaults={"can_send_message": "AVAILABLE"})
    event = _event(wa_app, "account_update", {"event": "PARTNER_APP_INSTALLED"})

    process_account_webhook(str(event.pk))

    info = WABAInfo.objects.get(wa_app=wa_app)
    assert info.can_send_message == "AVAILABLE"


@pytest.mark.django_db
def test_an_unknown_tier_is_not_stored():
    from wa.tasks import process_account_webhook

    wa_app = _wa_app()
    event = _event(wa_app, "phone_number_quality_update", {"current_limit": "TIER_500K"})

    process_account_webhook(str(event.pk))

    assert WABAInfo.objects.get(wa_app=wa_app).messaging_limit is None


@pytest.mark.django_db
def test_batched_account_changes_are_all_applied():
    """Meta batches these like everything else — the lesson from #268."""
    from wa.models import WAWebhookEvent
    from wa.tasks import process_account_webhook

    wa_app = _wa_app()
    event = WAWebhookEvent.objects.create(
        wa_app=wa_app,
        bsp="META",
        event_type="ACCOUNT",
        is_processed=True,
        payload={
            "entry": [
                {
                    "id": WABA,
                    "changes": [
                        {"field": "phone_number_quality_update", "value": {"current_limit": "TIER_100K"}},
                        {"field": "account_update", "value": {"event": "ACCOUNT_RESTRICTION"}},
                    ],
                }
            ]
        },
    )

    process_account_webhook(str(event.pk))

    info = WABAInfo.objects.get(wa_app=wa_app)
    assert info.messaging_limit == "TIER_100K"
    assert info.can_send_message == "LIMITED"


@pytest.mark.django_db
def test_the_event_is_always_marked_processed():
    """Even a payload we cannot act on must not join the stuck backlog."""
    from wa.models import WAWebhookEvent
    from wa.tasks import process_account_webhook

    wa_app = _wa_app()
    event = WAWebhookEvent.objects.create(
        wa_app=wa_app, bsp="META", event_type="ACCOUNT", payload={"entry": "junk"}, is_processed=True
    )
    WAWebhookEvent.objects.filter(pk=event.pk).update(is_processed=False)

    process_account_webhook(str(event.pk))

    assert WAWebhookEvent.objects.get(pk=event.pk).is_processed is True


# ─────────────────────────────────────────────────────────────────────────────
# Template quality
# ─────────────────────────────────────────────────────────────────────────────


def _template(wa_app, **overrides):
    from wa.models import WATemplate

    fields = {
        "wa_app": wa_app,
        "name": f"T {uuid.uuid4().hex[:6]}",
        "element_name": f"t_{uuid.uuid4().hex[:8]}",
        "language_code": "en",
        "category": "MARKETING",
        "template_type": "TEXT",
        "content": "Hi",
        "status": "APPROVED",
        "meta_template_id": f"mt-{uuid.uuid4().hex[:8]}",
    }
    fields.update(overrides)
    return WATemplate.objects.create(**fields)


@pytest.mark.django_db
def test_a_template_quality_drop_is_recorded():
    """Previously invisible until META paused the template."""
    from wa.tasks import _process_meta_template_webhook

    wa_app = _wa_app()
    template = _template(wa_app)
    event = _event(
        wa_app,
        "message_template_quality_update",
        {
            "message_template_id": template.meta_template_id,
            "message_template_name": template.element_name,
            "message_template_language": "en",
            "previous_quality_score": "GREEN",
            "new_quality_score": "RED",
        },
        event_type="TEMPLATE",
    )

    _process_meta_template_webhook(event, event.payload)

    template.refresh_from_db()
    assert template.quality_rating == "RED"
    assert template.quality_rating_updated_at is not None


@pytest.mark.django_db
def test_a_quality_drop_does_not_change_the_template_status():
    """A warning, not a state change. PAUSED arrives on its own event."""
    from wa.tasks import _process_meta_template_webhook

    wa_app = _wa_app()
    template = _template(wa_app, status="APPROVED")
    event = _event(
        wa_app,
        "message_template_quality_update",
        {"message_template_id": template.meta_template_id, "new_quality_score": "RED"},
        event_type="TEMPLATE",
    )

    _process_meta_template_webhook(event, event.payload)

    template.refresh_from_db()
    assert template.status == "APPROVED"


@pytest.mark.django_db
def test_an_unrecognised_score_is_not_stored():
    from wa.tasks import _process_meta_template_webhook

    wa_app = _wa_app()
    template = _template(wa_app)
    event = _event(
        wa_app,
        "message_template_quality_update",
        {"message_template_id": template.meta_template_id, "new_quality_score": "CHARTREUSE"},
        event_type="TEMPLATE",
    )

    _process_meta_template_webhook(event, event.payload)

    template.refresh_from_db()
    assert template.quality_rating is None
