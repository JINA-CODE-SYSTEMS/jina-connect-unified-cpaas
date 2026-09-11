"""Per-tenant META access tokens (#275).

``WAAppSerializer`` declared ``bsp_credentials`` write-only in ``extra_kwargs``
but never listed it in ``Meta.fields``, so DRF dropped the entry on the floor:
the field could not be set through the v2 API at all. It read like working
configuration and did nothing, and every META send fell back to the one global
``settings.META_PERM_TOKEN`` — one credential's health, one rate limit and one
rotation event for every tenant on the platform.

The read side already preferred ``bsp_credentials["access_token"]`` everywhere,
so the properties worth pinning are the ones the missing field broke:

* a token set through the API actually lands, and the adapter then uses it in
  preference to the global one,
* rotating one tenant's token leaves another's alone,
* the token never comes back out in a response body — on the v2 endpoint or
  the legacy one, whose ``fields = "__all__"`` returned it verbatim.

Also covers the ``app_id`` overload: ``upload_media`` read it as the META App
ID while the model documents it as the Gupshup one. ``meta_app_id`` names it,
with a fallback so apps configured before the field keep uploading.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_per_tenant_meta_token.py -v
"""

from __future__ import annotations

import io
import itertools
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from wa.adapters.meta_direct import MetaDirectAdapter

User = get_user_model()

_mobile_seq = itertools.count(1)


def _tenant_with_owner():
    """A tenant, an OWNER user in it, and an APIClient authenticated as that user.

    OWNER because ``WAAppViewSet`` only hands the full serializer — the one
    carrying the BSP identifiers and credentials — to priority >= 80 (#251).
    """
    from tenants.models import Tenant, TenantRole, TenantUser

    tenant = Tenant.objects.create(name=f"TokenTenant-{uuid.uuid4().hex[:6]}", is_active=True)
    role = TenantRole.objects.get(tenant=tenant, slug="owner")  # seeded by signal
    user = User.objects.create_user(
        username=f"tok_owner_{uuid.uuid4().hex[:8]}",
        email=f"tok_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190000{next(_mobile_seq):05d}",
        password="testpass123",
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role)

    client = APIClient()
    client.force_authenticate(user=user)
    return tenant, user, client


def _wa_app(tenant, **overrides):
    from wa.models import WAApp

    fields = {
        "tenant": tenant,
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": "GUPSHUP-APP-ID",
        "app_secret": "s",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": f"waba-{uuid.uuid4().hex[:6]}",
        "phone_number_id": f"pn-{uuid.uuid4().hex[:6]}",
        "bsp": "META",
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


# ─────────────────────────────────────────────────────────────────────────────
# The token can be set at all — the heart of #275
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_token_can_be_set_through_the_v2_api():
    """The regression guard: with the field missing from ``Meta.fields`` DRF
    silently ignores the key and answers 200 with nothing written."""
    tenant, _user, client = _tenant_with_owner()
    wa_app = _wa_app(tenant)

    resp = client.patch(
        f"/wa/v2/apps/{wa_app.id}/",
        {"bsp_credentials": {"access_token": "tenant-token-A"}},
        format="json",
    )

    assert resp.status_code == 200, resp.data
    wa_app.refresh_from_db()
    assert wa_app.bsp_credentials == {"access_token": "tenant-token-A"}


@pytest.mark.django_db
def test_the_token_can_be_set_when_the_app_is_created():
    tenant, _user, client = _tenant_with_owner()

    resp = client.post(
        "/wa/v2/apps/",
        {
            "tenant": tenant.id,
            "app_name": "created-through-the-api",
            "phone_number": "+919876543210",
            "app_id": "GUPSHUP-APP-ID",
            "bsp": "META",
            "waba_id": "waba-created",
            "phone_number_id": "pn-created",
            "bsp_credentials": {"access_token": "tenant-token-on-create"},
        },
        format="json",
    )

    assert resp.status_code == 201, resp.data

    from wa.models import WAApp

    created = WAApp.objects.get(id=resp.data["id"])
    assert created.bsp_credentials == {"access_token": "tenant-token-on-create"}


@pytest.mark.django_db
def test_a_token_set_through_the_api_wins_over_the_global_one(settings):
    """Acceptance: set through the v2 API, used in preference to META_PERM_TOKEN."""
    settings.META_PERM_TOKEN = "global-platform-token"
    tenant, _user, client = _tenant_with_owner()
    wa_app = _wa_app(tenant)

    # Before it is set, the app is on the shared credential.
    assert MetaDirectAdapter(wa_app)._resolve_access_token() == "global-platform-token"

    client.patch(
        f"/wa/v2/apps/{wa_app.id}/",
        {"bsp_credentials": {"access_token": "tenant-token-A"}},
        format="json",
    )

    wa_app.refresh_from_db()
    assert MetaDirectAdapter(wa_app)._resolve_access_token() == "tenant-token-A"


@pytest.mark.django_db
def test_rotating_one_tenants_token_leaves_the_others_alone(settings):
    """Acceptance: rotation is per tenant, not all-or-nothing."""
    settings.META_PERM_TOKEN = "global-platform-token"
    tenant_a, _ua, client_a = _tenant_with_owner()
    tenant_b, _ub, client_b = _tenant_with_owner()
    app_a = _wa_app(tenant_a, bsp_credentials={"access_token": "token-A-v1"})
    app_b = _wa_app(tenant_b, bsp_credentials={"access_token": "token-B-v1"})

    resp = client_a.patch(
        f"/wa/v2/apps/{app_a.id}/",
        {"bsp_credentials": {"access_token": "token-A-v2"}},
        format="json",
    )
    assert resp.status_code == 200, resp.data

    app_a.refresh_from_db()
    app_b.refresh_from_db()
    assert MetaDirectAdapter(app_a)._resolve_access_token() == "token-A-v2"
    assert MetaDirectAdapter(app_b)._resolve_access_token() == "token-B-v1"

    # And tenant B's owner cannot reach tenant A's app to rotate it.
    assert client_b.patch(f"/wa/v2/apps/{app_a.id}/", {"is_active": False}, format="json").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# …and never comes back out
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_v2_api_never_returns_the_token():
    """Write-only: not in the retrieve body, not echoed by the write that set it."""
    tenant, _user, client = _tenant_with_owner()
    wa_app = _wa_app(tenant, bsp_credentials={"access_token": "tenant-token-A"})

    write = client.patch(
        f"/wa/v2/apps/{wa_app.id}/",
        {"bsp_credentials": {"access_token": "tenant-token-B"}},
        format="json",
    )
    read = client.get(f"/wa/v2/apps/{wa_app.id}/")
    listing = client.get("/wa/v2/apps/")

    for resp in (write, read, listing):
        assert resp.status_code == 200, resp.data
        assert "bsp_credentials" not in resp.content.decode()
        assert "tenant-token-" not in resp.content.decode()


@pytest.mark.django_db
def test_the_legacy_wa_app_endpoint_never_returns_the_token():
    """``fields = "__all__"`` on the legacy serializer returned the live token."""
    tenant, _user, client = _tenant_with_owner()
    _wa_app(tenant, bsp_credentials={"access_token": "tenant-token-A"})

    resp = client.get("/tenants/tenant-gupshup/")

    assert resp.status_code == 200, resp.data
    body = resp.content.decode()
    assert "bsp_credentials" not in body
    assert "tenant-token-A" not in body


# ─────────────────────────────────────────────────────────────────────────────
# meta_app_id — the other half of the overload
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_upload_media_uses_the_meta_app_id():
    """``app_id`` is the Gupshup ID; the Resumable Upload API needs the META one."""
    tenant, _user, _client = _tenant_with_owner()
    wa_app = _wa_app(
        tenant,
        app_id="GUPSHUP-APP-ID",
        meta_app_id="META-APP-ID",
        bsp_credentials={"access_token": "tok"},
    )

    with patch(
        "wa.utility.apis.meta.media_api.MetaMediaAPI.upload_media_for_template",
        return_value="handle-1",
    ) as upload:
        result = MetaDirectAdapter(wa_app).upload_media(io.BytesIO(b"x"), "a.png", "image/png")

    assert result.success is True
    assert upload.call_args.kwargs["app_id"] == "META-APP-ID"


@pytest.mark.django_db
def test_upload_media_falls_back_to_app_id_for_apps_configured_before_the_field():
    """Existing configuration keeps working: app_id is still read when meta_app_id is unset."""
    tenant, _user, _client = _tenant_with_owner()
    wa_app = _wa_app(tenant, app_id="META-APP-ID-IN-THE-OLD-COLUMN", bsp_credentials={"access_token": "tok"})
    assert wa_app.meta_app_id is None

    with patch(
        "wa.utility.apis.meta.media_api.MetaMediaAPI.upload_media_for_template",
        return_value="handle-1",
    ) as upload:
        result = MetaDirectAdapter(wa_app).upload_media(io.BytesIO(b"x"), "a.png", "image/png")

    assert result.success is True
    assert upload.call_args.kwargs["app_id"] == "META-APP-ID-IN-THE-OLD-COLUMN"


@pytest.mark.django_db
def test_the_meta_app_id_can_be_set_through_the_v2_api():
    """A field nobody can set is the defect this ticket is about."""
    tenant, _user, client = _tenant_with_owner()
    wa_app = _wa_app(tenant)

    resp = client.patch(f"/wa/v2/apps/{wa_app.id}/", {"meta_app_id": "META-APP-ID"}, format="json")

    assert resp.status_code == 200, resp.data
    wa_app.refresh_from_db()
    assert wa_app.meta_app_id == "META-APP-ID"
    # Not a secret, unlike the token — the operator must be able to read it back.
    assert resp.data["meta_app_id"] == "META-APP-ID"
    assert wa_app.app_id == "GUPSHUP-APP-ID", "the Gupshup identifier must be left alone"
