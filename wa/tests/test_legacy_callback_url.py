"""The legacy deployment-wide callback URL, pinned per BSP.

Three places built this string from their own copy of a two-entry dict —
``wa.admin``, ``tenants.admin`` and the v2 subscription viewset — and all three
keyed it on the **raw** ``bsp`` column::

    {"GUPSHUP": …, "META": …}.get(wa_app.bsp, "/wa/v2/webhooks/gupshup/")

``wa.adapters.resolve_bsp`` is explicit that this is the wrong question to ask:
a blank column is not "no BSP", it is ``DEFAULT_BSP``, which is META. So a
blank-``bsp`` app — META to the adapter factory, to the send paths and, since
#265, to the webhook receiver — was handed the *Gupshup* receiver URL by all
three, and would have registered a callback that answers its own deliveries
with ``unknown_app``.

These tests pin the output rather than the implementation, because the point of
the change is that two of the three answers must not move and the third must.
``test_a_blank_bsp_app_gets_the_meta_path`` is the one that would have failed
before it; the META and Gupshup cases are here so that a regression in the
shared helper cannot hide behind the case it fixed.

Those callers have since moved to the per-app URL (#334,
``webhook_identity.registration_callback_url``), which
``test_registered_callback_url.py`` pins. This file stays as the check on the
half that must *not* move: the legacy path is still composed in one place, still
resolves through ``resolve_bsp``, and is still served — it is the URL live
deployments already registered, and that is exactly what made moving
registration safe.

HOW TO RUN:
    DB_NAME=jc307 python -m pytest wa/tests/test_legacy_callback_url.py -v
"""

from __future__ import annotations

import uuid

import pytest
from django.test import override_settings

from wa.services import webhook_identity

BASE = "https://hooks.example.test"

META_URL = f"{BASE}/wa/v2/webhooks/meta/"
GUPSHUP_URL = f"{BASE}/wa/v2/webhooks/gupshup/"


def _wa_app(bsp):
    """A ``TenantWAApp`` whose ``bsp`` column is exactly *bsp*.

    Written through ``update`` after creation for the blank case: the column
    has a default and ``save()`` would supply it, and what is under test is
    the behaviour of a row that really does hold an empty string — which is
    what every app created before the column existed holds.
    """
    from tenants.models import Tenant, TenantWAApp

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant.objects.create(name=f"LegacyURL-{suffix}")
    app = TenantWAApp.objects.create(
        tenant=tenant,
        app_name=f"legacy-url-{suffix}",
        app_id=f"gs_{suffix}",
        app_secret=f"secret_{suffix}",  # noqa: S106 — test fixture
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        bsp=bsp,
    )
    TenantWAApp.objects.filter(pk=app.pk).update(bsp=bsp)
    app.refresh_from_db()
    assert app.bsp == bsp, "the fixture did not store the column it was asked to"
    return app


# ─────────────────────────────────────────────────────────────────────────────
# The two answers that must not change
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db()
@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_a_meta_app_gets_the_meta_path():
    from tenants.models import BSPChoices

    assert webhook_identity.legacy_callback_url(_wa_app(BSPChoices.META)) == META_URL


@pytest.mark.django_db()
@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_a_gupshup_app_gets_the_gupshup_path():
    from tenants.models import BSPChoices

    assert webhook_identity.legacy_callback_url(_wa_app(BSPChoices.GUPSHUP)) == GUPSHUP_URL


# ─────────────────────────────────────────────────────────────────────────────
# The answer that changes — to the correct one
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db()
@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_a_blank_bsp_app_gets_the_meta_path():
    """The defect, closed. Blank means META, so blank must register META's receiver.

    Before this change all three callers produced ``GUPSHUP_URL`` here, because
    ``dict.get(wa_app.bsp, gupshup)`` misses on an empty string and falls back.
    """
    app = _wa_app("")

    from tenants.models import BSPChoices
    from wa.adapters import resolve_bsp

    assert resolve_bsp(app) == BSPChoices.META, "premise: a blank column resolves to META"
    assert webhook_identity.legacy_callback_url(app) == META_URL


@pytest.mark.django_db()
@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
def test_the_registered_url_is_one_a_receiver_actually_serves(client):
    """The string is not merely stable, it resolves to the right view.

    A test comparing two hardcoded strings would pass just as happily if both
    were wrong, so each URL is POSTed to and required not to 404. A signature
    rejection (200 with a reason, per #306) is the expected answer from the
    Meta receiver for an unsigned body — what matters is that something is
    listening at all.
    """
    from tenants.models import BSPChoices

    for bsp in (BSPChoices.META, BSPChoices.GUPSHUP, ""):
        url = webhook_identity.legacy_callback_url(_wa_app(bsp))
        path = url[len(BASE) :]
        response = client.post(path, data="{}", content_type="application/json")
        assert response.status_code != 404, f"{path} is registered with no receiver"


# ─────────────────────────────────────────────────────────────────────────────
# The callers no longer hold a copy of the table
# ─────────────────────────────────────────────────────────────────────────────


def test_no_caller_still_hardcodes_the_receiver_paths():
    """The point of the change, asserted where it can regress.

    Pinning the URLs above does not stop a fourth copy of the dict appearing —
    a new caller would simply produce the same two strings and the same wrong
    answer for a blank column. This reads the source of the three that had one.
    """
    import inspect

    from tenants import admin as tenants_admin
    from wa import admin as wa_admin
    from wa.viewsets import wa_subscription_v2

    for module in (wa_admin, tenants_admin, wa_subscription_v2):
        source = inspect.getsource(module)
        assert '"/wa/v2/webhooks/gupshup/"' not in source, (
            f"{module.__name__} hardcodes a receiver path again — use webhook_identity.legacy_callback_url()"
        )
        assert '"/wa/v2/webhooks/meta/"' not in source, (
            f"{module.__name__} hardcodes a receiver path again — use webhook_identity.legacy_callback_url()"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Through a real caller, which is what the URLs above do not prove
# ─────────────────────────────────────────────────────────────────────────────
#
# The three assertions above pin the *helper*. They would pass unchanged if a
# caller quietly kept its own copy of the dict, which is exactly the regression
# this change is about — and when the revert check put the dict back into
# ``wa.admin``, those three tests all still passed. Only the source-inspection
# test failed, and a source-inspection test cannot see a fourth caller written
# from scratch.
#
# So this drives the real admin action and asserts the ``webhook_url`` that ends
# up on the row, which is the string that actually gets registered with a BSP.
#
# What that string is changed with #334: the action now registers the app's own
# per-app URL, the same one the client is told to paste, instead of the legacy
# shared path. The per-app URL is still BSP-specific, so this keeps doing the job
# it was written for — a blank column must resolve to META's receiver and not
# Gupshup's — on the URL the caller actually registers today.


class _FakeResult:
    """Stands in for ``AdapterResult``, which is all the action reads of it."""

    success = True
    error_message = ""
    data = {"deleted_count": 0}  # noqa: RUF012 — mirrors the real attribute


class _FakeAdapter:
    """Records nothing and succeeds at everything.

    The BSP round trip is not under test here: what is under test is which URL
    the caller *chose* before it got this far. A real adapter would need network
    credentials and would fail on a blank-BSP app for unrelated reasons.
    """

    def purge_all_webhooks(self):
        return _FakeResult()

    def register_webhook(self, subscription):
        return _FakeResult()


@pytest.mark.django_db()
@override_settings(DEFAULT_WEBHOOK_BASE_URL=BASE)
@pytest.mark.parametrize(
    ("bsp", "receiver"),
    [("META", "meta"), ("GUPSHUP", "gupshup"), ("", "meta")],
    ids=["meta", "gupshup", "blank-is-meta"],
)
def test_the_admin_refresh_registers_the_right_url(bsp, receiver, monkeypatch, rf):
    """The URL stored on the subscription the admin action creates.

    ``""`` is the case this was written for: a blank column is META everywhere
    else, so it must not be handed Gupshup's receiver. Since #334 the action
    registers the *per-app* path, which is BSP-specific in the same way, so the
    blank case is still the one that would regress.
    """
    from django.contrib.admin.sites import AdminSite
    from django.contrib.messages.storage.fallback import FallbackStorage

    import wa.adapters
    from wa.admin import WASubscriptionAdmin
    from wa.models import WASubscription

    monkeypatch.setattr(wa.adapters, "get_bsp_adapter", lambda wa_app: _FakeAdapter())

    app = _wa_app(bsp)

    request = rf.post("/admin/")
    request.session = {}
    request._messages = FallbackStorage(request)

    WASubscriptionAdmin(WASubscription, AdminSite())._do_refresh_for_apps(request, [app.pk])

    sub = WASubscription.objects.get(wa_app=app)
    assert sub.webhook_url == f"{BASE}/wa/v2/webhooks/{receiver}/{app.webhook_identifier}/"
