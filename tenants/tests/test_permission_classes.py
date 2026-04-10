#!/usr/bin/env python
"""
Unit tests for tenants/permission_classes.py

Tests the RBAC DRF permission classes:
- TenantRolePermission (action→permission-key mapping)
- IsOwner, IsAdminOrAbove, IsManagerOrAbove, IsAgentOrAbove (priority shortcuts)

Run with:
    python manage.py test tenants.tests.test_permission_classes
"""

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from rest_framework.views import APIView

from tenants.models import (
    Tenant, TenantUser, TenantRole, RolePermission, DefaultRoleSlugs,
)
from tenants.permission_classes import (
    TenantRolePermission,
    IsOwner,
    IsAdminOrAbove,
    IsManagerOrAbove,
    IsAgentOrAbove,
)

User = get_user_model()


class _FakeView:
    """Minimal stand-in for a DRF viewset with ``action`` and ``required_permissions``."""

    def __init__(self, action="list", required_permissions=None):
        self.action = action
        self.required_permissions = required_permissions or {}


class PermissionClassesTestCase(TestCase):
    """Tests for TenantRolePermission and priority shortcut classes."""

    @classmethod
    def setUpTestData(cls):
        # ── Tenant ────────────────────────────────────────────────────
        # post_save signal auto-calls seed_default_roles(), creating 5
        # system roles + their RolePermission rows.
        cls.tenant = Tenant.objects.create(name="Test Tenant")

        # ── Roles (fetch signal-created roles) ────────────────────────
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.admin_role = TenantRole.objects.get(tenant=cls.tenant, slug="admin")
        cls.manager_role = TenantRole.objects.get(tenant=cls.tenant, slug="manager")
        cls.agent_role = TenantRole.objects.get(tenant=cls.tenant, slug="agent")
        cls.viewer_role = TenantRole.objects.get(tenant=cls.tenant, slug="viewer")

        # ── Override permissions for test-specific scenarios ──────────
        # Clear signal-seeded permissions and set up controlled test data.
        RolePermission.objects.filter(role__tenant=cls.tenant).delete()

        # Owner, Admin, Manager can do everything tested
        for perm_key in ["broadcast.view", "broadcast.create", "broadcast.cancel"]:
            RolePermission.objects.create(role=cls.owner_role, permission=perm_key, allowed=True)
            RolePermission.objects.create(role=cls.admin_role, permission=perm_key, allowed=True)
            RolePermission.objects.create(role=cls.manager_role, permission=perm_key, allowed=True)

        # Agent can only view
        RolePermission.objects.create(role=cls.agent_role, permission="broadcast.view", allowed=True)
        RolePermission.objects.create(role=cls.agent_role, permission="broadcast.create", allowed=False)

        # Viewer can only view
        RolePermission.objects.create(role=cls.viewer_role, permission="broadcast.view", allowed=True)
        RolePermission.objects.create(role=cls.viewer_role, permission="broadcast.create", allowed=False)

        # ── Users ─────────────────────────────────────────────────────
        cls.superuser = User.objects.create_superuser(
            username="superadmin", email="super@test.com", password="testpass",
            mobile="+919000000001",
        )
        cls.owner_user = User.objects.create_user(
            username="owner_user", email="owner@test.com", password="testpass",
            mobile="+919000000002",
        )
        cls.admin_user = User.objects.create_user(
            username="admin_user", email="admin@test.com", password="testpass",
            mobile="+919000000003",
        )
        cls.manager_user = User.objects.create_user(
            username="manager_user", email="manager@test.com", password="testpass",
            mobile="+919000000004",
        )
        cls.agent_user = User.objects.create_user(
            username="agent_user", email="agent@test.com", password="testpass",
            mobile="+919000000005",
        )
        cls.viewer_user = User.objects.create_user(
            username="viewer_user", email="viewer@test.com", password="testpass",
            mobile="+919000000006",
        )
        cls.no_role_user = User.objects.create_user(
            username="norole_user", email="norole@test.com", password="testpass",
            mobile="+919000000007",
        )

        # ── TenantUser links ─────────────────────────────────────────
        TenantUser.objects.create(tenant=cls.tenant, user=cls.owner_user, role=cls.owner_role)
        TenantUser.objects.create(tenant=cls.tenant, user=cls.admin_user, role=cls.admin_role)
        TenantUser.objects.create(tenant=cls.tenant, user=cls.manager_user, role=cls.manager_role)
        TenantUser.objects.create(tenant=cls.tenant, user=cls.agent_user, role=cls.agent_role)
        TenantUser.objects.create(tenant=cls.tenant, user=cls.viewer_user, role=cls.viewer_role)
        TenantUser.objects.create(tenant=cls.tenant, user=cls.no_role_user, role=cls.viewer_role)

    def _make_request(self, user):
        factory = RequestFactory()
        request = factory.get("/fake/")
        request.user = user
        return request

    # ── TenantRolePermission ──────────────────────────────────────────

    def test_superuser_always_passes(self):
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        request = self._make_request(self.superuser)
        self.assertTrue(perm.has_permission(request, view))

    def test_owner_allowed_for_all(self):
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        request = self._make_request(self.owner_user)
        self.assertTrue(perm.has_permission(request, view))

    def test_agent_denied_create(self):
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        request = self._make_request(self.agent_user)
        self.assertFalse(perm.has_permission(request, view))

    def test_agent_allowed_view(self):
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={"list": "broadcast.view"})
        request = self._make_request(self.agent_user)
        self.assertTrue(perm.has_permission(request, view))

    def test_default_fallback_key(self):
        """If the exact action is not mapped, fall back to 'default'."""
        perm = TenantRolePermission()
        view = _FakeView(
            action="some_custom_action",
            required_permissions={"default": "broadcast.view"},
        )
        request = self._make_request(self.agent_user)
        self.assertTrue(perm.has_permission(request, view))

    def test_no_required_permissions_allows(self):
        """If viewset has no required_permissions, access is granted."""
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={})
        request = self._make_request(self.agent_user)
        self.assertTrue(perm.has_permission(request, view))

    def test_no_role_assigned_denied(self):
        """User with lowest role (viewer) is denied create permissions."""
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        request = self._make_request(self.no_role_user)
        self.assertFalse(perm.has_permission(request, view))

    def test_unauthenticated_denied(self):
        from django.contrib.auth.models import AnonymousUser
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={"list": "broadcast.view"})
        factory = RequestFactory()
        request = factory.get("/fake/")
        request.user = AnonymousUser()
        self.assertFalse(perm.has_permission(request, view))

    def test_permission_not_in_db_denied(self):
        """Permission key with no DB row → denied (deny-by-default)."""
        perm = TenantRolePermission()
        view = _FakeView(
            action="destroy",
            required_permissions={"destroy": "tenant.delete"},
        )
        request = self._make_request(self.agent_user)
        self.assertFalse(perm.has_permission(request, view))

    def test_descriptive_403_message(self):
        """Denied request should have a descriptive message."""
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        request = self._make_request(self.agent_user)
        perm.has_permission(request, view)
        self.assertIn("broadcast.create", perm.message)
        self.assertIn("Agent", perm.message)

    # ── Priority shortcut classes ─────────────────────────────────────

    def test_is_owner_allows_owner(self):
        perm = IsOwner()
        view = _FakeView()
        self.assertTrue(perm.has_permission(self._make_request(self.owner_user), view))

    def test_is_owner_denies_admin(self):
        perm = IsOwner()
        view = _FakeView()
        self.assertFalse(perm.has_permission(self._make_request(self.admin_user), view))

    def test_is_admin_or_above_allows_owner(self):
        perm = IsAdminOrAbove()
        view = _FakeView()
        self.assertTrue(perm.has_permission(self._make_request(self.owner_user), view))

    def test_is_admin_or_above_allows_admin(self):
        perm = IsAdminOrAbove()
        view = _FakeView()
        self.assertTrue(perm.has_permission(self._make_request(self.admin_user), view))

    def test_is_admin_or_above_denies_manager(self):
        perm = IsAdminOrAbove()
        view = _FakeView()
        self.assertFalse(perm.has_permission(self._make_request(self.manager_user), view))

    def test_is_manager_or_above_allows_manager(self):
        perm = IsManagerOrAbove()
        view = _FakeView()
        self.assertTrue(perm.has_permission(self._make_request(self.manager_user), view))

    def test_is_manager_or_above_denies_agent(self):
        perm = IsManagerOrAbove()
        view = _FakeView()
        self.assertFalse(perm.has_permission(self._make_request(self.agent_user), view))

    def test_is_agent_or_above_allows_agent(self):
        perm = IsAgentOrAbove()
        view = _FakeView()
        self.assertTrue(perm.has_permission(self._make_request(self.agent_user), view))

    def test_is_agent_or_above_denies_viewer(self):
        perm = IsAgentOrAbove()
        view = _FakeView()
        self.assertFalse(perm.has_permission(self._make_request(self.viewer_user), view))

    def test_priority_shortcut_superuser_passes(self):
        perm = IsOwner()
        view = _FakeView()
        self.assertTrue(perm.has_permission(self._make_request(self.superuser), view))

    def test_priority_shortcut_no_role_denied(self):
        perm = IsAgentOrAbove()
        view = _FakeView()
        self.assertFalse(perm.has_permission(self._make_request(self.no_role_user), view))

    # ── Edge cases ────────────────────────────────────────────────────

    def test_user_with_no_tenant_membership_denied(self):
        """User who exists but has no TenantUser row at all."""
        orphan = User.objects.create_user(
            username="orphan", email="orphan@test.com", password="testpass",
            mobile="+919000000099",
        )
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={"list": "broadcast.view"})
        self.assertFalse(perm.has_permission(self._make_request(orphan), view))

    def test_inactive_tenant_user_denied(self):
        """TenantUser with is_active=False should be denied even with a valid role."""
        inactive_user = User.objects.create_user(
            username="inactive_user", email="inactive@test.com", password="testpass",
            mobile="+919000000098",
        )
        TenantUser.objects.create(
            tenant=self.tenant, user=inactive_user,
            role=self.owner_role, is_active=False,
        )
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={"list": "broadcast.view"})
        self.assertFalse(perm.has_permission(self._make_request(inactive_user), view))

    def test_explicit_deny_vs_missing_row(self):
        """allowed=False in DB and no row at all should both deny."""
        perm = TenantRolePermission()
        # Agent has broadcast.create = False (explicit deny)
        view_explicit = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        self.assertFalse(perm.has_permission(self._make_request(self.agent_user), view_explicit))
        # Agent has no row for tenant.delete (missing row)
        view_missing = _FakeView(action="destroy", required_permissions={"destroy": "tenant.delete"})
        self.assertFalse(perm.has_permission(self._make_request(self.agent_user), view_missing))

    def test_exact_action_takes_precedence_over_default(self):
        """Exact action match should be used instead of the default key."""
        # Agent can view but cannot create — verify exact match wins
        perm = TenantRolePermission()
        view = _FakeView(
            action="create",
            required_permissions={
                "create": "broadcast.create",   # agent denied
                "default": "broadcast.view",     # agent allowed
            },
        )
        # Should use "create" key, not "default", so agent is denied
        self.assertFalse(perm.has_permission(self._make_request(self.agent_user), view))

    def test_custom_role_with_custom_priority(self):
        """Custom role with priority=50 should pass IsAgentOrAbove but fail IsManagerOrAbove."""
        custom_role = TenantRole.objects.create(
            tenant=self.tenant, name="Campaign Lead", slug="campaign_lead",
            priority=50, is_system=False,
        )
        custom_user = User.objects.create_user(
            username="custom_user", email="custom@test.com", password="testpass",
            mobile="+919000000097",
        )
        TenantUser.objects.create(tenant=self.tenant, user=custom_user, role=custom_role)

        view = _FakeView()
        self.assertTrue(IsAgentOrAbove().has_permission(self._make_request(custom_user), view))
        self.assertFalse(IsManagerOrAbove().has_permission(self._make_request(custom_user), view))

    def test_inactive_tenant_user_denied_for_shortcut(self):
        """Priority shortcut should also deny inactive TenantUser."""
        inactive_owner = User.objects.create_user(
            username="inactive_owner", email="inactiveowner@test.com", password="testpass",
            mobile="+919000000096",
        )
        TenantUser.objects.create(
            tenant=self.tenant, user=inactive_owner,
            role=self.owner_role, is_active=False,
        )
        perm = IsOwner()
        view = _FakeView()
        self.assertFalse(perm.has_permission(self._make_request(inactive_owner), view))
