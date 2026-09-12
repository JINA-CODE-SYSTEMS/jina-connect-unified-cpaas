"""Which callback URL this deployment registers with a BSP (#334).

Two surfaces disagreed, in the same header in Settings → WhatsApp. The Webhook
Setup screen hands a client their **per-app** URL —
``/wa/v2/webhooks/<bsp>/<webhook_identifier>/`` — to paste into their own BSP
dashboard. Every registration path in the codebase sent the BSP the **legacy
deployment-wide** one, ``/wa/v2/webhooks/<bsp>/``. So a client pastes ours,
someone later presses "Refresh Webhooks" — a button that reads as routine
maintenance — and the deployment re-registers a different path. Which one wins is
the BSP's business; inbound messages stopping while both sides look correctly
configured is the failure #310 exists to prevent.

Resolution taken: **registration follows the per-app URL** (#334's option 1). It
is the smaller surface and it is safe because the legacy path stays served
forever (#310), so nothing that has already registered it breaks — which
``test_legacy_callback_url.py`` is the standing check on.

What is pinned here is the agreement itself, through each real caller rather than
through the helper: a test that only compared two helpers would pass just as
happily if a caller kept choosing for itself, and that is exactly the regression
this file is about — four callers each picked their own URL before #334.

HOW TO RUN:
    DB_NAME=jc307 python -m pytest wa/tests/test_registered_callback_url.py -v
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from tenants.models import BSPChoices
from wa.services import webhook_identity

BASE = "https://hooks.example.test"

User = get_user_model()

_mobile_seq = itertools.count(1)

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _tenant(prefix: str = "RegisteredURL"):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{prefix}-{uuid.uuid4().hex[:8]}", is_active=True)


def _wa_app(tenant=None, bsp=BSPChoices.META, **overrides):
    """A ``TenantWAApp`` whose ``bsp`` column is exactly *bsp*.

    Written through ``update`` as well as ``create`` for the blank case, the same
    way ``test_legacy_callback_url._wa_app`` does it: the column has a default and
    ``save()`` would supply one, and what matters for a blank-BSP app is the
    behaviour of a row that really does hold ``""``.
    """
    from wa.models import WAApp

    suffix = uuid.uuid4().hex[:8]
    fields = {
        "tenant": tenant or _tenant(),
        "app_name": f"reg-url-{suffix}",
        "app_id": f"gs_{suffix}",
        "app_secret": f"secret_{suffix}",  # noqa: S106 — test fixture
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "bsp": bsp,
    }
    fields.update(overrides)
    app = WAApp.objects.create(**fields)
    WAApp.objects.filter(pk=app.pk).update(bsp=bsp)
    app.refresh_from_db()
    assert app.bsp == bsp
    return app


def _api_client_for(tenant, role_slug: str = "owner") -> APIClient:
    from tenants.models import TenantRole, TenantUser

    role = TenantRole.objects.get(tenant=tenant, slug=role_slug)
    user = User.objects.create_user(
        username=f"regurl_{role_slug}_{uuid.uuid4().hex[:8]}",
        email=f"regurl_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190003{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test login
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role)

    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _per_app_url(app, bsp=BSPChoices.META) -> str:
    segment = "meta" if bsp == BSPChoices.META else "gupshup"
    return f"{BASE}/wa/v2/webhooks/{segment}/{app.webhook_identifier}/"


class _FakeResult:
    """Stands in for ``AdapterResult``, which is all these callers read of it."""

    success = True
    error_message = ""
    data = {"deleted_count": 0}  # noqa: RUF012 — mirrors the real attribute


class _FakeAdapter:
    """Succeeds at everything and records nothing.

    The BSP round trip is not under test: what is under test is which URL the
    caller *chose* before it got this far. A real adapter would want network
    credentials and would fail a blank-BSP app for unrelated reasons.
    """

    def __init__(self, wa_app=None):
        self.wa_app = wa_app

    def purge_all_webhooks(self):
        return _FakeResult()

    def register_webhook(self, subscription):
        return _FakeResult()


@pytest.fixture()
def _fake_adapters(monkeypatch):
    """Every caller resolves its adapter through ``wa.adapters``."""
    import wa.adapters

    monkeypatch.setattr(wa.adapters, "get_bsp_adapter", lambda wa_app: _FakeAdapter(wa_app))


# ─────────────────────────────────────────────────────────────────────────────
# The helper: one answer to "which URL do we register"
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
@pytest.mark.parametrize("bsp", [BSPChoices.META, BSPChoices.GUPSHUP, ""], ids=["meta", "gupshup", "blank-is-meta"])
def test_the_registration_url_is_the_apps_own_url(bsp):
    """Including for a blank column, which means META everywhere else (#265)."""
    app = _wa_app(bsp=bsp)
    expected_bsp = bsp or BSPChoices.META

    registered = webhook_identity.registration_callback_url(app)

    assert registered == _per_app_url(app, bsp=expected_bsp)
    assert registered == webhook_identity.callback_url(app)
    assert registered != webhook_identity.legacy_callback_url(app)


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_the_registered_url_is_one_a_receiver_actually_serves(client):
    """Two matching strings prove nothing if both are wrong, so the URL is
    POSTed to and required not to 404. A signature rejection (200 with a reason,
    per #306) is the expected answer for an unsigned body — what matters is that
    something is listening."""
    for bsp in (BSPChoices.META, BSPChoices.GUPSHUP, ""):
        url = webhook_identity.registration_callback_url(_wa_app(bsp=bsp))
        path = url[len(BASE) :]
        response = client.post(path, data="{}", content_type="application/json")
        assert response.status_code != 404, f"{path} is registered with no receiver"


# ─────────────────────────────────────────────────────────────────────────────
# Through each real caller, which is the part the helper cannot prove
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_the_refresh_endpoint_registers_the_per_app_url(_fake_adapters):
    """The endpoint behind the Refresh Webhooks button #334 is named after."""
    from wa.models import WASubscription

    tenant = _tenant()
    app = _wa_app(tenant)
    api = _api_client_for(tenant)

    response = api.post("/wa/v2/subscriptions/refresh/", {"wa_app": str(app.pk)}, format="json")

    assert response.status_code == 200, response.data
    assert WASubscription.objects.get(wa_app=app).webhook_url == _per_app_url(app)


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_an_explicit_webhook_url_still_wins_on_refresh(_fake_adapters):
    """The override is untouched: a caller who names a URL gets that URL.

    #334 is about the *default*, which is what the button sends. Changing the
    override too would take away the only way to register something else.
    """
    from wa.models import WASubscription

    tenant = _tenant()
    app = _wa_app(tenant)
    api = _api_client_for(tenant)

    chosen = "https://client.example.test/their/own/receiver/"
    response = api.post(
        "/wa/v2/subscriptions/refresh/",
        {"wa_app": str(app.pk), "webhook_url": chosen},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert WASubscription.objects.get(wa_app=app).webhook_url == chosen


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_the_tenants_admin_action_registers_the_per_app_url(_fake_adapters, rf):
    """The second "Reset & re-register webhooks" action, on the app list."""
    from django.contrib.admin.sites import AdminSite
    from django.contrib.messages.storage.fallback import FallbackStorage

    from tenants.admin import TenantWAAppAdmin
    from tenants.models import TenantWAApp
    from wa.models import WASubscription

    app = _wa_app()

    request = rf.post("/admin/")
    request.session = {}
    request._messages = FallbackStorage(request)

    admin = TenantWAAppAdmin(TenantWAApp, AdminSite())
    admin.reset_and_register_webhooks(request, TenantWAApp.objects.filter(pk=app.pk))

    assert WASubscription.objects.get(wa_app=app).webhook_url == _per_app_url(app)


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_the_gupshup_auto_register_task_registers_the_per_app_url(_fake_adapters):
    """A newly created Gupshup app registers the same URL its own setup screen
    shows, rather than the shared one it used to."""
    from wa.models import WASubscription
    from wa.tasks import auto_register_gupshup_webhook

    app = _wa_app(bsp=BSPChoices.GUPSHUP)
    WASubscription.objects.filter(wa_app=app).delete()

    auto_register_gupshup_webhook(app.pk)

    subscription = WASubscription.objects.filter(wa_app=app).latest("created_at")
    assert subscription.webhook_url == _per_app_url(app, bsp=BSPChoices.GUPSHUP)


# ─────────────────────────────────────────────────────────────────────────────
# The property, end to end: the two surfaces cannot disagree
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_the_url_the_client_is_told_to_paste_is_the_url_we_register(_fake_adapters):
    """#334 in one assertion, over the two surfaces that sit in one header.

    Both halves are read the way a person reads them: the setup endpoint's
    payload, and the row a refresh leaves behind.
    """
    from wa.models import WASubscription

    tenant = _tenant()
    app = _wa_app(tenant)
    api = _api_client_for(tenant)

    handed_to_client = api.get(f"/wa/v2/apps/{app.pk}/webhook-setup/").data["callback_url"]
    api.post("/wa/v2/subscriptions/refresh/", {"wa_app": str(app.pk)}, format="json")
    registered = WASubscription.objects.get(wa_app=app).webhook_url

    assert registered == handed_to_client


# ─────────────────────────────────────────────────────────────────────────────
# A BSP with no receiver of its own (#334's second finding)
# ─────────────────────────────────────────────────────────────────────────────


def test_a_bsp_with_no_receiver_can_be_asked_about():
    """``callback_url`` answers with a borrowed path for a BSP that has no
    receiver, because an exception at setup time would be a 500 on an unrelated
    screen. That makes the URL not self-validating — so the question has its own
    answer rather than only a URL that cannot be checked."""
    assert webhook_identity.has_receiver(BSPChoices.META)
    assert webhook_identity.has_receiver(BSPChoices.GUPSHUP)
    assert not webhook_identity.has_receiver(BSPChoices.TWILIO)


def test_a_borrowed_receiver_path_is_not_silent():
    """It still answers, and it says so. "Wrong is better than absent" only
    holds while somebody can find out which one they got — the fallback is
    invisible in the URL to anyone who does not already know the registry.

    Listens on the ``wa`` logger directly: the project's LOGGING config sets
    ``propagate=False`` there, so ``caplog`` alone sees nothing.
    """
    import logging

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.DEBUG)
            self.lines: list[str] = []

        def emit(self, record):
            self.lines.append(record.getMessage())

    app = _wa_app(bsp=BSPChoices.TWILIO)

    handler = _Capture()
    wa_logger = logging.getLogger("wa")
    wa_logger.addHandler(handler)
    try:
        url = webhook_identity.callback_url(app)
    finally:
        wa_logger.removeHandler(handler)

    logged = "\n".join(handler.lines)
    assert "/wa/v2/webhooks/gupshup/" in url
    assert "no receiver is registered" in logged
    assert BSPChoices.TWILIO in logged


@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_the_legacy_path_keeps_answering_after_the_move(client):
    """Why option 1 is safe: the URL anyone has already registered still works.

    The move only changes what *this deployment* registers from now on. A
    delivery to the legacy path is still accepted (the 200 here is #306's
    signature rejection for an unsigned body, not a 404), which is the
    backwards-compatibility requirement #310 took on.
    """
    app = _wa_app()
    legacy_path = webhook_identity.legacy_callback_url(app)[len(BASE) :]

    assert legacy_path == "/wa/v2/webhooks/meta/"
    assert client.post(legacy_path, data="{}", content_type="application/json").status_code == 200
