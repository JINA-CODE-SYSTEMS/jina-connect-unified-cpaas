"""B3 (#183): default-flag mutex on VoiceProviderConfig.

Covers the partial unique constraints at the DB layer and the
serializer-level transactional demotion that the REST API performs so
callers don't have to do the demote-then-promote dance themselves.
"""

from __future__ import annotations

import json

import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser, TenantVoiceApp
from voice.constants import VoiceProvider
from voice.models import VoiceProviderConfig


def _make_cfg(tenant, **extra):
    return VoiceProviderConfig.objects.create(
        tenant=tenant,
        name=extra.pop("name", "cfg"),
        provider=extra.pop("provider", VoiceProvider.TWILIO),
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
        from_numbers=["+14155550100"],
        **extra,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DB-level: partial unique constraints
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDefaultFlagDatabaseConstraints:
    def test_two_default_outbound_in_same_tenant_rejected(self):
        tenant = Tenant.objects.create(name="MutexTenant")
        _make_cfg(tenant, name="a", is_default_outbound=True)
        with transaction.atomic(), pytest.raises(IntegrityError):
            _make_cfg(tenant, name="b", is_default_outbound=True)

    def test_two_default_inbound_in_same_tenant_rejected(self):
        tenant = Tenant.objects.create(name="MutexTenant2")
        _make_cfg(tenant, name="a", is_default_inbound=True)
        with transaction.atomic(), pytest.raises(IntegrityError):
            _make_cfg(tenant, name="b", is_default_inbound=True)

    def test_default_outbound_and_inbound_can_coexist_on_one_config(self):
        # The two constraints are independent — a single config holding
        # both flags is the common single-provider tenant case.
        tenant = Tenant.objects.create(name="MutexTenant3")
        cfg = _make_cfg(tenant, name="solo", is_default_outbound=True, is_default_inbound=True)
        assert cfg.is_default_outbound and cfg.is_default_inbound

    def test_defaults_isolated_per_tenant(self):
        t1 = Tenant.objects.create(name="T1")
        t2 = Tenant.objects.create(name="T2")
        _make_cfg(t1, name="t1-default", is_default_outbound=True)
        # Same flag in a different tenant must be allowed.
        _make_cfg(t2, name="t2-default", is_default_outbound=True)

    def test_non_default_rows_unrestricted(self):
        tenant = Tenant.objects.create(name="MutexTenant4")
        _make_cfg(tenant, name="a", is_default_outbound=False)
        _make_cfg(tenant, name="b", is_default_outbound=False)
        _make_cfg(tenant, name="c", is_default_outbound=False)
        # No raise.
        assert VoiceProviderConfig.objects.filter(tenant=tenant).count() == 3


# ─────────────────────────────────────────────────────────────────────────────
# Serializer-level: transactional demotion via REST
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tenant(db):
    return Tenant.objects.create(name="MutexAPITenant")


@pytest.fixture()
def admin_user(db, tenant):
    from django.contrib.auth import get_user_model

    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    user = get_user_model().objects.create_user(
        username="mutex_admin",
        email="ma@test.com",
        mobile="+919600009000",
        password="x",
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)
    return user


@pytest.fixture()
def voice_app(tenant):
    return TenantVoiceApp.objects.create(tenant=tenant, is_enabled=True)


@pytest.fixture()
def api(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


class TestDefaultFlagSerializerMutex:
    def test_create_new_default_demotes_existing(self, api, tenant, voice_app):
        existing = _make_cfg(tenant, name="existing", is_default_outbound=True)
        resp = api.post(
            "/voice/v1/api/provider-configs/",
            data={
                "name": "challenger",
                "provider": VoiceProvider.TWILIO,
                "credentials": {"account_sid": "AC2", "auth_token": "t2"},
                "from_numbers": ["+14155550101"],
                "is_default_outbound": True,
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        existing.refresh_from_db()
        assert existing.is_default_outbound is False
        # New row is now the sole default-outbound.
        defaults = VoiceProviderConfig.objects.filter(tenant=tenant, is_default_outbound=True)
        assert defaults.count() == 1
        assert defaults.first().name == "challenger"

    def test_patch_to_default_demotes_existing(self, api, tenant, voice_app):
        existing = _make_cfg(tenant, name="old-default", is_default_inbound=True)
        challenger = _make_cfg(tenant, name="challenger", is_default_inbound=False)
        resp = api.patch(
            f"/voice/v1/api/provider-configs/{challenger.id}/",
            data={"is_default_inbound": True},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        existing.refresh_from_db()
        challenger.refresh_from_db()
        assert existing.is_default_inbound is False
        assert challenger.is_default_inbound is True

    def test_non_default_create_does_not_disturb_existing(self, api, tenant, voice_app):
        existing = _make_cfg(tenant, name="existing", is_default_outbound=True)
        resp = api.post(
            "/voice/v1/api/provider-configs/",
            data={
                "name": "innocuous",
                "provider": VoiceProvider.TWILIO,
                "credentials": {"account_sid": "AC2", "auth_token": "t2"},
                "from_numbers": ["+14155550102"],
                "is_default_outbound": False,
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        existing.refresh_from_db()
        assert existing.is_default_outbound is True

    def test_patch_self_to_default_when_already_default_is_noop(self, api, tenant, voice_app):
        cfg = _make_cfg(tenant, name="self", is_default_outbound=True)
        resp = api.patch(
            f"/voice/v1/api/provider-configs/{cfg.id}/",
            data={"is_default_outbound": True},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        cfg.refresh_from_db()
        assert cfg.is_default_outbound is True

    def test_integrityerror_race_returns_400_not_500(self, api, tenant, voice_app, monkeypatch):
        """B3 race: two concurrent transactions both pass the demote
        check (each sees no committed default after the other's
        uncommitted demote), the partial unique index then rejects the
        second commit. The serializer must translate the
        ``IntegrityError`` into a 400 ``ValidationError`` so the
        frontend can show a retry message instead of an opaque 500.

        We simulate the race by short-circuiting the demote step (no-op)
        so the second create races straight at the constraint, the same
        way two parallel transactions would after both pass demote.
        """
        # Pre-existing default-outbound row that *won't* be demoted.
        _make_cfg(tenant, name="incumbent", is_default_outbound=True)

        from voice.serializers import VoiceProviderConfigSerializer

        monkeypatch.setattr(
            VoiceProviderConfigSerializer,
            "_demote_existing_defaults",
            lambda *a, **kw: None,
        )
        resp = api.post(
            "/voice/v1/api/provider-configs/",
            data={
                "name": "challenger",
                "provider": VoiceProvider.TWILIO,
                "credentials": {"account_sid": "AC2", "auth_token": "t2"},
                "from_numbers": ["+14155550144"],
                "is_default_outbound": True,
            },
            format="json",
        )
        assert resp.status_code == 400, resp.content
        body = resp.json()
        assert "is_default_outbound" in body
        # DRF may serialise the field value as a bare string or a
        # one-element list depending on how ValidationError was raised;
        # both forms are valid. Coerce to string before substring check.
        assert "concurrently" in str(body["is_default_outbound"]).lower()
