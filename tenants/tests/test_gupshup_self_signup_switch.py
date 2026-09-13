"""A deployment may close Gupshup signup without stranding anyone.

``GUPSHUP_SELF_SIGNUP_ENABLED`` decides whether an organisation with no WhatsApp
Business Account may apply for one through Gupshup's Embedded Signup from inside
the product. A deployment that resells its own Meta apps, or that onboards
clients bringing their own, switches it off; the open-source default leaves it
on, because it is the only self-serve route to a WABA that exists today.

**The line this file defends is not "Gupshup is off".** It is that switching it
off refuses exactly the two actions that MINT something new — a Gupshup app, an
ESF URL — and nothing else. An organisation that is already part-way through
signup when the switch is thrown must still be able to read its own ESF status
and still complete activation, and every app that already exists must go on
sending, receiving and billing. A switch that stranded a live customer would not
be a configuration option, it would be an outage. Most of the tests below are
about what stays working.

The refusal is also *legible*: 403 with a stable ``code``, because three
different things can refuse this same button — the deployment, the caller's
role, and Gupshup itself — and each wants something different said to the user.

No network: the only test that lets creation through stubs the two Gupshup calls
the same way ``test_esf_app_bsp.py`` does.

HOW TO RUN:
    DB_NAME=... python -m pytest tenants/tests/test_gupshup_self_signup_switch.py -v
"""

from __future__ import annotations

import itertools
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tenants.services.onboarding_routes import (
    CLIENT_META_APP,
    GUPSHUP_EMBEDDED_SIGNUP,
    SELF_SIGNUP_DISABLED_CODE,
)

User = get_user_model()

_mobile_seq = itertools.count(1)

CREATE_APP_URL = "/tenants/tenant-gupshup/create-app/"
ONBOARDING_OPTIONS_URL = "/wa/v2/apps/onboarding-options/"


def _esf_status_url(app_id) -> str:
    return f"/tenants/tenant-gupshup/{app_id}/esf-url-status/"


def _generate_esf_url(app_id) -> str:
    return f"/tenants/tenant-gupshup/{app_id}/generate-esf-url/"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _tenant(tag: str = "org"):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{tag}-{uuid.uuid4().hex[:6]}", is_active=True)


def _user(**kwargs):
    return User.objects.create_user(
        username=f"signup_{uuid.uuid4().hex[:8]}",
        email=f"signup_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190011{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test credential
        **kwargs,
    )


def _client_for(tenant, role_slug: str = "owner"):
    from tenants.models import TenantRole, TenantUser

    role = TenantRole.objects.get(tenant=tenant, slug=role_slug)
    user = _user()
    TenantUser.objects.create(tenant=tenant, user=user, role=role)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _gupshup_app(tenant):
    """An app of the kind ESF produces, created directly — no network."""
    from tenants.models import TenantWAApp

    return TenantWAApp.objects.create(
        tenant=tenant,
        app_name=f"gs-{uuid.uuid4().hex[:6]}",
        app_id=f"gsid-{uuid.uuid4().hex[:8]}",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        bsp="GUPSHUP",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Off: the two minting actions, and only those, are refused
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_creating_a_gupshup_app_is_refused_when_self_signup_is_off(settings):
    """The headline. 403, not 404 — the route exists, this deployment has closed
    it, and a client that cannot tell those apart will retry a URL it believes it
    got wrong."""
    from tenants.models import TenantWAApp

    settings.GUPSHUP_SELF_SIGNUP_ENABLED = False
    tenant = _tenant()

    response = _client_for(tenant, "owner").post(CREATE_APP_URL, {}, format="json")

    assert response.status_code == 403, response.data
    assert response.data["code"] == SELF_SIGNUP_DISABLED_CODE
    # Nothing half-made left behind for a later sync to pick up.
    assert TenantWAApp.objects.filter(tenant=tenant).count() == 0


@pytest.mark.django_db
def test_minting_a_fresh_esf_url_is_refused_too(settings):
    """Refusing only ``create-app`` would leave the back door open: an
    organisation that already holds an app from before the switch could keep
    minting four-day signup links from it indefinitely."""
    settings.GUPSHUP_SELF_SIGNUP_ENABLED = False
    tenant = _tenant()
    app = _gupshup_app(tenant)

    response = _client_for(tenant, "owner").post(_generate_esf_url(app.app_id), {}, format="json")

    assert response.status_code == 403, response.data
    assert response.data["code"] == SELF_SIGNUP_DISABLED_CODE


@pytest.mark.django_db
def test_the_refusal_says_what_to_do_instead(settings):
    """A dead end is not a product decision. The message has to name the route
    that *is* open, because the user reading it has no other way to find out."""
    settings.GUPSHUP_SELF_SIGNUP_ENABLED = False
    tenant = _tenant()

    response = _client_for(tenant, "owner").post(CREATE_APP_URL, {}, format="json")

    detail = response.data["error"].lower()
    assert "meta" in detail, detail
    assert "administrator" in detail or "admin" in detail, detail


# ─────────────────────────────────────────────────────────────────────────────
# 2. Off: what must keep working, which is the part that matters
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_existing_app_can_still_read_its_own_esf_status(settings):
    """Throwing the switch mid-signup must not blind an organisation to the
    state it is already in."""
    settings.GUPSHUP_SELF_SIGNUP_ENABLED = False
    tenant = _tenant()
    app = _gupshup_app(tenant)

    response = _client_for(tenant, "owner").get(_esf_status_url(app.app_id))

    assert response.status_code == 200, response.data
    assert "has_esf_url" in response.data


@pytest.mark.django_db
def test_an_existing_gupshup_app_is_untouched(settings):
    """The switch is about onboarding, not about Gupshup. An app that exists
    keeps its BSP, its adapter and therefore its ability to send and receive."""
    from wa.adapters import get_bsp_adapter
    from wa.adapters.gupshup import GupshupAdapter

    settings.GUPSHUP_SELF_SIGNUP_ENABLED = False
    app = _gupshup_app(_tenant())

    assert isinstance(get_bsp_adapter(app), GupshupAdapter)


@pytest.mark.django_db
def test_bringing_your_own_meta_app_is_unaffected(settings):
    """The route the switch exists to steer people towards has to be open, or
    turning it off just removes onboarding altogether."""
    from wa.models import WAApp

    settings.GUPSHUP_SELF_SIGNUP_ENABLED = False
    tenant = _tenant()

    response = _client_for(tenant, "owner").post(
        "/wa/v2/apps/",
        {
            "tenant": tenant.id,
            "app_name": f"byo-{uuid.uuid4().hex[:6]}",
            "phone_number": f"+1{uuid.uuid4().int % 10**10:010d}",
            "app_id": "1234567890",
            "bsp": "META",
            "waba_id": "1234567890",
            "phone_number_id": "9876543210",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert WAApp.objects.get(pk=response.data["id"]).tenant_id == tenant.id


# ─────────────────────────────────────────────────────────────────────────────
# 3. On: the default, and the open-source behaviour it preserves
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_creation_is_allowed_when_self_signup_is_on(settings):
    """The guard must be a guard and not a removal. With the switch on, the
    request reaches ``ESFService`` exactly as it always did."""
    settings.GUPSHUP_SELF_SIGNUP_ENABLED = True
    tenant = _tenant()

    with patch("tenants.viewsets.tenant_gupshup.ESFService.create_app_for_tenant") as create:
        create.return_value = {"app_id": "gs-1", "esf_url": "https://example.invalid/esf"}
        response = _client_for(tenant, "owner").post(CREATE_APP_URL, {}, format="json")

    assert response.status_code == 201, response.data
    assert create.called


@pytest.mark.django_db
def test_a_settings_module_predating_the_switch_keeps_todays_behaviour(settings):
    """Read through a defaulting getattr on purpose. An older private deployment,
    or test settings built by copying one, must not silently lose its only
    self-serve onboarding route because a new name was introduced upstream."""
    from tenants.services.onboarding_routes import gupshup_self_signup_enabled

    del settings.GUPSHUP_SELF_SIGNUP_ENABLED

    assert gupshup_self_signup_enabled() is True


# ─────────────────────────────────────────────────────────────────────────────
# 4. The client is told, rather than deciding for itself
# ─────────────────────────────────────────────────────────────────────────────


def _route(payload, key):
    return next(r for r in payload["routes"] if r["key"] == key)


@pytest.mark.django_db
def test_the_options_endpoint_reports_gupshup_closed(settings):
    """#310's lesson, applied ahead of time: if the client decides which doors
    exist, the first deployment to close one shows a button that 403s."""
    settings.GUPSHUP_SELF_SIGNUP_ENABLED = False
    tenant = _tenant()

    response = _client_for(tenant, "owner").get(ONBOARDING_OPTIONS_URL)

    assert response.status_code == 200, response.data
    gupshup = _route(response.data, GUPSHUP_EMBEDDED_SIGNUP)
    assert gupshup["available"] is False
    assert gupshup["reason"] == "disabled_by_platform"
    assert _route(response.data, CLIENT_META_APP)["available"] is True


@pytest.mark.django_db
def test_the_options_endpoint_reports_gupshup_open(settings):
    settings.GUPSHUP_SELF_SIGNUP_ENABLED = True
    tenant = _tenant()

    response = _client_for(tenant, "owner").get(ONBOARDING_OPTIONS_URL)

    assert response.status_code == 200, response.data
    gupshup = _route(response.data, GUPSHUP_EMBEDDED_SIGNUP)
    assert gupshup["available"] is True
    assert gupshup["reason"] is None


@pytest.mark.django_db
def test_what_the_endpoint_says_is_what_the_endpoint_does(settings):
    """The two halves of the contract, asserted against each other in one test.

    Separate tests for "reports closed" and "refuses" would both keep passing if
    one of them were later changed to read a different switch. This is the one
    that fails when they disagree.
    """
    for enabled, expected_status in ((True, 201), (False, 403)):
        settings.GUPSHUP_SELF_SIGNUP_ENABLED = enabled
        tenant = _tenant()
        api = _client_for(tenant, "owner")

        advertised = _route(api.get(ONBOARDING_OPTIONS_URL).data, GUPSHUP_EMBEDDED_SIGNUP)["available"]

        with patch("tenants.viewsets.tenant_gupshup.ESFService.create_app_for_tenant") as create:
            create.return_value = {"app_id": "gs-1", "esf_url": "https://example.invalid/esf"}
            actual = api.post(CREATE_APP_URL, {}, format="json")

        assert advertised is enabled
        assert actual.status_code == expected_status, actual.data


# ─────────────────────────────────────────────────────────────────────────────
# 5. can_manage — the field that stops #310's permission bug recurring
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_every_role_may_read_the_options():
    """Gated on ``wa_app.view``, deliberately. A viewer who cannot act still
    needs to be told what the options are and who to ask, rather than shown an
    empty page — and the alternative, gating the screen on ``manage``, is how
    three roles came to see a link that refused them."""
    tenant = _tenant()

    for role_slug in ("owner", "admin", "manager", "agent", "viewer"):
        response = _client_for(tenant, role_slug).get(ONBOARDING_OPTIONS_URL)
        assert response.status_code == 200, f"{role_slug}: {response.data}"


@pytest.mark.django_db
def test_can_manage_tracks_the_permission_the_write_actually_requires():
    """The whole point of returning it: the client must not recompute this.

    Owner and admin hold ``wa_app.manage``; manager, agent and viewer do not.
    A client that assumed "can read the options" implies "can act" reproduces
    #310 exactly.
    """
    tenant = _tenant()

    for role_slug in ("owner", "admin"):
        assert _client_for(tenant, role_slug).get(ONBOARDING_OPTIONS_URL).data["can_manage"] is True, role_slug

    for role_slug in ("manager", "agent", "viewer"):
        assert _client_for(tenant, role_slug).get(ONBOARDING_OPTIONS_URL).data["can_manage"] is False, role_slug


@pytest.mark.django_db
def test_can_manage_is_true_for_the_platform_operator_of_353():
    """The caller the platform-admin mount point is built for holds no
    membership at all, so a role lookup alone answers False for the one person
    who may certainly act."""
    api = APIClient()
    api.force_authenticate(user=_user(is_superuser=True, is_staff=True))

    assert api.get(ONBOARDING_OPTIONS_URL).data["can_manage"] is True


@pytest.mark.django_db
def test_can_manage_is_false_for_an_impersonated_session():
    """Impersonation is read-only (#300). A superuser bypasses RBAC, so without
    an explicit check this would advertise a create button to a session that is
    refused at two layers."""
    from users.impersonation import issue_impersonation_token

    org = _tenant("viewed")
    raw, _session = issue_impersonation_token(_user(is_superuser=True, is_staff=True), org)

    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

    response = api.get(ONBOARDING_OPTIONS_URL)

    assert response.status_code == 200, response.data
    assert response.data["can_manage"] is False
