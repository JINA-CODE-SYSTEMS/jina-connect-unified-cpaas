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

Two ways this path went on losing work after #259, added later
--------------------------------------------------------------

* **Every ``WebhookEventType`` member maps to valid Gupshup modes, or is
  declared unsupported on purpose.** An unmapped type used to be passed through
  verbatim as a Gupshup *mode*, which pydantic refused — taking down the whole
  payload, every valid mode with it. #348 mapped the two members that had
  already broken it and left the rule unenforced, so the next member added
  reproduced it. Pinned at the mapping site, through the real
  ``SubscriptionFormData``, and over both refresh surfaces as well as creation —
  they pass whatever a stored ``WASubscription`` carries.
* **A broker that refuses the dispatch does not cost the app its
  subscription.** ``tenants.signals`` had an inline ``if
  settings.CELERY_BROKER_URL`` check, which asks whether a string is set and not
  whether a queue is reachable. It dispatches through ``wa.signals._dispatch``
  now (#269's helper), which falls back to running in-process. "No broker
  configured" stays a deliberate no-op — see
  ``test_no_broker_still_skips_dispatch``.

No network: the Graph boundary is ``meta_path.FakeGraph``, which raises on an
unrouted request, and Gupshup's partner client is replaced at its own seam.

HOW TO RUN:
    DB_NAME=jc259 python -m pytest wa/tests/test_auto_webhook_registration.py -v
"""

from __future__ import annotations

import logging
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
#
# The pk arrives as a **string**: the signal dispatches through
# ``wa.signals._dispatch``, which normalises every pk on its way to a queue
# (``task.delay(str(pk))``). That normalisation exists for the two ``wa``
# callers, whose pks are UUIDs, and ``TenantWAApp.objects.get(pk="27")``
# resolves an integer pk from its string form unchanged — so the string is
# asserted here rather than removed from the one place a pk is prepared for
# transport.
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_creating_a_meta_app_queues_registration():
    """The bug: a Meta Direct app queued nothing, so its WABA stayed unsubscribed."""
    with patch.object(auto_register_bsp_webhook, "delay") as delay:
        app = meta_wa_app(tenant("MetaAuto"))

    delay.assert_called_once_with(str(app.pk))


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_creating_a_gupshup_app_still_queues_registration():
    """Gupshup auto-registration worked before #259 and must go on working.

    It is the provider actually in use, and an unfired creation signal is one of
    the consequences #312/#313 were filed for.
    """
    with patch.object(auto_register_bsp_webhook, "delay") as delay:
        app = _gupshup_app()

    delay.assert_called_once_with(str(app.pk))


@override_settings(CELERY_BROKER_URL="")
def test_no_broker_still_skips_dispatch():
    """Pre-existing behaviour, unchanged: no broker configured, nothing queued.

    This is a decision, not an accident, and the dispatch helper is told so
    explicitly (``run_without_broker=False``). An empty ``CELERY_BROKER_URL`` is
    what a dev box, CI and this very suite look like — not a fault — and
    registration is an outbound call to a BSP partner API, so the in-process
    fallback ``_dispatch`` offers would make *every* test that creates a WA app
    reach for Graph or Gupshup's partner API.

    So the assertion is not only that nothing was queued but that nothing was
    *registered* either: a fallback quietly applying here would leave a
    subscription row behind, and that row is the visible half of an outbound
    call nobody asked for.
    """
    with patch.object(auto_register_bsp_webhook, "delay") as delay:
        app = _gupshup_app()

    delay.assert_not_called()
    assert not WASubscription.objects.filter(wa_app=app).exists(), (
        "no broker configured must stay a no-op — an in-process fallback here would make app creation "
        "call a BSP partner API on every dev box and in every test that creates an app"
    )


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0", DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_a_configured_but_unreachable_broker_registers_in_process_instead_of_losing_it():
    """The live defect, and the one the old inline check could not see.

    ``tenants.signals`` tested ``if settings.CELERY_BROKER_URL`` — whether a
    string is set, not whether a queue is reachable, and the setting has a
    non-empty default. With the broker configured and down, ``.delay()`` raised
    out of a ``post_save`` handler and the app was created with no subscription:
    #259's own bug, whose only recovery is an admin action an operator has to
    know to press. Since #259 made this signal the dispatch point for every app
    on every BSP, one lost dispatch now costs every newly created app.

    The adapter is mocked, not the registration: what is under test is that the
    work happens at all, so the subscription row and the adapter call are the
    assertions.
    """
    adapter = MagicMock()
    adapter.supports.return_value = True
    adapter.register_webhook.return_value = _ok_result()

    with patch.object(auto_register_bsp_webhook, "delay", side_effect=OSError("Connection refused")) as delay:
        with patch("wa.adapters.get_bsp_adapter", return_value=adapter):
            app = _gupshup_app()

    delay.assert_called_once()
    adapter.register_webhook.assert_called_once()
    assert WASubscription.objects.filter(wa_app=app).exists(), (
        "a broker that refuses the dispatch must not cost the app its webhook subscription"
    )


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
# Every event type is accounted for, and an unaccounted one degrades
#
# The test above pins the two instances that broke. This section pins the
# *rule*, because the fallback that broke them was unchanged by the fix: the
# next member added to ``WebhookEventType`` reproduces it exactly. An event type
# with no row in the mode map used to be passed through verbatim as a Gupshup
# mode, and ``SubscriptionFormData`` validates modes against a closed
# ``Literal`` — so one unmapped type did not cost its own events, it raised
# ``ValidationError`` and took the entire payload down with every valid mode in
# it, before any HTTP request existed to fail.
#
# Unmapped types are now dropped, with a log line, and the rest is subscribed
# to. These tests are the other half of that choice: dropping is only safe while
# a gap in the table cannot reach production unnoticed.
# ─────────────────────────────────────────────────────────────────────────────


class _WaLogCapture(logging.Handler):
    """Records messages from the ``wa`` logger.

    The project's LOGGING config sets ``propagate=False`` on ``wa``, so
    ``caplog`` alone sees nothing from the adapters.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    def __enter__(self):
        logging.getLogger("wa").addHandler(self)
        return self

    def __exit__(self, *exc):
        logging.getLogger("wa").removeHandler(self)
        return False

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def test_every_webhook_event_type_either_maps_to_modes_or_is_declared_unsupported():
    """The guard that would have caught the original break, and the next one.

    Walks every ``WebhookEventType`` member and requires each to be one of two
    things: mapped to Gupshup modes the **real** ``SubscriptionFormData``
    accepts, or named in ``GupshupAdapter.UNSUPPORTED_EVENT_TYPES`` — an
    explicit, reasoned exclusion. A member that is neither is the bug: it was
    passed through as a mode nobody validated, and when #348 mapped ``PAYMENT``
    and stopped requesting ``UNKNOWN`` it fixed the two instances and left the
    rule unenforced.

    Constructing the real form data is the whole assertion. A test that mocked
    it away, or that compared the mode map against a list of expected strings,
    is precisely what let an invalid mode hide.
    """
    from wa.adapters.gupshup import GupshupAdapter
    from wa.models import WebhookEventType
    from wa.utility.data_model.gupshup.subscription import SubscriptionFormData

    adapter = GupshupAdapter(_gupshup_app())
    excluded = GupshupAdapter.UNSUPPORTED_EVENT_TYPES

    for event_type in WebhookEventType:
        modes = adapter._map_event_types_to_gupshup_modes([event_type.value])

        if event_type.value in excluded:
            assert modes == [], (
                f"{event_type.value} is declared unsubscribable on Gupshup but still produced modes {modes}"
            )
            continue

        assert modes, (
            f"{event_type.value} maps to no Gupshup mode. Give it a row in "
            f"GupshupAdapter.EVENT_TYPE_TO_MODES, or list it in UNSUPPORTED_EVENT_TYPES with the reason "
            f"Gupshup has no mode for it — an unmapped type used to be passed through verbatim and "
            f"took the whole subscription payload down with it."
        )
        # Raises ValidationError if any produced mode is not one Gupshup offers.
        SubscriptionFormData(modes=modes, tag=f"t_{event_type.value}", url="https://hooks.example.test/h/")


def test_the_unsupported_event_types_are_a_current_and_reasoned_list():
    """An exclusion has to stay a decision, not become a dumping ground.

    Two ways the list rots: a reason-less entry, which is indistinguishable from
    "we gave up"; and an entry for an event type that no longer exists, whose
    stale excuse would silently cover whatever replaced it — so a renamed member
    would arrive unmapped *and* apparently accounted for.
    """
    from wa.adapters.gupshup import GupshupAdapter
    from wa.models import WebhookEventType

    known = {et.value for et in WebhookEventType}

    for event_type, reason in GupshupAdapter.UNSUPPORTED_EVENT_TYPES.items():
        assert event_type in known, (
            f"UNSUPPORTED_EVENT_TYPES names {event_type!r}, which is not a WebhookEventType any more — "
            f"a stale entry excuses a type that no longer exists and covers for whatever replaced it"
        )
        assert reason.strip(), f"{event_type} is excluded with no reason given"
        assert event_type not in GupshupAdapter.EVENT_TYPE_TO_MODES, (
            f"{event_type} is both mapped and declared unsupported — one of the two is wrong"
        )


def test_an_unmapped_event_type_is_dropped_and_the_rest_still_subscribed():
    """The fallback's replacement, at the mapping site.

    ``FLOWS_MESSAGE`` stands in for the next member added to
    ``WebhookEventType`` without a row: under the old passthrough the modes list
    carried it verbatim and pydantic refused everything, so ``MESSAGE`` and
    ``STATUS`` — both perfectly valid, both asked for — were lost to it. Now the
    unmappable one is dropped and the rest is registered.

    ``FLOWS_MESSAGE`` is deliberately a string Gupshup itself *does* accept as a
    mode: the two vocabularies overlap in places, which is why passing one off
    as the other looked like it worked, and why a dropped type must be dropped
    for not being in the table rather than for failing validation downstream.
    """
    from wa.adapters.gupshup import GupshupAdapter
    from wa.utility.data_model.gupshup.subscription import SubscriptionFormData

    adapter = GupshupAdapter(_gupshup_app())

    with _WaLogCapture() as log:
        modes = adapter._map_event_types_to_gupshup_modes(["MESSAGE", "FLOWS_MESSAGE", "STATUS"])

    assert "MESSAGE" in modes and "DELIVERED" in modes, "the mappable event types must survive an unmappable one"
    assert "FLOWS_MESSAGE" not in modes, "an unmapped event type must not be passed off as a Gupshup mode"
    SubscriptionFormData(modes=modes, tag="t", url="https://hooks.example.test/h/")

    assert "FLOWS_MESSAGE" in log.text, "dropping an event type silently is the other half of this trap"
    assert "EVENT_TYPE_TO_MODES" in log.text, "the warning must say where to fix it"


def test_a_subscription_with_nothing_mappable_is_refused_rather_than_defaulted():
    """Dropping every requested type is a failure, not a partial success.

    ``register_webhook`` falls back to ``MESSAGE``/``ALL`` for a subscription
    that names no event types at all. Letting a subscription whose every type
    was dropped land in that same branch would register something nobody asked
    for and report it as success — the shape of silence this path keeps being
    bitten by. So it is refused, naming what could not be mapped, and no request
    is made.

    The partner double is told to *succeed*, deliberately: without the guard this
    registration goes through, reports ``ACTIVE`` and returns success, and it is
    that false success the assertions below have to be the ones to catch.
    """
    from wa.adapters.gupshup import GupshupAdapter

    app = _gupshup_app()
    subscription = WASubscription.objects.create(
        wa_app=app,
        webhook_url=webhook_identity.callback_url(app),
        event_types=["UNKNOWN"],
        status=SubscriptionStatus.PENDING,
    )

    api = _gupshup_api_double(app)

    with patch.object(GupshupAdapter, "_get_subscription_api", return_value=api):
        result = GupshupAdapter(app).register_webhook(subscription)

    api.create_subscription.assert_not_called()
    assert not result.success, "a subscription we could map nothing from must not report success"
    assert "UNKNOWN" in result.error_message

    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.FAILED
    assert "UNKNOWN" in subscription.error_message


# ─────────────────────────────────────────────────────────────────────────────
# The blast radius: the two refresh surfaces, not just app creation
#
# ``register_webhook`` is also reached from the ``/refresh/`` endpoint behind the
# frontend's *Refresh Webhooks* button and from the admin *Reset & re-register
# webhooks* action. Both build ``event_types`` from the whole
# ``WebhookEventType`` enum — ``UNKNOWN`` included, which #348's narrower fix
# only removed from the auto-registration task — and both then hand it to the
# adapter. Under the old passthrough those two buttons carried the same refused
# payload, reported to an operator as "re-registration failed", with the mapping
# table nowhere in sight and the app's previous subscription already purged.
#
# Both go through the real ``GupshupAdapter``, mocked only at
# ``_get_subscription_api`` — its credential seam — so the mapping, the pydantic
# validation and the form body are all production code.
# ─────────────────────────────────────────────────────────────────────────────


def _gupshup_api_double(app):
    api = MagicMock()
    api.appId = app.app_id
    api.get_all_subscriptions.return_value = {"subscriptions": []}
    api.get_subscriptions.return_value = {"subscriptions": []}
    api.delete_all_subscriptions.return_value = {"status": "success", "deleted_count": 0}
    api.create_subscription.return_value = {"status": "success", "subscription": {"id": 777}}
    return api


def _api_client_for(owner):
    """An authenticated client with ``wa_app.manage``, which ``/refresh/`` requires."""
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIClient

    from tenants.models import TenantRole, TenantUser

    suffix = uuid.uuid4().hex[:8]
    user = get_user_model().objects.create_user(
        username=f"refresh_{suffix}",
        email=f"refresh_{suffix}@test.invalid",
        mobile=f"+9190{uuid.uuid4().int % 10**8:08d}",
        password="testpass123",  # noqa: S106 — throwaway test login
    )
    TenantUser.objects.create(tenant=owner, user=user, role=TenantRole.objects.get(tenant=owner, slug="owner"))

    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _refresh_via_endpoint(app):
    """The ``/refresh/`` endpoint behind the frontend's Refresh Webhooks button."""
    response = _api_client_for(app.tenant).post("/wa/v2/subscriptions/refresh/", {"wa_app": str(app.pk)}, format="json")
    assert response.status_code == 200, response.data


def _refresh_via_admin_action(app):
    """The admin *Reset & re-register webhooks* action on the WA app list."""
    from django.contrib.admin.sites import AdminSite
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    from tenants.admin import TenantWAAppAdmin
    from tenants.models import TenantWAApp

    request = RequestFactory().post("/admin/")
    request.session = {}
    request._messages = FallbackStorage(request)

    TenantWAAppAdmin(TenantWAApp, AdminSite()).reset_and_register_webhooks(
        request, TenantWAApp.objects.filter(pk=app.pk)
    )


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
@pytest.mark.parametrize(
    "refresh",
    [_refresh_via_endpoint, _refresh_via_admin_action],
    ids=["refresh-endpoint", "admin-action"],
)
def test_a_refresh_of_every_event_type_survives_gupshups_payload_validation(refresh):
    """Both refresh surfaces ask for the full enum, ``UNKNOWN`` and all.

    A stored row holding an event type Gupshup has no mode for used to fail here
    exactly as it failed on app creation — and worse, because a refresh purges
    the app's existing subscriptions first: an operator pressed a button
    labelled *Refresh Webhooks* and was left with an app subscribed to nothing.
    """
    from wa.adapters.gupshup import GupshupAdapter
    from wa.models import WebhookEventType

    app = _gupshup_app()
    api = _gupshup_api_double(app)

    with patch.object(GupshupAdapter, "_get_subscription_api", return_value=api):
        refresh(app)

    subscription = WASubscription.objects.get(wa_app=app)
    assert subscription.status == SubscriptionStatus.ACTIVE, subscription.error_message
    assert "UNKNOWN" in subscription.event_types, (
        "this test is only worth something while the refresh paths really do store an unsubscribable type — "
        "if they stop, re-point it at whatever they store instead"
    )

    api.create_subscription.assert_called_once()
    sent = api.create_subscription.call_args[0][0]["modes"].split(",")
    assert "UNKNOWN" not in sent
    assert set(sent) >= {"MESSAGE", "ALL", "PAYMENTS", "TEMPLATE", "ACCOUNT", "BILLING", "DELIVERED"}, sent

    mappable = [et.value for et in WebhookEventType if et.value not in GupshupAdapter.UNSUPPORTED_EVENT_TYPES]
    assert set(sent) == set(GupshupAdapter(app)._map_event_types_to_gupshup_modes(mappable)), (
        "a refresh must subscribe to everything it can, not to whatever happened to survive"
    )


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
