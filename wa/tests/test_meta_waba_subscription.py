"""WABA → app subscription on Meta Direct (#264).

`POST /{waba_id}/subscribed_apps` appeared nowhere in the repository.
`MetaDirectAdapter.register_webhook` flipped the local row to ACTIVE and
returned success without making any call, so an onboarded customer received
nothing from Meta — no messages, no statuses, no template decisions — while
the platform reported a healthy subscription.

The property that matters most here is not "the happy path works". It is that
**a subscription which did not happen must never read as ACTIVE**, because
that is the state that made the original bug invisible.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_meta_waba_subscription.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from wa.adapters.meta_direct import MetaDirectAdapter
from wa.models import SubscriptionStatus

SUBSCRIBED_ONE = {"data": [{"whatsapp_business_api_data": {"id": "APP-1", "name": "Our App"}}]}


def _wa_app(**overrides):
    from tenants.models import Tenant
    from wa.models import WAApp

    tenant = Tenant.objects.create(name=f"SubTenant-{uuid.uuid4().hex[:6]}", is_active=True)
    fields = {
        "tenant": tenant,
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": "APP-1",
        "app_secret": "s",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": f"waba-{uuid.uuid4().hex[:6]}",
        "phone_number_id": f"pn-{uuid.uuid4().hex[:6]}",
        "bsp": "META",
        "bsp_credentials": {"access_token": "tok"},
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


def _subscription(wa_app):
    from wa.models import WASubscription

    return WASubscription.objects.create(
        wa_app=wa_app,
        webhook_url="https://example.test/wa/webhook/meta",
        event_types=["messages"],
        status=SubscriptionStatus.PENDING,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The call is made at all
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_registering_subscribes_the_waba_to_the_app():
    """The regression guard: a no-op implementation fails this outright."""
    wa_app = _wa_app()
    sub = _subscription(wa_app)

    with patch("wa.utility.apis.meta.waba.WABAAPI.subscribe_app") as subscribe, patch(
        "wa.utility.apis.meta.waba.WABAAPI.get_subscribed_apps", return_value=SUBSCRIBED_ONE
    ):
        result = MetaDirectAdapter(wa_app).register_webhook(sub)

    subscribe.assert_called_once()
    assert result.success is True


@pytest.mark.django_db
def test_it_posts_to_the_waba_subscribed_apps_edge():
    """Pins the actual Graph edge, not merely that *something* was called."""
    wa_app = _wa_app()
    sub = _subscription(wa_app)

    with patch("wa.utility.apis.meta.waba.WABAAPI.make_request", return_value=SUBSCRIBED_ONE) as req:
        MetaDirectAdapter(wa_app).register_webhook(sub)

    methods_and_urls = [(c.args[0]["method"], c.args[0]["url"]) for c in req.call_args_list]
    assert ("POST", f"https://graph.facebook.com/v24.0/{wa_app.waba_id}/subscribed_apps") in methods_and_urls


@pytest.mark.django_db
def test_success_records_active_and_the_subscribed_app_id():
    wa_app = _wa_app()
    sub = _subscription(wa_app)

    with patch("wa.utility.apis.meta.waba.WABAAPI.subscribe_app"), patch(
        "wa.utility.apis.meta.waba.WABAAPI.get_subscribed_apps", return_value=SUBSCRIBED_ONE
    ):
        MetaDirectAdapter(wa_app).register_webhook(sub)

    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.bsp_subscription_id == "APP-1"
    assert sub.error_message is None


# ─────────────────────────────────────────────────────────────────────────────
# A subscription that did not happen is not ACTIVE
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_refused_subscription_is_not_active():
    """The heart of #264: success was reported for a call never made."""
    wa_app = _wa_app()
    sub = _subscription(wa_app)

    with patch("wa.utility.apis.meta.waba.WABAAPI.subscribe_app", side_effect=Exception("(#200) permissions")):
        result = MetaDirectAdapter(wa_app).register_webhook(sub)

    assert result.success is False
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.FAILED
    assert "permissions" in (sub.error_message or "")


@pytest.mark.django_db
def test_an_accepted_post_with_no_listed_app_is_a_failure():
    """Meta says success, lists nothing — nothing would be delivered."""
    wa_app = _wa_app()
    sub = _subscription(wa_app)

    with patch("wa.utility.apis.meta.waba.WABAAPI.subscribe_app"), patch(
        "wa.utility.apis.meta.waba.WABAAPI.get_subscribed_apps", return_value={"data": []}
    ):
        result = MetaDirectAdapter(wa_app).register_webhook(sub)

    assert result.success is False
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.FAILED


@pytest.mark.django_db
def test_an_unverifiable_subscription_is_a_failure():
    wa_app = _wa_app()
    sub = _subscription(wa_app)

    with patch("wa.utility.apis.meta.waba.WABAAPI.subscribe_app"), patch(
        "wa.utility.apis.meta.waba.WABAAPI.get_subscribed_apps", side_effect=Exception("rate limited")
    ):
        result = MetaDirectAdapter(wa_app).register_webhook(sub)

    assert result.success is False
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.FAILED


@pytest.mark.django_db
def test_a_missing_waba_id_fails_and_says_so():
    """Names the unset field rather than surfacing a Graph 401 later."""
    wa_app = _wa_app(waba_id="")
    sub = _subscription(wa_app)

    result = MetaDirectAdapter(wa_app).register_webhook(sub)

    assert result.success is False
    assert "WABA ID" in (result.error_message or "")
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.FAILED


@pytest.mark.django_db
def test_a_missing_token_fails_and_says_so(settings):
    settings.META_PERM_TOKEN = ""
    wa_app = _wa_app(bsp_credentials={})
    sub = _subscription(wa_app)

    result = MetaDirectAdapter(wa_app).register_webhook(sub)

    assert result.success is False
    assert "access token" in (result.error_message or "")


# ─────────────────────────────────────────────────────────────────────────────
# A mismatched app_id warns rather than refusing
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_mismatched_app_id_still_succeeds():
    """``app_id`` is overloaded — documented as Gupshup's, reused as Meta's.

    A mismatch is therefore not trustworthy enough to refuse a subscription
    Meta has confirmed; it is logged and the listed app is recorded instead.
    """
    wa_app = _wa_app(app_id="some-gupshup-value")
    sub = _subscription(wa_app)

    with patch("wa.utility.apis.meta.waba.WABAAPI.subscribe_app"), patch(
        "wa.utility.apis.meta.waba.WABAAPI.get_subscribed_apps", return_value=SUBSCRIBED_ONE
    ):
        result = MetaDirectAdapter(wa_app).register_webhook(sub)

    assert result.success is True
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.bsp_subscription_id == "APP-1"


@pytest.mark.django_db
def test_malformed_rows_in_the_listing_are_skipped():
    wa_app = _wa_app()
    sub = _subscription(wa_app)
    listing = {"data": [None, "junk", {}, {"whatsapp_business_api_data": {}}, SUBSCRIBED_ONE["data"][0]]}

    with patch("wa.utility.apis.meta.waba.WABAAPI.subscribe_app"), patch(
        "wa.utility.apis.meta.waba.WABAAPI.get_subscribed_apps", return_value=listing
    ):
        result = MetaDirectAdapter(wa_app).register_webhook(sub)

    assert result.success is True
    sub.refresh_from_db()
    assert sub.bsp_subscription_id == "APP-1"


# ─────────────────────────────────────────────────────────────────────────────
# Unregistering
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_unregistering_unsubscribes_the_waba():
    wa_app = _wa_app()
    sub = _subscription(wa_app)
    sub.status = SubscriptionStatus.ACTIVE
    sub.save(update_fields=["status"])

    with patch("wa.utility.apis.meta.waba.WABAAPI.unsubscribe_app") as unsubscribe:
        result = MetaDirectAdapter(wa_app).unregister_webhook(sub)

    unsubscribe.assert_called_once()
    assert result.success is True
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.INACTIVE


@pytest.mark.django_db
def test_a_refused_unsubscribe_does_not_read_as_torn_down():
    """Meta would still be delivering; INACTIVE would be a lie."""
    wa_app = _wa_app()
    sub = _subscription(wa_app)

    with patch("wa.utility.apis.meta.waba.WABAAPI.unsubscribe_app", side_effect=Exception("boom")):
        result = MetaDirectAdapter(wa_app).unregister_webhook(sub)

    assert result.success is False
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.FAILED
