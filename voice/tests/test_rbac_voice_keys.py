"""B2 (#182): voice.* RBAC permission keys + role mappings.

Confirms:

  * Every expected ``voice.*`` key is in ``ALL_PERMISSIONS``.
  * After ``seed_default_roles``, the per-role grant matches the
    ticket's role matrix exactly.
  * ``IsVoiceAdmin`` checks ``voice.provider.edit`` instead of the
    legacy role-name list — i.e. an OWNER passes, an AGENT does not,
    and a staff user passes regardless.
"""

from __future__ import annotations

import pytest

from tenants.models import RolePermission, Tenant, TenantRole, TenantUser
from tenants.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    seed_default_roles,
)

VOICE_KEYS = [k for k in ALL_PERMISSIONS if k.startswith("voice.")]


class TestVoicePermissionRegistry:
    def test_all_expected_voice_keys_registered(self):
        expected = {
            "voice.provider.view",
            "voice.provider.create",
            "voice.provider.edit",
            "voice.provider.delete",
            "voice.config.view",
            "voice.config.edit",
            "voice.call.view",
            "voice.call.initiate",
            "voice.call.recording.play",
            "voice.call.recording.download",
            "voice.template.view",
            "voice.template.create",
            "voice.template.edit",
            "voice.template.delete",
            "voice.consent.view",
            "voice.consent.edit",
            "voice.rate_card.view",
            "voice.rate_card.edit",
        }
        assert set(VOICE_KEYS) == expected

    def test_every_voice_key_has_description(self):
        for k in VOICE_KEYS:
            assert k in PERMISSION_DESCRIPTIONS, f"missing description for {k}"
            assert PERMISSION_DESCRIPTIONS[k]


class TestDefaultRoleGrants:
    """The grant matrix from B2."""

    def test_owner_and_admin_get_all_voice_keys(self):
        owner = DEFAULT_ROLE_PERMISSIONS["owner"]
        admin = DEFAULT_ROLE_PERMISSIONS["admin"]
        for k in VOICE_KEYS:
            assert owner.get(k) is True, k
            assert admin.get(k) is True, k

    def test_manager_grants(self):
        manager = DEFAULT_ROLE_PERMISSIONS["manager"]
        granted = {k for k in VOICE_KEYS if manager.get(k)}
        assert granted == {
            "voice.call.view",
            "voice.call.initiate",
            "voice.call.recording.play",
            "voice.call.recording.download",
            "voice.provider.view",
            "voice.config.view",
        }

    def test_agent_grants(self):
        agent = DEFAULT_ROLE_PERMISSIONS["agent"]
        granted = {k for k in VOICE_KEYS if agent.get(k)}
        assert granted == {
            "voice.call.view",
            "voice.call.initiate",
            "voice.call.recording.play",
        }

    def test_viewer_grants(self):
        viewer = DEFAULT_ROLE_PERMISSIONS["viewer"]
        granted = {k for k in VOICE_KEYS if viewer.get(k)}
        assert granted == {
            "voice.call.view",
            "voice.call.recording.play",
        }


# ─────────────────────────────────────────────────────────────────────────────
# DB-level: seed_default_roles + migration round-trip
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSeededRolePermissions:
    def test_seed_creates_voice_role_permissions(self):
        tenant = Tenant.objects.create(name="VoiceRBAC")
        # Signal-driven seeding fires on Tenant.post_save; call again
        # to confirm idempotency and exercise the function directly.
        seed_default_roles(tenant)

        owner = TenantRole.objects.get(tenant=tenant, slug="owner")
        agent = TenantRole.objects.get(tenant=tenant, slug="agent")

        owner_keys = {
            rp.permission
            for rp in RolePermission.objects.filter(role=owner, allowed=True, permission__startswith="voice.")
        }
        assert set(VOICE_KEYS).issubset(owner_keys)

        agent_keys = {
            rp.permission
            for rp in RolePermission.objects.filter(role=agent, allowed=True, permission__startswith="voice.")
        }
        assert agent_keys == {
            "voice.call.view",
            "voice.call.initiate",
            "voice.call.recording.play",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Voice gate: IsVoiceAdmin via RBAC
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tenant(db):
    return Tenant.objects.create(name="VoiceAdminRBACTenant")


def _user_with_role(tenant, slug, *, username, mobile):
    from django.contrib.auth import get_user_model

    role = TenantRole.objects.get(tenant=tenant, slug=slug)
    user = get_user_model().objects.create_user(
        username=username, email=f"{username}@t.io", mobile=mobile, password="x"
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)
    return user


class TestIsVoiceAdminRBAC:
    def test_owner_passes(self, tenant):
        from rest_framework.test import APIRequestFactory

        from voice.permissions import IsVoiceAdmin

        user = _user_with_role(tenant, "owner", username="owner1", mobile="+919600002001")
        req = APIRequestFactory().get("/voice/v1/api/provider-configs/")
        req.user = user
        assert IsVoiceAdmin().has_permission(req, view=None) is True

    def test_agent_blocked(self, tenant):
        from rest_framework.test import APIRequestFactory

        from voice.permissions import IsVoiceAdmin

        user = _user_with_role(tenant, "agent", username="agent1", mobile="+919600002002")
        req = APIRequestFactory().get("/voice/v1/api/provider-configs/")
        req.user = user
        assert IsVoiceAdmin().has_permission(req, view=None) is False

    def test_staff_bypass(self, tenant):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory

        from voice.permissions import IsVoiceAdmin

        user = get_user_model().objects.create_user(
            username="staff", email="s@t.io", mobile="+919600002003", password="x", is_staff=True
        )
        req = APIRequestFactory().get("/voice/v1/api/provider-configs/")
        req.user = user
        assert IsVoiceAdmin().has_permission(req, view=None) is True
