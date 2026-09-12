"""Automatic webhook registration, for every BSP rather than one (#259).

The only automatic webhook registration in the codebase was called
``auto_register_gupshup_webhook`` and opened with::

    if wa_app.bsp != BSPChoices.GUPSHUP:
        return {"status": "skipped", "reason": "not_gupshup"}

with a second copy of the same test in ``tenants.signals``, which meant a Meta
Direct app never even queued the task. So creating one produced an app with no
subscription: #264 gave Meta a real ``POST /{waba_id}/subscribed_apps`` and
#310/#311/#306 gave it an authenticated per-app receiver, and nothing joined the
two — nothing inbound arrived until an operator knew to press *Refresh
Webhooks*, per app, unprompted.

What these tests pin, and why each one is here
----------------------------------------------

* **A Meta app gets a subscription with no manual step** — the acceptance
  criterion, end to end from ``post_save`` through the adapter to the Graph call.
* **A Gupshup app still does.** Gupshup is the provider actually in use and it
  worked before this change; #312/#313 are what a silently-unfired signal costs.
  Asserted, not assumed.
* **A blank ``bsp`` column is META, not "no BSP"** (#265). A registration path
  that tests the raw column skips every pre-existing row, which is the failure
  mode that makes this class of gate so easy to reintroduce.
* **The path is chosen by ``get_bsp_adapter``**, not by comparing a BSP. This is
  the durable half of the ticket — a correct Meta branch added beside the
  Gupshup one would satisfy every behavioural test above and still be the bug.
* **An unregisterable BSP skips with a reason naming it.** ``not_gupshup`` said
  nothing about which provider was refused.
* **The registered URL is the app's own per-app URL**, the one
  ``webhook_identity.webhook_setup`` tells the client to paste. Registering the
  legacy deployment-wide path instead is the silent non-delivery #310 exists to
  prevent (#334 is the ticket that settles this for subscription *refresh*; it
  is taken as given here).

No network: the Graph boundary is ``meta_path.FakeGraph``, which raises on an
unrouted request, and Gupshup's partner client is replaced at its own seam.

HOW TO RUN:
    DB_NAME=jc259 python -m pytest wa/tests/test_auto_webhook_registration.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from wa.models import SubscriptionStatus, WASubscription
from wa.services import webhook_identity
from wa.tasks import auto_register_bsp_webhook
from wa.tests.meta_path import FakeGraph, meta_wa_app, tenant

pytestmark = pytest.mark.django_db

BASE = "https://hooks.example.test"

#: A WABA with one app subscribed — what Meta's verifying ``GET`` returns once
#: the ``POST`` has taken effect. ``register_webhook`` refuses to report success
#: on an empty list, which is the #264 guard.
SUBSCRIBED_ONE = {"data": [{"whatsapp_business_api_data": {"id": "APP-1", "name": "Our App"}}]}


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures and doubles
# ─────────────────────────────────────────────────────────────────────────────


def _gupshup_app(owner=None, **overrides):
    """A ``TenantWAApp`` unambiguously on Gupshup, with partner credentials.

    ``bsp`` is written explicitly for the same reason ``meta_wa_app`` does it:
    the column's default happens to be ``GUPSHUP``, so a test built on the
    default would pass whether or not the Gupshup branch was ever reached.
    """
    from tenants.models import BSPChoices
    from wa.models import WAApp

    suffix = uuid.uuid4().hex[:8]
    fields = {
        "tenant": owner or tenant("GupshupAuto"),
        "app_name": f"gs-app-{suffix}",
        "app_id": f"gs_{suffix}",
        "app_secret": f"secret_{suffix}",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": f"waba_{suffix}",
        "phone_number_id": f"pn_{suffix}",
        "bsp": BSPChoices.GUPSHUP,
        "bsp_partner_app_token": f"partner_{suffix}",
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


def _app_with_raw_bsp(raw_bsp: str, **overrides):
    """An app whose ``bsp`` column holds exactly *raw_bsp*, blank included.

    Written through ``update`` after creation because ``save()`` supplies the
    column's default, and the blank column is precisely what is under test.
    """
    app = _gupshup_app(**overrides)
    type(app).objects.filter(pk=app.pk).update(bsp=raw_bsp)
    app.refresh_from_db()
    return app


class _NoWebhookAdapter:
    """An adapter for a BSP that cannot be told where to deliver.

    Declares no ``subscriptions`` capability. Registered into the real
    ``_ADAPTER_REGISTRY`` by :func:`_registry_with` so the skip is reached
    through the production factory rather than by stubbing the factory out —
    the point of the ticket is that the factory is what decides.
    """

    PROVIDER_NAME = "nowebhook"

    def __init__(self, wa_app):
        self.wa_app = wa_app

    def supports(self, capability: str) -> bool:
        return False

    def register_webhook(self, subscription):  # pragma: no cover — must not be called
        raise AssertionError("register_webhook must not be called for a BSP that does not support it")


class _LyingAdapter(_NoWebhookAdapter):
    """Declares the capability and then does not implement the method.

    #266 is this disagreement the other way round — a flag omitted for a method
    that worked — so it is a mistake made here before, and the likely way in is a
    new adapter copying ``capabilities`` wholesale over a stub.
    """

    def supports(self, capability: str) -> bool:
        return True

    def register_webhook(self, subscription):
        raise NotImplementedError("nowebhook adapter does not implement register_webhook()")


def _registry_with(monkeypatch, bsp: str, adapter_cls):
    """Add *bsp* -> *adapter_cls* to the live adapter registry for one test."""
    from wa import adapters

    monkeypatch.setitem(adapters._ADAPTER_REGISTRY, bsp, adapter_cls)


def _ok_result(bsp_subscription_id="GS-1"):
    from wa.adapters.base import AdapterResult

    return AdapterResult(success=True, provider="test", data={"bsp_subscription_id": bsp_subscription_id})


# ─────────────────────────────────────────────────────────────────────────────
# The signal dispatches for every BSP
#
# ``tenants.signals`` held the second BSP test, and it is the one that decided
# nothing happened at all for a Meta app: the task was never queued, so even a
# BSP-neutral task would have sat unused. ``.delay`` is patched rather than run
# because what is under test here is the dispatch, and because the suite runs
# with no broker (see the root ``conftest``) — a test that depended on eager
# execution would pass or fail on CI's Redis rather than on this code.
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_creating_a_meta_app_queues_registration():
    """The bug: a Meta Direct app queued nothing, so its WABA stayed unsubscribed."""
    with patch.object(auto_register_bsp_webhook, "delay") as delay:
        app = meta_wa_app(tenant("MetaAuto"))

    delay.assert_called_once_with(app.pk)


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_creating_a_gupshup_app_still_queues_registration():
    """Gupshup auto-registration worked before #259 and must go on working.

    It is the provider actually in use, and an unfired creation signal is one of
    the consequences #312/#313 were filed for.
    """
    with patch.object(auto_register_bsp_webhook, "delay") as delay:
        app = _gupshup_app()

    delay.assert_called_once_with(app.pk)


@override_settings(CELERY_BROKER_URL="")
def test_no_broker_still_skips_dispatch():
    """Pre-existing behaviour, unchanged: no broker configured, nothing queued."""
    with patch.object(auto_register_bsp_webhook, "delay") as delay:
        _gupshup_app()

    delay.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# The task selects its path through get_bsp_adapter
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw_bsp", ["META", "GUPSHUP", ""])
def test_the_adapter_factory_chooses_the_registration_path(raw_bsp):
    """Acceptance criterion 3, asserted structurally rather than behaviourally.

    A hand-written ``if bsp == META: ... elif bsp == GUPSHUP: ...`` would pass
    every other test in this file. What must be true is that the call site asks
    the factory — so the factory is where the assertion goes, for all three
    values of the column including the blank one.
    """
    app = _app_with_raw_bsp(raw_bsp)
    adapter = MagicMock()
    adapter.supports.return_value = True
    adapter.register_webhook.return_value = _ok_result()

    with patch("wa.adapters.get_bsp_adapter", return_value=adapter) as factory:
        result = auto_register_bsp_webhook(app.pk)

    factory.assert_called_once_with(app)
    adapter.register_webhook.assert_called_once()
    assert result["status"] == "success"


def test_a_blank_bsp_column_is_registered_as_meta():
    """A blank column is META, not "no BSP" (#265).

    A path that tested the raw column would skip every pre-existing row. The
    resolved BSP is reported in the result, so this pins the answer rather than
    only that something happened.
    """
    app = _app_with_raw_bsp("")
    adapter = MagicMock()
    adapter.supports.return_value = True
    adapter.register_webhook.return_value = _ok_result()

    with patch("wa.adapters.get_bsp_adapter", return_value=adapter):
        result = auto_register_bsp_webhook(app.pk)

    assert result["bsp"] == "META"
    assert result["status"] == "success"


# ─────────────────────────────────────────────────────────────────────────────
# A BSP that cannot be registered with skips, and says which one
# ─────────────────────────────────────────────────────────────────────────────


def test_a_bsp_with_no_adapter_skips_naming_the_bsp():
    """``TWILIO`` is a real ``BSPChoices`` value with no adapter registered."""
    app = _app_with_raw_bsp("TWILIO")

    result = auto_register_bsp_webhook(app.pk)

    assert result["status"] == "skipped"
    assert result["bsp"] == "TWILIO"
    assert "TWILIO" in result["reason"]
    assert "not_gupshup" not in result["reason"]


def test_a_bsp_whose_adapter_cannot_register_skips_naming_the_bsp(monkeypatch):
    """An adapter exists but declares no ``subscriptions`` capability.

    Reached through the production factory — the registry entry is real for the
    duration of the test — so this is the same code path a future BSP adapter
    without webhook support would take.
    """
    _registry_with(monkeypatch, "TWILIO", _NoWebhookAdapter)
    app = _app_with_raw_bsp("TWILIO")

    result = auto_register_bsp_webhook(app.pk)

    assert result["status"] == "skipped"
    assert "TWILIO" in result["reason"]
    assert not WASubscription.objects.filter(wa_app=app).exists(), (
        "a skip must not leave a PENDING subscription row for a BSP that will never confirm it"
    )


def test_an_adapter_that_declares_the_capability_but_lacks_the_method_skips(monkeypatch):
    """Retrying an unimplemented method three times cannot make it succeed.

    Without this arm a stub ``register_webhook`` burns the retry budget and logs
    an exception traceback instead of saying plainly that the BSP cannot be
    registered with — and leaves the row PENDING rather than FAILED.
    """
    _registry_with(monkeypatch, "TWILIO", _LyingAdapter)
    app = _app_with_raw_bsp("TWILIO")

    result = auto_register_bsp_webhook(app.pk)

    assert result["status"] == "skipped"
    assert "TWILIO" in result["reason"]
    assert WASubscription.objects.get(wa_app=app).status == SubscriptionStatus.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# Which URL gets registered (#334, taken as given)
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
@pytest.mark.parametrize("maker", [meta_wa_app, _gupshup_app], ids=["meta", "gupshup"])
def test_the_registered_url_is_the_apps_own_callback_url(maker):
    """The URL the client is told to paste, not the deployment-wide legacy one.

    The per-app path carries ``webhook_identifier``, so the receiver knows whose
    delivery it is before reading the body and can verify the signature against
    that app's own secret (#310/#311/#306). The legacy path authenticates against
    one deployment-wide secret and can only ever serve a single app; registering
    it for a client who was handed the per-app URL is non-delivery that reports
    itself as healthy.
    """
    app = maker(tenant("UrlChoice"))
    adapter = MagicMock()
    adapter.supports.return_value = True
    adapter.register_webhook.return_value = _ok_result()

    with patch("wa.adapters.get_bsp_adapter", return_value=adapter):
        auto_register_bsp_webhook(app.pk)

    subscription = WASubscription.objects.get(wa_app=app)
    assert subscription.webhook_url == webhook_identity.callback_url(app)
    assert app.webhook_identifier in subscription.webhook_url
    assert subscription.webhook_url != webhook_identity.legacy_callback_url(app)


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_an_app_already_active_on_the_legacy_url_is_not_registered_twice():
    """Gupshup allows five subscriptions per app; a duplicate burns one to no effect."""
    app = _gupshup_app()
    WASubscription.objects.create(
        wa_app=app,
        webhook_url=webhook_identity.legacy_callback_url(app),
        event_types=["MESSAGE"],
        status=SubscriptionStatus.ACTIVE,
    )

    with patch("wa.adapters.get_bsp_adapter") as factory:
        result = auto_register_bsp_webhook(app.pk)

    assert result["reason"] == "already_exists"
    factory.return_value.register_webhook.assert_not_called()
    assert WASubscription.objects.filter(wa_app=app).count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# End to end at the provider boundary
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_meta_registration_subscribes_the_waba_at_the_graph_boundary(monkeypatch):
    """The acceptance criterion, through the real adapter to the real edge.

    ``FakeGraph`` raises on any request it was not told to expect, so a
    registration that quietly called something else — or nothing — fails here
    rather than passing on a default.
    """
    app = meta_wa_app(tenant("MetaGraph"), app_id="APP-1")
    graph = FakeGraph().install(monkeypatch)
    graph.post("subscribed_apps", {"success": True})
    graph.get("subscribed_apps", SUBSCRIBED_ONE)

    result = auto_register_bsp_webhook(app.pk)

    assert result["status"] == "success"
    assert result["bsp"] == "META"

    posted = graph.only("POST", f"{app.waba_id}/subscribed_apps")
    assert posted.url.startswith("https://graph.facebook.com/")

    subscription = WASubscription.objects.get(wa_app=app)
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.bsp_subscription_id == "APP-1"


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_gupshup_registration_posts_the_per_app_url_to_the_partner_api():
    """Gupshup's own path, unbroken, carrying the per-app URL in the form body.

    Patched at ``_get_subscription_api`` — the adapter's credential seam — so
    everything above it is the production code path and nothing reaches the
    network.
    """
    from wa.adapters.gupshup import GupshupAdapter

    app = _gupshup_app()
    api = MagicMock()
    api.appId = app.app_id
    api.get_all_subscriptions.return_value = {"subscriptions": []}
    api.create_subscription.return_value = {"status": "success", "subscription": {"id": 4242}}

    with patch.object(GupshupAdapter, "_get_subscription_api", return_value=api):
        result = auto_register_bsp_webhook(app.pk)

    assert result["status"] == "success"
    assert result["bsp"] == "GUPSHUP"

    api.create_subscription.assert_called_once()
    payload = api.create_subscription.call_args[0][0]
    assert payload["url"] == webhook_identity.callback_url(app)
    assert app.webhook_identifier in payload["url"]

    subscription = WASubscription.objects.get(wa_app=app)
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.bsp_subscription_id == "4242"


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_the_requested_event_types_survive_gupshups_payload_validation():
    """Regression guard for a break this ticket's first Gupshup test exposed.

    Auto-registration asked for every ``WebhookEventType`` member. Two of them
    had no row in ``GupshupAdapter._map_event_types_to_gupshup_modes``, whose
    fallback passes an unmapped value through verbatim, so the modes list
    carried ``PAYMENT`` (Gupshup spells it ``PAYMENTS``) and ``UNKNOWN`` (our own
    unclassifiable bucket, not a category any provider offers). ``pydantic``
    refused the payload, the task retried three times on a ``ValidationError``
    and gave up: **every Gupshup app created registered nothing**, while the
    ticket's premise was that this path worked.

    Constructing the real ``SubscriptionFormData`` is the whole assertion — a
    test that mocked it away could not have found this, and an invalid mode
    would sail past an assertion on the URL alone.
    """
    from wa.adapters.gupshup import GupshupAdapter
    from wa.utility.data_model.gupshup.subscription import SubscriptionFormData

    app = _gupshup_app()
    api = MagicMock()
    api.appId = app.app_id
    api.get_all_subscriptions.return_value = {"subscriptions": []}
    api.create_subscription.return_value = {"status": "success", "subscription": {"id": 1}}

    with patch.object(GupshupAdapter, "_get_subscription_api", return_value=api):
        result = auto_register_bsp_webhook(app.pk)

    assert result["status"] == "success"

    subscription = WASubscription.objects.get(wa_app=app)
    assert "UNKNOWN" not in subscription.event_types
    assert "PAYMENT" in subscription.event_types, "payment events must still be subscribed to"

    modes = GupshupAdapter(app)._map_event_types_to_gupshup_modes(subscription.event_types)
    assert "PAYMENTS" in modes, "PAYMENT must map to Gupshup's spelling, not pass through"
    SubscriptionFormData(modes=modes, tag="t", url=subscription.webhook_url)


# ─────────────────────────────────────────────────────────────────────────────
# Failure and compatibility
# ─────────────────────────────────────────────────────────────────────────────


def test_a_missing_app_is_reported_not_raised():
    assert auto_register_bsp_webhook(10**9) == {"status": "error", "reason": "wa_app_not_found"}


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_a_refused_registration_is_not_reported_as_success():
    """A subscription that did not happen must never read as ACTIVE or succeed.

    That state is what made #264 invisible — a healthy-looking subscription for a
    customer receiving nothing. The adapters set FAILED themselves; this pins
    that the task does not paper over a refusal on the way out.

    Called directly, ``Task.retry(exc=...)`` re-raises the cause rather than
    ``Retry``: a direct call is ``called_directly``, so there is no request to
    re-queue. In a worker the same line schedules the next attempt. Either way
    the refusal propagates, which is the property worth pinning — the failure
    must not be swallowed into a success return.
    """
    from wa.adapters.base import AdapterResult

    app = _gupshup_app()
    adapter = MagicMock()
    adapter.supports.return_value = True
    adapter.register_webhook.return_value = AdapterResult(
        success=False, provider="test", error_message="partner API said no"
    )

    with patch("wa.adapters.get_bsp_adapter", return_value=adapter):
        with pytest.raises(Exception, match="partner API said no"):
            auto_register_bsp_webhook(app.pk)

    assert WASubscription.objects.get(wa_app=app).status != SubscriptionStatus.ACTIVE


def test_the_old_task_name_is_still_registered():
    """Rolling deploys: an old web process keeps enqueuing the old name.

    A name the new worker does not know is acked and dropped, which for this
    task is an app created mid-deploy with no subscription — the bug itself.
    """
    import wa.tasks  # noqa: F401 — importing is what registers the tasks
    from jina_connect.celery import app as celery_app

    assert "wa.tasks.auto_register_gupshup_webhook" in celery_app.tasks
    assert "wa.tasks.auto_register_bsp_webhook" in celery_app.tasks


def test_the_old_name_runs_the_same_body():
    """The alias is a shim, not a second implementation that can drift."""
    from wa.tasks import auto_register_gupshup_webhook

    assert auto_register_gupshup_webhook(10**9) == {"status": "error", "reason": "wa_app_not_found"}
