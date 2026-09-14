"""Showing a stored credential is allowed, recorded, and narrow (#289 reversed).

The columns are encrypted so the plaintext leaves the database for a Graph call
and nothing else, and the API withheld them for the same reason. An operator
holding a credential they cannot see cannot tell a working one from a wrong one,
so the reveal exists — and because a META access token can send as the tenant,
read their message history and rewrite their templates, every reveal is written
down before the value is handed over.
"""

import uuid

import pytest
from rest_framework.test import APIClient

from tenants.models import (
    BSPChoices,
    Tenant,
    TenantRole,
    TenantUser,
    TenantWAApp,
    WACredentialReveal,
)
from users.models import User

STORED_TOKEN = "EAAG" + "x" * 60 + "7Fx9"
STORED_APP_SECRET = "abcdef0123456789abcdef0123456789"


@pytest.fixture()
def app_and_owner(db):
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant.objects.create(name=f"Reveal-{suffix}")
    user = User.objects.create_user(username=f"owner-{suffix}")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])

    app = TenantWAApp.objects.create(
        tenant=tenant,
        app_name=f"app-{suffix}",
        app_id=f"gs_{suffix}",
        app_secret=f"gupshup-{suffix}",  # the unrelated Gupshup one
        wa_number=f"+27115{uuid.uuid4().int % 10**6:06d}",
        bsp=BSPChoices.META,
        bsp_access_token=STORED_TOKEN,
        meta_app_secret=STORED_APP_SECRET,
    )
    return app, user, tenant


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db()
def test_the_stored_token_comes_back_in_full(app_and_owner):
    app, user, _ = app_and_owner

    res = _client(user).post(f"/wa/v2/apps/{app.id}/reveal-credential/", {"field": "bsp_access_token"}, format="json")

    assert res.status_code == 200
    assert res.data["value"] == STORED_TOKEN
    assert res.data["is_set"] is True


@pytest.mark.django_db()
def test_every_reveal_is_recorded_before_the_value_is_returned(app_and_owner):
    app, user, tenant = app_and_owner

    _client(user).post(f"/wa/v2/apps/{app.id}/reveal-credential/", {"field": "meta_app_secret"}, format="json")

    row = WACredentialReveal.objects.get(wa_app=app)
    assert row.field == "meta_app_secret"
    assert row.actor_username == user.username
    assert row.tenant_name == tenant.name
    # The record names the field, never its contents.
    assert STORED_APP_SECRET not in str(row.__dict__)


@pytest.mark.django_db()
def test_a_field_outside_the_whitelist_is_refused(app_and_owner):
    """A column added later must not become readable by default."""
    app, user, _ = app_and_owner

    res = _client(user).post(f"/wa/v2/apps/{app.id}/reveal-credential/", {"field": "app_secret"}, format="json")

    assert res.status_code == 400
    assert WACredentialReveal.objects.count() == 0


@pytest.mark.django_db()
def test_nothing_stored_is_an_answer_not_an_error(app_and_owner):
    """ "Not configured" must be told apart from "configured and blank-looking"."""
    app, user, _ = app_and_owner
    app.meta_app_secret = ""
    app.save(update_fields=["meta_app_secret"])

    res = _client(user).post(f"/wa/v2/apps/{app.id}/reveal-credential/", {"field": "meta_app_secret"}, format="json")

    assert res.status_code == 200
    assert res.data["is_set"] is False
    assert res.data["value"] == ""
    # Nothing was shown, so nothing is recorded.
    assert WACredentialReveal.objects.count() == 0


@pytest.mark.django_db()
def test_the_response_is_not_cacheable(app_and_owner):
    app, user, _ = app_and_owner

    res = _client(user).post(f"/wa/v2/apps/{app.id}/reveal-credential/", {"field": "bsp_access_token"}, format="json")

    assert res["Cache-Control"] == "no-store"


@pytest.mark.django_db()
def test_a_member_without_manage_cannot_reveal(app_and_owner):
    """Same key as the actions that change an app; a viewer must never reach it."""
    app, _, tenant = app_and_owner
    # Roles are seeded per tenant; "viewer" is the read-only one, and a
    # TenantUser without a role is not a state this project allows.
    viewer_role = TenantRole.objects.get(tenant=tenant, slug="viewer")
    viewer = User.objects.create_user(
        username=f"viewer-{uuid.uuid4().hex[:6]}",
        email=f"viewer-{uuid.uuid4().hex[:6]}@test.com",
        mobile=f"+2711{uuid.uuid4().int % 10**7:07d}",
    )
    TenantUser.objects.create(user=viewer, tenant=tenant, role=viewer_role)

    res = _client(viewer).post(f"/wa/v2/apps/{app.id}/reveal-credential/", {"field": "bsp_access_token"}, format="json")

    assert res.status_code in (403, 404)
    assert WACredentialReveal.objects.count() == 0
