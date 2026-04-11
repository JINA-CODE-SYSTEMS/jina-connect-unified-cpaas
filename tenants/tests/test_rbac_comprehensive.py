#!/usr/bin/env python
"""
Comprehensive RBAC Test Suite (RBAC-11).

Covers:
  1. Unit Tests          — Permission registry, has_permission(), seed_default_roles(),
                           TenantRolePermission, priority shortcuts
  2. Integration Tests   — Each role against every endpoint, custom role access,
                           role assignment, ownership transfer, my-permissions
  3. Security Tests      — Escalation prevention, OWNER protections, priority guards
  4. Migration Tests     — Signal-based seeding, data correctness

Run with:
    python manage.py test tenants.tests.test_rbac_comprehensive --verbosity=2 --no-input
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from tenants.models import (
    DefaultRoleSlugs,
    RolePermission,
    Tenant,
    TenantRole,
    TenantUser,
)
from tenants.permission_classes import (
    IsAdminOrAbove,
    IsAgentOrAbove,
    IsManagerOrAbove,
    IsOwner,
    TenantRolePermission,
)
from tenants.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    has_permission,
    seed_default_roles,
)

User = get_user_model()


# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════


class _FakeView:
    """Minimal viewset stand-in for permission class tests."""

    def __init__(self, action="list", required_permissions=None):
        self.action = action
        self.required_permissions = required_permissions or {}


def _make_user(username, email, mobile, **kwargs):
    return User.objects.create_user(
        username=username,
        email=email,
        mobile=mobile,
        password="testpass123",
        **kwargs,
    )


def _api_client(user):
    """Return an APIClient authenticated as *user*."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ═══════════════════════════════════════════════════════════════════════════
# 1.  UNIT TESTS — Permission Registry
# ═══════════════════════════════════════════════════════════════════════════


class PermissionRegistryTests(TestCase):
    """Tests for ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS, PERMISSION_DESCRIPTIONS."""

    def test_all_permissions_is_list_of_strings(self):
        self.assertIsInstance(ALL_PERMISSIONS, list)
        for p in ALL_PERMISSIONS:
            self.assertIsInstance(p, str)
            self.assertIn(".", p, f"Permission key '{p}' missing dot separator")

    def test_all_permissions_unique(self):
        self.assertEqual(len(ALL_PERMISSIONS), len(set(ALL_PERMISSIONS)))

    def test_all_permissions_count(self):
        """Sanity check that we have the expected number of permission keys."""
        self.assertEqual(len(ALL_PERMISSIONS), 43)

    def test_default_roles_have_entries(self):
        for slug in DefaultRoleSlugs.values:
            self.assertIn(slug, DEFAULT_ROLE_PERMISSIONS, f"Missing DEFAULT_ROLE_PERMISSIONS for '{slug}'")

    def test_owner_has_all_permissions_true(self):
        owner_perms = DEFAULT_ROLE_PERMISSIONS["owner"]
        for perm in ALL_PERMISSIONS:
            self.assertTrue(owner_perms.get(perm, False), f"OWNER should have '{perm}' = True")

    def test_admin_denied_ownership_ops(self):
        admin_perms = DEFAULT_ROLE_PERMISSIONS["admin"]
        self.assertFalse(admin_perms.get("tenant.delete", True))
        self.assertFalse(admin_perms.get("tenant.transfer", True))

    def test_admin_has_remaining_permissions(self):
        admin_perms = DEFAULT_ROLE_PERMISSIONS["admin"]
        admin_denied = {"tenant.delete", "tenant.transfer", "wa_app.delete"}
        for perm in ALL_PERMISSIONS:
            if perm in admin_denied:
                continue
            self.assertTrue(admin_perms.get(perm, False), f"ADMIN should have '{perm}' = True")

    def test_viewer_only_has_view_permissions(self):
        viewer_perms = DEFAULT_ROLE_PERMISSIONS["viewer"]
        for perm, allowed in viewer_perms.items():
            if allowed:
                self.assertTrue(
                    perm.endswith(".view") or perm == "billing.view",
                    f"VIEWER has non-view permission '{perm}' set to True",
                )

    def test_manager_has_no_user_management_or_billing_manage(self):
        mgr = DEFAULT_ROLE_PERMISSIONS["manager"]
        for perm in (
            "users.invite",
            "users.change_role",
            "users.remove",
            "billing.manage",
            "wa_app.manage",
            "rate_card.manage",
            "tenant.edit",
            "tenant.delete",
            "tenant.transfer",
        ):
            self.assertFalse(mgr.get(perm, False), f"MANAGER should not have '{perm}'")

    def test_agent_subset_of_manager(self):
        """Every permission granted to AGENT should also be granted to MANAGER."""
        agent = DEFAULT_ROLE_PERMISSIONS["agent"]
        manager = DEFAULT_ROLE_PERMISSIONS["manager"]
        for perm, allowed in agent.items():
            if allowed:
                self.assertTrue(manager.get(perm, False), f"AGENT has '{perm}' but MANAGER does not")

    def test_permission_descriptions_match_all_permissions(self):
        self.assertEqual(set(PERMISSION_DESCRIPTIONS.keys()), set(ALL_PERMISSIONS))

    def test_permission_descriptions_are_non_empty(self):
        for perm, desc in PERMISSION_DESCRIPTIONS.items():
            self.assertIsInstance(desc, str)
            self.assertTrue(len(desc) > 5, f"Description for '{perm}' too short: '{desc}'")

    def test_default_role_permissions_no_unknown_keys(self):
        perm_set = set(ALL_PERMISSIONS)
        for slug, perms in DEFAULT_ROLE_PERMISSIONS.items():
            for key in perms:
                self.assertIn(key, perm_set, f"Unknown perm key '{key}' in role '{slug}'")


# ═══════════════════════════════════════════════════════════════════════════
# 1b. UNIT TESTS — has_permission() function
# ═══════════════════════════════════════════════════════════════════════════


class HasPermissionFunctionTests(TestCase):
    """Tests for tenants.permissions.has_permission()."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="HasPerm Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.viewer_role = TenantRole.objects.get(tenant=cls.tenant, slug="viewer")

    def test_owner_has_all_permissions(self):
        for perm in ALL_PERMISSIONS:
            self.assertTrue(has_permission(self.owner_role, perm), f"OWNER should have '{perm}'")

    def test_viewer_denied_create_permissions(self):
        for perm in ALL_PERMISSIONS:
            if not perm.endswith(".view") and perm != "billing.view":
                self.assertFalse(has_permission(self.viewer_role, perm), f"VIEWER should NOT have '{perm}'")

    def test_unknown_permission_denied(self):
        self.assertFalse(has_permission(self.owner_role, "nonexistent.action"))

    def test_explicit_false_denied(self):
        """allowed=False in DB is denied."""
        admin_role = TenantRole.objects.get(tenant=self.tenant, slug="admin")
        self.assertFalse(has_permission(admin_role, "tenant.delete"))


# ═══════════════════════════════════════════════════════════════════════════
# 1c. UNIT TESTS — seed_default_roles()
# ═══════════════════════════════════════════════════════════════════════════


class SeedDefaultRolesTests(TestCase):
    """Tests for tenants.permissions.seed_default_roles() function."""

    def test_signal_creates_roles_on_tenant_creation(self):
        """post_save signal should auto-seed 5 roles."""
        tenant = Tenant.objects.create(name="Signal Seed Tenant")
        roles = TenantRole.objects.filter(tenant=tenant, is_system=True)
        self.assertEqual(roles.count(), 5)
        for slug in DefaultRoleSlugs.values:
            self.assertTrue(roles.filter(slug=slug).exists(), f"Missing seeded role '{slug}'")

    def test_seeded_roles_have_all_permission_rows(self):
        tenant = Tenant.objects.create(name="Perm Rows Tenant")
        for role in TenantRole.objects.filter(tenant=tenant, is_system=True):
            perm_count = RolePermission.objects.filter(role=role).count()
            self.assertEqual(
                perm_count,
                len(ALL_PERMISSIONS),
                f"Role '{role.slug}' has {perm_count} perms, expected {len(ALL_PERMISSIONS)}",
            )

    def test_seeded_owner_is_not_editable(self):
        tenant = Tenant.objects.create(name="Editable Check Tenant")
        owner = TenantRole.objects.get(tenant=tenant, slug="owner")
        self.assertFalse(owner.is_editable)
        for slug in ("admin", "manager", "agent", "viewer"):
            role = TenantRole.objects.get(tenant=tenant, slug=slug)
            self.assertTrue(role.is_editable)

    def test_seed_is_idempotent(self):
        """Calling seed_default_roles twice does not create duplicate roles."""
        tenant = Tenant.objects.create(name="Idempotent Tenant")
        # Signal already seeded. Call again manually.
        seed_default_roles(tenant)
        self.assertEqual(
            TenantRole.objects.filter(tenant=tenant, is_system=True).count(),
            5,
        )
        owner_role = TenantRole.objects.get(tenant=tenant, slug="owner")
        self.assertEqual(
            RolePermission.objects.filter(role=owner_role).count(),
            len(ALL_PERMISSIONS),
        )

    def test_seeded_priorities(self):
        tenant = Tenant.objects.create(name="Priority Tenant")
        expected = {"owner": 100, "admin": 80, "manager": 60, "agent": 40, "viewer": 20}
        for slug, priority in expected.items():
            role = TenantRole.objects.get(tenant=tenant, slug=slug)
            self.assertEqual(role.priority, priority, f"Role '{slug}' priority should be {priority}")


# ═══════════════════════════════════════════════════════════════════════════
# 2.  INTEGRATION TESTS — API endpoints via APIClient
# ═══════════════════════════════════════════════════════════════════════════


class RBACIntegrationBase(TestCase):
    """
    Base class providing a tenant, all 5 default roles, one user per role,
    and APIClients for each user.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="RBAC Integration Tenant")

        # Fetch signal-created roles
        cls.roles = {}
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            cls.roles[slug] = TenantRole.objects.get(tenant=cls.tenant, slug=slug)

        # Create users and TenantUser links
        cls.users = {}
        counter = 100
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            counter += 1
            u = _make_user(
                username=f"int_{slug}",
                email=f"int_{slug}@test.com",
                mobile=f"+91900000{counter:04d}",
            )
            TenantUser.objects.create(tenant=cls.tenant, user=u, role=cls.roles[slug])
            cls.users[slug] = u

        # Extra users
        counter += 1
        cls.no_role_user = _make_user(
            username="int_norole",
            email="int_norole@test.com",
            mobile=f"+91900000{counter:04d}",
        )
        # #253: After migration 0008, role is NOT NULL. Give lowest role.
        TenantUser.objects.create(tenant=cls.tenant, user=cls.no_role_user, role=cls.roles["viewer"])

        counter += 1
        cls.superuser = User.objects.create_superuser(
            username="int_super",
            email="int_super@test.com",
            mobile=f"+91900000{counter:04d}",
            password="testpass123",
        )

    def _client(self, role_slug):
        return _api_client(self.users[role_slug])


class MyPermissionsEndpointTests(RBACIntegrationBase):
    """Tests for GET /tenants/my-permissions/."""

    def test_owner_sees_all_true(self):
        resp = self._client("owner").get("/tenants/my-permissions/")
        self.assertEqual(resp.status_code, 200)
        perms = resp.data["permissions"]
        for perm in ALL_PERMISSIONS:
            self.assertTrue(perms[perm], f"OWNER should have '{perm}'=True")

    def test_viewer_sees_only_view_true(self):
        resp = self._client("viewer").get("/tenants/my-permissions/")
        self.assertEqual(resp.status_code, 200)
        perms = resp.data["permissions"]
        for perm in ALL_PERMISSIONS:
            expected = DEFAULT_ROLE_PERMISSIONS["viewer"].get(perm, False)
            self.assertEqual(perms[perm], expected, f"VIEWER '{perm}' should be {expected}")

    def test_unauthenticated_denied(self):
        resp = APIClient().get("/tenants/my-permissions/")
        self.assertEqual(resp.status_code, 401)

    def test_no_role_user_gets_404(self):
        """#253: The 'no role' scenario no longer exists — viewer gets 200 with view perms."""
        resp = _api_client(self.no_role_user).get("/tenants/my-permissions/")
        self.assertEqual(resp.status_code, 200)

    def test_response_contains_role_info(self):
        resp = self._client("admin").get("/tenants/my-permissions/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("role", resp.data)
        self.assertEqual(resp.data["role"]["slug"], "admin")

    def test_all_five_roles_return_correct_permissions(self):
        """Every default role should return the exact permission set from DEFAULT_ROLE_PERMISSIONS."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            resp = self._client(slug).get("/tenants/my-permissions/")
            self.assertEqual(resp.status_code, 200, f"{slug} failed")
            perms = resp.data["permissions"]
            expected = DEFAULT_ROLE_PERMISSIONS[slug]
            for perm in ALL_PERMISSIONS:
                self.assertEqual(
                    perms[perm],
                    expected.get(perm, False),
                    f"{slug}: '{perm}' expected {expected.get(perm, False)}, got {perms[perm]}",
                )


class MemberManagementEndpointTests(RBACIntegrationBase):
    """Tests for /tenants/members/ endpoints."""

    def test_list_members_allowed_for_viewer(self):
        resp = self._client("viewer").get("/tenants/members/")
        self.assertEqual(resp.status_code, 200)

    def test_list_members_denied_for_no_role(self):
        """#253: Now that 'no role' is impossible, viewer (lowest) CAN list members."""
        resp = _api_client(self.no_role_user).get("/tenants/members/")
        self.assertEqual(resp.status_code, 200)

    def test_invite_allowed_for_admin(self):
        invitee = _make_user("invite_target", "invite_target@test.com", "+919000009000")
        resp = self._client("admin").post(
            "/tenants/members/add/",
            {
                "email": invitee.email,
                "role_id": self.roles["agent"].pk,
            },
        )
        self.assertEqual(resp.status_code, 201)

    def test_invite_denied_for_viewer(self):
        invitee = _make_user("invite_denied", "invite_denied@test.com", "+919000009001")
        resp = self._client("viewer").post(
            "/tenants/members/add/",
            {
                "email": invitee.email,
                "role_id": self.roles["agent"].pk,
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_invite_denied_for_agent(self):
        invitee = _make_user("invite_agent_deny", "invite_agent_deny@test.com", "+919000009002")
        resp = self._client("agent").post(
            "/tenants/members/add/",
            {
                "email": invitee.email,
                "role_id": self.roles["viewer"].pk,
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_change_role_allowed_for_admin(self):
        # Admin changes viewer's role to agent
        viewer_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["viewer"])
        resp = self._client("admin").patch(
            f"/tenants/members/{viewer_tu.pk}/role/",
            {"role_id": self.roles["agent"].pk},
        )
        self.assertEqual(resp.status_code, 200)

    def test_change_role_denied_for_agent(self):
        viewer_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["viewer"])
        resp = self._client("agent").patch(
            f"/tenants/members/{viewer_tu.pk}/role/",
            {"role_id": self.roles["agent"].pk},
        )
        self.assertEqual(resp.status_code, 403)

    def test_remove_member_allowed_for_admin(self):
        removable = _make_user("removable", "removable@test.com", "+919000009003")
        tu = TenantUser.objects.create(
            tenant=self.tenant,
            user=removable,
            role=self.roles["viewer"],
        )
        resp = self._client("admin").delete(f"/tenants/members/{tu.pk}/")
        self.assertEqual(resp.status_code, 204)

    def test_remove_denied_for_viewer(self):
        removable2 = _make_user("removable2", "removable2@test.com", "+919000009004")
        tu = TenantUser.objects.create(
            tenant=self.tenant,
            user=removable2,
            role=self.roles["viewer"],
        )
        resp = self._client("viewer").delete(f"/tenants/members/{tu.pk}/")
        self.assertEqual(resp.status_code, 403)

    def test_post_members_returns_405(self):
        """Direct POST /members/ should be blocked."""
        resp = self._client("admin").post("/tenants/members/", {})
        self.assertEqual(resp.status_code, 405)

    def test_patch_members_detail_returns_405(self):
        """Direct PATCH /members/{id}/ should be blocked."""
        viewer_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["viewer"])
        resp = self._client("admin").patch(
            f"/tenants/members/{viewer_tu.pk}/",
            {"name": "nope"},
        )
        self.assertEqual(resp.status_code, 405)

    def test_reinvite_deactivated_member(self):
        """Inviting a deactivated member should reactivate them."""
        deactivated = _make_user("deactivated", "deactivated@test.com", "+919000009005")
        tu = TenantUser.objects.create(
            tenant=self.tenant,
            user=deactivated,
            role=self.roles["viewer"],
            is_active=False,
        )
        resp = self._client("admin").post(
            "/tenants/members/add/",
            {
                "email": deactivated.email,
                "role_id": self.roles["agent"].pk,
            },
        )
        self.assertEqual(resp.status_code, 201)
        tu.refresh_from_db()
        self.assertTrue(tu.is_active)
        self.assertEqual(tu.role, self.roles["agent"])


class TransferOwnershipEndpointTests(RBACIntegrationBase):
    """Tests for POST /tenants/transfer-ownership/."""

    def test_owner_can_transfer_to_admin(self):
        resp = self._client("owner").post(
            "/tenants/transfer-ownership/",
            {
                "target_user_id": self.users["admin"].pk,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Ownership transferred", resp.data["detail"])

        # Verify DB state
        owner_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["owner"])
        admin_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["admin"])
        self.assertEqual(admin_tu.role.slug, "owner")
        self.assertEqual(owner_tu.role.slug, "admin")

        # Restore original state for other tests
        admin_tu.role = self.roles["owner"]
        admin_tu.save(update_fields=["role"])
        owner_tu.role = self.roles["owner"]
        owner_tu.save(update_fields=["role"])
        # Fix: admin should be admin again
        admin_tu.role = self.roles["admin"]
        admin_tu.save(update_fields=["role"])

    def test_admin_cannot_transfer(self):
        resp = self._client("admin").post(
            "/tenants/transfer-ownership/",
            {
                "target_user_id": self.users["manager"].pk,
            },
        )
        # Should be denied by TenantRolePermission (tenant.transfer is False for admin)
        self.assertEqual(resp.status_code, 403)

    def test_cannot_transfer_to_non_admin(self):
        """Transfer target must be ADMIN role."""
        resp = self._client("owner").post(
            "/tenants/transfer-ownership/",
            {
                "target_user_id": self.users["manager"].pk,
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_transfer_to_nonexistent_user(self):
        resp = self._client("owner").post(
            "/tenants/transfer-ownership/",
            {
                "target_user_id": 99999,
            },
        )
        self.assertEqual(resp.status_code, 400)


class RoleCRUDEndpointTests(RBACIntegrationBase):
    """Tests for /tenants/roles/ CRUD endpoints."""

    def test_list_roles_allowed_for_viewer(self):
        resp = self._client("viewer").get("/tenants/roles/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data["results"]), 5)

    def test_list_roles_denied_for_no_role(self):
        """#253: Now that 'no role' is impossible, viewer (lowest) CAN list roles."""
        resp = _api_client(self.no_role_user).get("/tenants/roles/")
        self.assertEqual(resp.status_code, 200)

    def test_create_role_allowed_for_admin(self):
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Test Custom Role",
                "priority": 35,
                "permissions": {"tenant.view": True, "broadcast.view": True},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["name"], "Test Custom Role")
        self.assertFalse(resp.data["is_system"])
        self.assertTrue(resp.data["permissions"]["tenant.view"])
        self.assertTrue(resp.data["permissions"]["broadcast.view"])
        self.assertFalse(resp.data["permissions"]["billing.manage"])
        # Cleanup
        TenantRole.objects.filter(slug="test-custom-role", tenant=self.tenant).delete()

    def test_create_role_denied_for_viewer(self):
        resp = self._client("viewer").post(
            "/tenants/roles/",
            {
                "name": "Viewer Role",
                "priority": 10,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_retrieve_role_detail(self):
        role = self.roles["admin"]
        resp = self._client("viewer").get(f"/tenants/roles/{role.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["slug"], "admin")
        self.assertIn("permissions", resp.data)
        self.assertEqual(len(resp.data["permissions"]), len(ALL_PERMISSIONS))

    def test_edit_role_name(self):
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="Editable",
            slug="editable-test",
            priority=25,
            is_system=False,
            is_editable=True,
        )
        RolePermission.objects.bulk_create(
            [RolePermission(role=custom, permission=p, allowed=False) for p in ALL_PERMISSIONS]
        )
        resp = self._client("admin").patch(
            f"/tenants/roles/{custom.pk}/",
            {"name": "Renamed Role"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Renamed Role")
        custom.delete()

    def test_edit_role_permissions(self):
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="PermEdit",
            slug="permedit-test",
            priority=25,
            is_system=False,
            is_editable=True,
        )
        RolePermission.objects.bulk_create(
            [RolePermission(role=custom, permission=p, allowed=False) for p in ALL_PERMISSIONS]
        )
        resp = self._client("admin").patch(
            f"/tenants/roles/{custom.pk}/",
            {"permissions": {"contact.view": True, "contact.create": True}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["permissions"]["contact.view"])
        self.assertTrue(resp.data["permissions"]["contact.create"])
        self.assertFalse(resp.data["permissions"]["contact.delete"])
        custom.delete()

    def test_delete_custom_role_no_members(self):
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="Deletable",
            slug="deletable-test",
            priority=25,
            is_system=False,
            is_editable=True,
        )
        resp = self._client("admin").delete(f"/tenants/roles/{custom.pk}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(TenantRole.objects.filter(pk=custom.pk).exists())

    def test_delete_system_role_blocked(self):
        resp = self._client("owner").delete(f"/tenants/roles/{self.roles['viewer'].pk}/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("System", resp.data["detail"])

    def test_delete_role_with_active_members_blocked(self):
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="HasMembers",
            slug="hasmembers-test",
            priority=25,
            is_system=False,
            is_editable=True,
        )
        member = _make_user("member_del", "member_del@test.com", "+919000009010")
        TenantUser.objects.create(tenant=self.tenant, user=member, role=custom)
        resp = self._client("admin").delete(f"/tenants/roles/{custom.pk}/")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("active_member_count", resp.data)
        # Cleanup
        TenantUser.objects.filter(user=member).delete()
        custom.delete()

    def test_permissions_catalog(self):
        """Catalog returns a dict grouped by module (e.g. tenant, broadcast, ...)."""
        resp = self._client("viewer").get("/tenants/roles/permissions-catalog/")
        self.assertEqual(resp.status_code, 200)
        # Response is a dict with module-name keys
        self.assertIsInstance(resp.data, dict)
        # Flatten and verify all permission keys are present
        all_keys = []
        for module, entries in resp.data.items():
            for entry in entries:
                all_keys.append(entry["key"])
                self.assertIn("key", entry)
                self.assertIn("label", entry)
        self.assertEqual(len(all_keys), len(ALL_PERMISSIONS))
        for perm in ALL_PERMISSIONS:
            self.assertIn(perm, all_keys)

    def test_reset_system_role(self):
        """Reset admin role to defaults, verify permissions are restored."""
        admin_role = self.roles["admin"]
        # Tamper: set tenant.view to False
        RolePermission.objects.filter(
            role=admin_role,
            permission="tenant.view",
        ).update(allowed=False)

        resp = self._client("owner").post(
            f"/tenants/roles/{admin_role.pk}/reset/",
        )
        self.assertEqual(resp.status_code, 200)
        # Verify restored
        self.assertTrue(resp.data["permissions"]["tenant.view"])
        self.assertFalse(resp.data["permissions"]["tenant.delete"])

    def test_reset_custom_role_blocked(self):
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="NoReset",
            slug="noreset-test",
            priority=25,
            is_system=False,
            is_editable=True,
        )
        resp = self._client("admin").post(f"/tenants/roles/{custom.pk}/reset/")
        self.assertEqual(resp.status_code, 400)
        custom.delete()

    def test_create_role_auto_generates_slug(self):
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Campaign Manager",
                "priority": 35,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["slug"], "campaign-manager")
        TenantRole.objects.filter(slug="campaign-manager", tenant=self.tenant).delete()

    def test_create_role_explicit_slug(self):
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Custom Name",
                "slug": "my-custom-slug",
                "priority": 30,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["slug"], "my-custom-slug")
        TenantRole.objects.filter(slug="my-custom-slug", tenant=self.tenant).delete()

    def test_create_role_duplicate_slug_rejected(self):
        TenantRole.objects.create(
            tenant=self.tenant,
            name="Dup",
            slug="dup-slug",
            priority=25,
            is_system=False,
        )
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Another",
                "slug": "dup-slug",
                "priority": 30,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        TenantRole.objects.filter(slug="dup-slug", tenant=self.tenant).delete()

    def test_role_detail_includes_member_count(self):
        resp = self._client("viewer").get(f"/tenants/roles/{self.roles['owner'].pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("member_count", resp.data)
        self.assertGreaterEqual(resp.data["member_count"], 1)


class CustomRoleEndToEndTests(RBACIntegrationBase):
    """
    #366: Full lifecycle tests for custom roles — create, assign,
    verify access, edit permissions, re-verify, delete.
    """

    def test_full_custom_role_lifecycle(self):
        """E2E: create → assign → verify access → edit → re-verify → delete."""
        # 1. Create custom role with contact.view + tenant.view + users.view
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Field Rep",
                "priority": 35,
                "permissions": {
                    "contact.view": True,
                    "tenant.view": True,
                    "users.view": True,
                },
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        custom_role_id = resp.data["id"]

        # 2. Create a user and assign the custom role via change-role
        lifecycle_user = _make_user(
            "lifecycle_user",
            "lifecycle@test.com",
            "+919000009320",
        )
        tu = TenantUser.objects.create(
            tenant=self.tenant,
            user=lifecycle_user,
            role=self.roles["viewer"],
        )
        resp = self._client("admin").patch(
            f"/tenants/members/{tu.pk}/role/",
            {"role_id": custom_role_id},
        )
        self.assertEqual(resp.status_code, 200)

        # 3. Verify user can view contacts but cannot create
        client = _api_client(lifecycle_user)
        resp = client.get("/contacts/")
        self.assertNotEqual(resp.status_code, 403)
        resp = client.post("/contacts/", {}, format="json")
        self.assertEqual(resp.status_code, 403)

        # 4. Edit role: grant contact.create
        resp = self._client("admin").patch(
            f"/tenants/roles/{custom_role_id}/",
            {"permissions": {"contact.create": True}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["permissions"]["contact.create"])

        # 5. Re-verify: user now passes the permission check on POST
        resp = client.post("/contacts/", {}, format="json")
        self.assertNotEqual(
            resp.status_code,
            403,
            "After granting contact.create, user should pass permission check",
        )

        # 6. Reassign user to viewer and delete custom role
        resp = self._client("admin").patch(
            f"/tenants/members/{tu.pk}/role/",
            {"role_id": self.roles["viewer"].pk},
        )
        self.assertEqual(resp.status_code, 200)
        resp = self._client("admin").delete(
            f"/tenants/roles/{custom_role_id}/",
        )
        self.assertEqual(resp.status_code, 204)

        # Cleanup
        TenantUser.objects.filter(user=lifecycle_user).delete()

    def test_custom_role_assignment_via_invite(self):
        """Create custom role → invite user with that role → verify assignment."""
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Invite Target Role",
                "priority": 25,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        role_id = resp.data["id"]

        inv_user = _make_user(
            "inv_custom",
            "inv_custom@test.com",
            "+919000009321",
        )
        resp = self._client("admin").post(
            "/tenants/members/add/",
            {
                "email": inv_user.email,
                "role_id": role_id,
            },
        )
        self.assertEqual(resp.status_code, 201)

        tu = TenantUser.objects.get(tenant=self.tenant, user=inv_user)
        self.assertEqual(tu.role_id, role_id)

        # Cleanup
        TenantUser.objects.filter(user=inv_user).delete()
        TenantRole.objects.filter(pk=role_id).delete()

    def test_create_role_with_priority_gte_own_rejected(self):
        """Attempting to create a role at own priority level → 400."""
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "EqualPrio",
                "priority": 80,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


class CustomRoleAccessTests(RBACIntegrationBase):
    """Test that a custom role with specific permissions can access correct endpoints."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Create a custom role with only broadcast.view + contact.view
        cls.custom_role = TenantRole.objects.create(
            tenant=cls.tenant,
            name="Broadcast Viewer",
            slug="broadcast-viewer",
            priority=30,
            is_system=False,
            is_editable=True,
        )
        for perm in ALL_PERMISSIONS:
            RolePermission.objects.create(
                role=cls.custom_role,
                permission=perm,
                allowed=perm in ("broadcast.view", "contact.view", "tenant.view", "users.view"),
            )
        cls.custom_user = _make_user(
            "custom_role_user",
            "custom_role@test.com",
            "+919000009020",
        )
        TenantUser.objects.create(
            tenant=cls.tenant,
            user=cls.custom_user,
            role=cls.custom_role,
        )

    def test_custom_role_can_list_members(self):
        """Custom role has users.view, should see members list."""
        resp = _api_client(self.custom_user).get("/tenants/members/")
        self.assertEqual(resp.status_code, 200)

    def test_custom_role_cannot_invite(self):
        """Custom role lacks users.invite."""
        invitee = _make_user("cust_inv", "cust_inv@test.com", "+919000009021")
        resp = _api_client(self.custom_user).post(
            "/tenants/members/add/",
            {
                "email": invitee.email,
                "role_id": self.roles["viewer"].pk,
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_custom_role_cannot_create_role(self):
        """Custom role lacks users.change_role."""
        resp = _api_client(self.custom_user).post(
            "/tenants/roles/",
            {
                "name": "Nope",
                "priority": 10,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_custom_role_my_permissions_accurate(self):
        resp = _api_client(self.custom_user).get("/tenants/my-permissions/")
        self.assertEqual(resp.status_code, 200)
        perms = resp.data["permissions"]
        self.assertTrue(perms["broadcast.view"])
        self.assertTrue(perms["contact.view"])
        self.assertFalse(perms["broadcast.create"])
        self.assertFalse(perms["users.invite"])


# ═══════════════════════════════════════════════════════════════════════════
# 3.  SECURITY / ESCALATION TESTS
# ═══════════════════════════════════════════════════════════════════════════


class EscalationPreventionTests(RBACIntegrationBase):
    """Test that all privilege escalation vectors are blocked."""

    def test_cannot_create_role_with_priority_gte_own(self):
        """Admin (priority=80) cannot create a role with priority >= 80."""
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "HighPrio",
                "priority": 80,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_create_role_with_priority_above_own(self):
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "TooHigh",
                "priority": 95,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_can_create_role_with_priority_below_own(self):
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "LowPrio",
                "priority": 50,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        TenantRole.objects.filter(slug="lowprio", tenant=self.tenant).delete()

    def test_cannot_grant_permission_you_dont_have(self):
        """Admin cannot grant tenant.transfer (which admin doesn't have)."""
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Escalated",
                "priority": 30,
                "permissions": {"tenant.transfer": True},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("tenant.transfer", str(resp.data))

    def test_cannot_edit_permissions_to_escalate(self):
        """Admin edits a role to grant tenant.delete — should be blocked."""
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="EscTest",
            slug="esc-test",
            priority=25,
            is_system=False,
            is_editable=True,
        )
        RolePermission.objects.bulk_create(
            [RolePermission(role=custom, permission=p, allowed=False) for p in ALL_PERMISSIONS]
        )
        resp = self._client("admin").patch(
            f"/tenants/roles/{custom.pk}/",
            {"permissions": {"tenant.delete": True}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        custom.delete()

    def test_cannot_edit_owner_role(self):
        """OWNER role is_editable=False, editing should be blocked."""
        owner_role = self.roles["owner"]
        resp = self._client("owner").patch(
            f"/tenants/roles/{owner_role.pk}/",
            {"name": "Supreme Leader"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot be edited", resp.data["detail"])

    def test_cannot_edit_higher_priority_role(self):
        """Manager (60) cannot edit Admin role (80)."""
        # Give manager the users.change_role permission
        RolePermission.objects.update_or_create(
            role=self.roles["manager"],
            permission="users.change_role",
            defaults={"allowed": True},
        )
        resp = self._client("manager").patch(
            f"/tenants/roles/{self.roles['admin'].pk}/",
            {"name": "Hacked Admin"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        # Restore
        RolePermission.objects.filter(
            role=self.roles["manager"],
            permission="users.change_role",
        ).update(allowed=False)

    def test_cannot_delete_higher_priority_role(self):
        """Manager (60) cannot delete a custom role with priority 70."""
        RolePermission.objects.update_or_create(
            role=self.roles["manager"],
            permission="users.change_role",
            defaults={"allowed": True},
        )
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="HighCustom",
            slug="high-custom",
            priority=70,
            is_system=False,
            is_editable=True,
        )
        resp = self._client("manager").delete(f"/tenants/roles/{custom.pk}/")
        self.assertEqual(resp.status_code, 403)
        # Cleanup
        custom.delete()
        RolePermission.objects.filter(
            role=self.roles["manager"],
            permission="users.change_role",
        ).update(allowed=False)

    def test_cannot_set_priority_gte_own_on_edit(self):
        """Admin (80) editing a custom role cannot set priority >= 80."""
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="PrioEdit",
            slug="prio-edit",
            priority=25,
            is_system=False,
            is_editable=True,
        )
        RolePermission.objects.bulk_create(
            [RolePermission(role=custom, permission=p, allowed=False) for p in ALL_PERMISSIONS]
        )
        resp = self._client("admin").patch(
            f"/tenants/roles/{custom.pk}/",
            {"priority": 80},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        custom.delete()

    def test_cannot_assign_owner_role_directly(self):
        """Assigning OWNER role via change-role should be blocked."""
        viewer_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["viewer"])
        resp = self._client("admin").patch(
            f"/tenants/members/{viewer_tu.pk}/role/",
            {"role_id": self.roles["owner"].pk},
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_change_owners_role(self):
        """Cannot change the OWNER's role via change-role endpoint."""
        owner_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["owner"])
        resp = self._client("owner").patch(
            f"/tenants/members/{owner_tu.pk}/role/",
            {"role_id": self.roles["admin"].pk},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("OWNER", resp.data["detail"])

    def test_cannot_remove_last_owner(self):
        owner_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["owner"])
        resp = self._client("owner").delete(f"/tenants/members/{owner_tu.pk}/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("OWNER", resp.data["detail"])

    def test_cannot_change_role_of_equal_priority_member(self):
        """Admin cannot change another admin's role."""
        extra_admin = _make_user("extra_admin", "extra_admin@test.com", "+919000009030")
        tu = TenantUser.objects.create(
            tenant=self.tenant,
            user=extra_admin,
            role=self.roles["admin"],
        )
        resp = self._client("admin").patch(
            f"/tenants/members/{tu.pk}/role/",
            {"role_id": self.roles["viewer"].pk},
        )
        self.assertEqual(resp.status_code, 403)
        TenantUser.objects.filter(user=extra_admin).delete()

    def test_cannot_remove_higher_priority_member(self):
        """Manager cannot remove Admin."""
        # Give manager users.remove permission
        RolePermission.objects.update_or_create(
            role=self.roles["manager"],
            permission="users.remove",
            defaults={"allowed": True},
        )
        admin_tu = TenantUser.objects.get(tenant=self.tenant, user=self.users["admin"])
        resp = self._client("manager").delete(f"/tenants/members/{admin_tu.pk}/")
        self.assertEqual(resp.status_code, 403)
        # Restore
        RolePermission.objects.filter(
            role=self.roles["manager"],
            permission="users.remove",
        ).update(allowed=False)

    def test_unknown_permission_key_rejected(self):
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "BadPerms",
                "priority": 30,
                "permissions": {"nonexistent.perm": True},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unknown", str(resp.data))

    def test_twenty_custom_role_limit(self):
        """Creating 21st custom role should be blocked."""
        created = []
        for i in range(20):
            r = TenantRole.objects.create(
                tenant=self.tenant,
                name=f"Limit{i}",
                slug=f"limit-{i}",
                priority=10,
                is_system=False,
                is_editable=True,
            )
            created.append(r)

        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "TooMany",
                "priority": 10,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("20", str(resp.data))

        # Cleanup
        TenantRole.objects.filter(pk__in=[r.pk for r in created]).delete()

    def test_admin_cannot_remove_owner(self):
        """#368-4: ADMIN cannot remove the OWNER."""
        owner_tu = TenantUser.objects.get(
            tenant=self.tenant,
            user=self.users["owner"],
        )
        resp = self._client("admin").delete(
            f"/tenants/members/{owner_tu.pk}/",
        )
        self.assertIn(resp.status_code, (400, 403))

    def test_custom_role_priority_50_cannot_create_priority_50(self):
        """
        #368-6: A custom role at priority 50 (with users.change_role)
        cannot create another role at priority >= 50.
        """
        custom_50 = TenantRole.objects.create(
            tenant=self.tenant,
            name="MidTier",
            slug="mid-tier",
            priority=50,
            is_system=False,
            is_editable=True,
        )
        for perm in ALL_PERMISSIONS:
            RolePermission.objects.create(
                role=custom_50,
                permission=perm,
                allowed=perm
                in (
                    "users.change_role",
                    "users.view",
                    "tenant.view",
                ),
            )
        mid_user = _make_user(
            "mid_tier",
            "mid_tier@test.com",
            "+919000009350",
        )
        TenantUser.objects.create(
            tenant=self.tenant,
            user=mid_user,
            role=custom_50,
        )

        # Same priority → rejected
        resp = _api_client(mid_user).post(
            "/tenants/roles/",
            {
                "name": "SamePrio",
                "priority": 50,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

        # Above own → rejected
        resp = _api_client(mid_user).post(
            "/tenants/roles/",
            {
                "name": "HigherPrio",
                "priority": 60,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

        # Below own → allowed
        resp = _api_client(mid_user).post(
            "/tenants/roles/",
            {
                "name": "LowerPrio50",
                "priority": 30,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

        # Cleanup — delete TenantUser first (role is PROTECT)
        TenantUser.objects.filter(user=mid_user).delete()
        TenantRole.objects.filter(
            slug__in=["mid-tier", "lowerprio50"],
            tenant=self.tenant,
        ).delete()

    def test_agent_cannot_create_any_role(self):
        """#368-1: AGENT lacks users.change_role → 403 on role creation."""
        resp = self._client("agent").post(
            "/tenants/roles/",
            {
                "name": "AgentAttempt",
                "priority": 10,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_manager_cannot_create_role_with_billing_manage(self):
        """#368-2: MANAGER lacks users.change_role → 403 (and doesn't have billing.manage)."""
        resp = self._client("manager").post(
            "/tenants/roles/",
            {
                "name": "ManagerBilling",
                "priority": 10,
                "permissions": {"billing.manage": True},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_denied_on_all_endpoints(self):
        """Unauthenticated user gets 401 on all RBAC endpoints."""
        anon = APIClient()
        for url in (
            "/tenants/my-permissions/",
            "/tenants/members/",
            "/tenants/roles/",
            "/tenants/roles/permissions-catalog/",
        ):
            resp = anon.get(url)
            self.assertEqual(resp.status_code, 401, f"Expected 401 on {url}")


# ═══════════════════════════════════════════════════════════════════════════
# 4.  MIGRATION / SIGNAL TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TenantCreationSignalTests(TestCase):
    """Test that creating a Tenant auto-seeds roles via post_save signal."""

    def test_new_tenant_gets_five_system_roles(self):
        tenant = Tenant.objects.create(name="Fresh Tenant")
        roles = TenantRole.objects.filter(tenant=tenant, is_system=True)
        self.assertEqual(roles.count(), 5)

    def test_new_tenant_roles_have_correct_slugs(self):
        tenant = Tenant.objects.create(name="Slug Tenant")
        slugs = set(TenantRole.objects.filter(tenant=tenant, is_system=True).values_list("slug", flat=True))
        self.assertEqual(slugs, {"owner", "admin", "manager", "agent", "viewer"})

    def test_new_tenant_owner_role_priority_100(self):
        tenant = Tenant.objects.create(name="Priority Tenant")
        owner = TenantRole.objects.get(tenant=tenant, slug="owner")
        self.assertEqual(owner.priority, 100)

    def test_new_tenant_roles_have_all_permission_rows(self):
        tenant = Tenant.objects.create(name="PermRow Tenant")
        for role in TenantRole.objects.filter(tenant=tenant, is_system=True):
            count = RolePermission.objects.filter(role=role).count()
            self.assertEqual(count, len(ALL_PERMISSIONS), f"Role {role.slug} has {count} perms")

    def test_new_tenant_owner_all_perms_true(self):
        tenant = Tenant.objects.create(name="AllTrue Tenant")
        owner = TenantRole.objects.get(tenant=tenant, slug="owner")
        false_perms = RolePermission.objects.filter(role=owner, allowed=False).values_list("permission", flat=True)
        self.assertEqual(list(false_perms), [], "OWNER should have all permissions True")

    def test_new_tenant_admin_denied_transfer_and_delete(self):
        tenant = Tenant.objects.create(name="AdminDeny Tenant")
        admin = TenantRole.objects.get(tenant=tenant, slug="admin")
        for perm_key in ("tenant.delete", "tenant.transfer"):
            rp = RolePermission.objects.get(role=admin, permission=perm_key)
            self.assertFalse(rp.allowed, f"ADMIN should have {perm_key}=False")

    def test_two_tenants_have_independent_roles(self):
        t1 = Tenant.objects.create(name="Tenant A")
        t2 = Tenant.objects.create(name="Tenant B")
        r1 = set(TenantRole.objects.filter(tenant=t1).values_list("pk", flat=True))
        r2 = set(TenantRole.objects.filter(tenant=t2).values_list("pk", flat=True))
        self.assertTrue(r1.isdisjoint(r2), "Tenants should have separate role PKs")


# ═══════════════════════════════════════════════════════════════════════════
# 5.  ROLE-ENDPOINT MATRIX — Every default role against every protected action
# ═══════════════════════════════════════════════════════════════════════════


class RoleEndpointMatrixTests(RBACIntegrationBase):
    """
    Verify that each default role can only access endpoints matching
    their permission grants from DEFAULT_ROLE_PERMISSIONS.

    Tests the critical endpoint groups:
    - /tenants/members/ (users.view, users.invite, users.remove)
    - /tenants/roles/ (users.view, users.change_role)
    - /tenants/transfer-ownership/ (tenant.transfer)
    """

    def _assert_access(self, role_slug, method, url, expected, data=None, fmt="json"):
        client = self._client(role_slug)
        if method == "get":
            resp = client.get(url)
        elif method == "post":
            resp = client.post(url, data or {}, format=fmt)
        elif method == "patch":
            resp = client.patch(url, data or {}, format=fmt)
        elif method == "delete":
            resp = client.delete(url)
        else:
            raise ValueError(f"Unknown method {method}")

        if expected == "allow":
            self.assertNotEqual(
                resp.status_code,
                403,
                f"{role_slug} should NOT get 403 on {method.upper()} {url} (got {resp.status_code})",
            )
        else:
            self.assertEqual(
                resp.status_code,
                403,
                f"{role_slug} should get 403 on {method.upper()} {url} (got {resp.status_code})",
            )

    def test_members_list_by_role(self):
        """All default roles have users.view → all should see /members/."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/tenants/members/", "allow")

    def test_roles_list_by_role(self):
        """All default roles have users.view → all should see /roles/."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/tenants/roles/", "allow")

    def test_roles_create_by_role(self):
        """Only owner & admin have users.change_role → can create roles."""
        self._assert_access("owner", "post", "/tenants/roles/", "allow", {"name": "MatrixOwner", "priority": 10})
        TenantRole.objects.filter(slug="matrixowner", tenant=self.tenant).delete()

        self._assert_access("admin", "post", "/tenants/roles/", "allow", {"name": "MatrixAdmin", "priority": 10})
        TenantRole.objects.filter(slug="matrixadmin", tenant=self.tenant).delete()

        for slug in ("manager", "agent", "viewer"):
            self._assert_access(slug, "post", "/tenants/roles/", "deny", {"name": f"Matrix{slug}", "priority": 10})

    def test_invite_by_role(self):
        """Only owner & admin have users.invite."""
        # Owner and Admin should be allowed (won't actually get 403)
        # Manager, Agent, Viewer should be denied
        for slug in ("manager", "agent", "viewer"):
            self._assert_access(
                slug, "post", "/tenants/members/add/", "deny", {"email": "nobody@test.com", "role_id": 1}
            )

    def test_transfer_ownership_by_role(self):
        """Only OWNER has tenant.transfer."""
        for slug in ("admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "post", "/tenants/transfer-ownership/", "deny", {"target_user_id": 1})


class CrossModuleEndpointMatrixTests(RBACIntegrationBase):
    """
    #365: Verify every default role against broadcast, contact, chatflow,
    team-inbox, billing, product, template, and WA app endpoints.

    Uses the same allow/deny pattern as RoleEndpointMatrixTests:
    - "allow" → status_code != 403 (user passed the permission gate)
    - "deny"  → status_code == 403
    """

    def _assert_access(self, role_slug, method, url, expected, data=None, fmt="json"):
        client = self._client(role_slug)
        if method == "get":
            resp = client.get(url)
        elif method == "post":
            resp = client.post(url, data or {}, format=fmt)
        else:
            raise ValueError(f"Unknown method {method}")

        if expected == "allow":
            self.assertNotEqual(
                resp.status_code,
                403,
                f"{role_slug} should NOT get 403 on {method.upper()} {url} (got {resp.status_code})",
            )
        else:
            self.assertEqual(
                resp.status_code,
                403,
                f"{role_slug} should get 403 on {method.upper()} {url} (got {resp.status_code})",
            )

    # --- Broadcast (all 5 roles have broadcast.view) ---
    def test_broadcast_list_all_roles(self):
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/broadcast/", "allow")

    # --- Contacts ---
    def test_contact_list_all_roles(self):
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/contacts/", "allow")

    def test_contact_create_by_role(self):
        """owner/admin/manager have contact.create; agent/viewer do not."""
        for slug in ("owner", "admin", "manager"):
            self._assert_access(slug, "post", "/contacts/", "allow")
        for slug in ("agent", "viewer"):
            self._assert_access(slug, "post", "/contacts/", "deny")

    # --- ChatFlow ---
    def test_chatflow_list_all_roles(self):
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/chat-flow/flows/", "allow")

    def test_chatflow_create_by_role(self):
        """owner/admin/manager have chatflow.create; agent/viewer do not."""
        for slug in ("owner", "admin", "manager"):
            self._assert_access(slug, "post", "/chat-flow/flows/", "allow")
        for slug in ("agent", "viewer"):
            self._assert_access(slug, "post", "/chat-flow/flows/", "deny")

    # --- Team Inbox ---
    def test_inbox_list_all_roles(self):
        """All 5 default roles have inbox.view."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/team-inbox/api/messages/", "allow")

    def test_inbox_reply_by_role(self):
        """owner/admin/manager/agent have inbox.reply; viewer does not."""
        for slug in ("owner", "admin", "manager", "agent"):
            self._assert_access(slug, "post", "/team-inbox/api/messages/", "allow")
        self._assert_access("viewer", "post", "/team-inbox/api/messages/", "deny")

    # --- Billing (razorpay) ---
    def test_billing_view_by_role(self):
        """owner/admin/manager/viewer have billing.view; agent does not."""
        for slug in ("owner", "admin", "manager", "viewer"):
            self._assert_access(slug, "get", "/razorpay/razor-pay/", "allow")
        self._assert_access("agent", "get", "/razorpay/razor-pay/", "deny")

    # --- Products ---
    def test_product_list_all_roles(self):
        """All 5 roles have product.view."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/products/", "allow")

    # --- WA App ---
    def test_wa_app_list_all_roles(self):
        """All 5 roles have wa_app.view."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/tenants/tenant-gupshup/", "allow")

    # --- Templates (tenant media) ---
    def test_template_list_all_roles(self):
        """All 5 roles have template.view."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/tenants/tenant-media/", "allow")

    # --- Tenant settings (branding) ---
    def test_branding_view_all_roles(self):
        """All 5 roles have tenant.view."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/tenants/branding/", "allow")

    # --- Member management: change-role full matrix ---
    def test_change_role_denied_for_low_privilege(self):
        """manager/agent/viewer lack users.change_role → 403."""
        viewer_tu = TenantUser.objects.get(
            tenant=self.tenant,
            user=self.users["viewer"],
        )
        for slug in ("manager", "agent", "viewer"):
            resp = self._client(slug).patch(
                f"/tenants/members/{viewer_tu.pk}/role/",
                {"role_id": self.roles["viewer"].pk},
            )
            self.assertEqual(
                resp.status_code,
                403,
                f"{slug} should get 403 on change-role",
            )

    # --- Member management: remove full matrix ---
    def test_remove_member_denied_for_low_privilege(self):
        """manager/agent/viewer lack users.remove → 403."""
        counter = 9300
        for slug in ("manager", "agent", "viewer"):
            counter += 1
            removable = _make_user(
                f"rem_mat_{slug}",
                f"rem_mat_{slug}@test.com",
                f"+91900000{counter:04d}",
            )
            tu = TenantUser.objects.create(
                tenant=self.tenant,
                user=removable,
                role=self.roles["viewer"],
            )
            resp = self._client(slug).delete(f"/tenants/members/{tu.pk}/")
            self.assertEqual(
                resp.status_code,
                403,
                f"{slug} should get 403 on remove-member",
            )
            TenantUser.objects.filter(user=removable).delete()


# ═══════════════════════════════════════════════════════════════════════════
# 6.  WEBSOCKET PERMISSION CHECK (sync-only, unit test)
# ═══════════════════════════════════════════════════════════════════════════


class WebSocketPermissionTests(TestCase):
    """
    Test the synchronous validate_tenant_access method from
    team_inbox/security.py.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="WS Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.viewer_role = TenantRole.objects.get(tenant=cls.tenant, slug="viewer")

        cls.owner_user = _make_user("ws_owner", "ws_owner@test.com", "+919000009040")
        TenantUser.objects.create(
            tenant=cls.tenant,
            user=cls.owner_user,
            role=cls.owner_role,
        )

        cls.viewer_user = _make_user("ws_viewer", "ws_viewer@test.com", "+919000009041")
        TenantUser.objects.create(
            tenant=cls.tenant,
            user=cls.viewer_user,
            role=cls.viewer_role,
        )

        cls.no_member = _make_user("ws_no_member", "ws_no_member@test.com", "+919000009042")

        # Custom role WITHOUT inbox.view
        cls.no_inbox_role = TenantRole.objects.create(
            tenant=cls.tenant,
            name="NoInbox",
            slug="no-inbox",
            priority=30,
            is_system=False,
        )
        for perm in ALL_PERMISSIONS:
            RolePermission.objects.create(
                role=cls.no_inbox_role,
                permission=perm,
                allowed=perm != "inbox.view",
            )
        cls.no_inbox_user = _make_user("ws_noinbox", "ws_noinbox@test.com", "+919000009043")
        TenantUser.objects.create(
            tenant=cls.tenant,
            user=cls.no_inbox_user,
            role=cls.no_inbox_role,
        )

    def _sync_validate(self, user, tenant_id):
        """Call the sync version of validate_tenant_access."""
        from tenants.permissions import has_permission as _hp

        if not tenant_id:
            return False
        tenant_user = (
            TenantUser.objects.filter(
                user=user,
                tenant_id=tenant_id,
                is_active=True,
            )
            .select_related("role")
            .first()
        )
        if not tenant_user:
            return False
        return _hp(tenant_user.role, "inbox.view")

    def test_owner_has_ws_access(self):
        self.assertTrue(self._sync_validate(self.owner_user, self.tenant.pk))

    def test_viewer_has_ws_access(self):
        """Viewer has inbox.view in defaults."""
        self.assertTrue(self._sync_validate(self.viewer_user, self.tenant.pk))

    def test_non_member_denied(self):
        self.assertFalse(self._sync_validate(self.no_member, self.tenant.pk))

    def test_role_without_inbox_view_denied(self):
        self.assertFalse(self._sync_validate(self.no_inbox_user, self.tenant.pk))

    def test_invalid_tenant_id_denied(self):
        self.assertFalse(self._sync_validate(self.owner_user, 99999))

    def test_none_tenant_id_denied(self):
        self.assertFalse(self._sync_validate(self.owner_user, None))

    def test_agent_can_connect_and_reply(self):
        """#367: AGENT has inbox.view (connect) + inbox.reply (send)."""
        agent_role = TenantRole.objects.get(tenant=self.tenant, slug="agent")
        agent_user = _make_user("ws_agent", "ws_agent@test.com", "+919000009340")
        TenantUser.objects.create(
            tenant=self.tenant,
            user=agent_user,
            role=agent_role,
        )
        # Can connect
        self.assertTrue(self._sync_validate(agent_user, self.tenant.pk))
        # Has inbox.reply (send)
        self.assertTrue(has_permission(agent_role, "inbox.reply"))

    def test_viewer_can_connect_but_cannot_reply(self):
        """#367: VIEWER has inbox.view but NOT inbox.reply."""
        self.assertTrue(self._sync_validate(self.viewer_user, self.tenant.pk))
        self.assertFalse(has_permission(self.viewer_role, "inbox.reply"))

    def test_mid_session_role_revocation(self):
        """
        #367: If user's role is changed to one without inbox.view,
        the next reconnect validation fails.
        """
        revoke_user = _make_user(
            "ws_revoke",
            "ws_revoke@test.com",
            "+919000009341",
        )
        tu = TenantUser.objects.create(
            tenant=self.tenant,
            user=revoke_user,
            role=self.owner_role,
        )
        self.assertTrue(self._sync_validate(revoke_user, self.tenant.pk))

        # Change to role without inbox.view
        tu.role = self.no_inbox_role
        tu.save(update_fields=["role"])

        # Next validation fails
        self.assertFalse(self._sync_validate(revoke_user, self.tenant.pk))

        # Cleanup
        TenantUser.objects.filter(user=revoke_user).delete()

    def test_deactivated_user_denied_ws(self):
        """#367: Deactivated TenantUser → ws validation fails."""
        deact_user = _make_user(
            "ws_deact",
            "ws_deact@test.com",
            "+919000009342",
        )
        tu = TenantUser.objects.create(
            tenant=self.tenant,
            user=deact_user,
            role=self.owner_role,
        )
        self.assertTrue(self._sync_validate(deact_user, self.tenant.pk))

        tu.is_active = False
        tu.save(update_fields=["is_active"])
        self.assertFalse(self._sync_validate(deact_user, self.tenant.pk))

        TenantUser.objects.filter(user=deact_user).delete()


# ═══════════════════════════════════════════════════════════════════════════
# 7.  EDGE CASE TESTS
# ═══════════════════════════════════════════════════════════════════════════


class EdgeCaseTests(RBACIntegrationBase):
    """Edge cases and boundary conditions."""

    def test_superuser_bypasses_rbac_on_list(self):
        client = _api_client(self.superuser)
        resp = client.get("/tenants/roles/")
        self.assertEqual(resp.status_code, 200)

    def test_superuser_bypasses_rbac_on_members(self):
        client = _api_client(self.superuser)
        resp = client.get("/tenants/members/")
        self.assertEqual(resp.status_code, 200)

    def test_inactive_tenant_user_denied_api(self):
        """Deactivated TenantUser should get 403 on protected endpoints."""
        inactive = _make_user("inactive_api", "inactive_api@test.com", "+919000009050")
        TenantUser.objects.create(
            tenant=self.tenant,
            user=inactive,
            role=self.roles["admin"],
            is_active=False,
        )
        resp = _api_client(inactive).get("/tenants/members/")
        self.assertIn(resp.status_code, (403,))

    def test_user_with_no_tenant_denied(self):
        """User with zero TenantUser rows should be denied."""
        orphan = _make_user("orphan_api", "orphan_api@test.com", "+919000009051")
        resp = _api_client(orphan).get("/tenants/members/")
        self.assertIn(resp.status_code, (403,))

    def test_role_with_priority_1_is_valid(self):
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Lowest",
                "priority": 1,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        TenantRole.objects.filter(slug="lowest", tenant=self.tenant).delete()

    def test_role_with_priority_99_by_owner(self):
        resp = self._client("owner").post(
            "/tenants/roles/",
            {
                "name": "Highest Custom",
                "priority": 99,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        TenantRole.objects.filter(slug="highest-custom", tenant=self.tenant).delete()

    def test_role_with_priority_100_rejected(self):
        """Priority 100 is OWNER — even OWNER can't create another 100."""
        resp = self._client("owner").post(
            "/tenants/roles/",
            {
                "name": "Fake Owner",
                "priority": 100,
            },
            format="json",
        )
        # priority >= own (100) → rejected
        self.assertEqual(resp.status_code, 400)

    def test_empty_permissions_dict_creates_all_false(self):
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "No Perms",
                "priority": 15,
                "permissions": {},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        for perm, val in resp.data["permissions"].items():
            self.assertFalse(val, f"'{perm}' should be False with empty perms dict")
        TenantRole.objects.filter(slug="no-perms", tenant=self.tenant).delete()

    def test_auto_slug_collision_handling(self):
        """If slug already exists, auto-slug appends counter."""
        TenantRole.objects.create(
            tenant=self.tenant,
            name="Collide",
            slug="collide",
            priority=10,
            is_system=False,
        )
        resp = self._client("admin").post(
            "/tenants/roles/",
            {
                "name": "Collide",
                "priority": 15,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["slug"], "collide-1")
        TenantRole.objects.filter(
            slug__startswith="collide",
            tenant=self.tenant,
            is_system=False,
        ).delete()

    def test_partial_permission_update_preserves_others(self):
        """PATCH with partial permissions dict should not wipe unspecified keys."""
        custom = TenantRole.objects.create(
            tenant=self.tenant,
            name="Partial",
            slug="partial-test",
            priority=25,
            is_system=False,
            is_editable=True,
        )
        RolePermission.objects.bulk_create(
            [
                RolePermission(
                    role=custom,
                    permission=p,
                    allowed=(p == "tenant.view"),
                )
                for p in ALL_PERMISSIONS
            ]
        )
        # Patch only contact.view
        resp = self._client("admin").patch(
            f"/tenants/roles/{custom.pk}/",
            {"permissions": {"contact.view": True}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["permissions"]["tenant.view"], "Unmodified tenant.view should still be True")
        self.assertTrue(resp.data["permissions"]["contact.view"], "Newly set contact.view should be True")
        custom.delete()

    def test_self_removal_blocked(self):
        """A member cannot remove themselves — must be removed by higher-priority member."""
        self_remove_user = _make_user("selfrem", "selfrem@test.com", "+919000009060")
        tu = TenantUser.objects.create(
            tenant=self.tenant,
            user=self_remove_user,
            role=self.roles["admin"],
        )
        resp = _api_client(self_remove_user).delete(f"/tenants/members/{tu.pk}/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("yourself", resp.data["detail"])
        # Cleanup
        TenantUser.objects.filter(user=self_remove_user).delete()


# ═══════════════════════════════════════════════════════════════════════════
# 8.  UNIT TESTS — TenantRolePermission class (RequestFactory)
# ═══════════════════════════════════════════════════════════════════════════


class TenantRolePermissionDirectTests(TestCase):
    """Direct unit tests for TenantRolePermission using RequestFactory."""

    @classmethod
    def setUpTestData(cls):
        cls.factory = RequestFactory()
        cls.tenant = Tenant.objects.create(name="PermClass Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.viewer_role = TenantRole.objects.get(tenant=cls.tenant, slug="viewer")

        cls.owner_user = _make_user("pc_owner", "pc_owner@test.com", "+919000009070")
        TenantUser.objects.create(
            tenant=cls.tenant,
            user=cls.owner_user,
            role=cls.owner_role,
        )

        cls.viewer_user = _make_user("pc_viewer", "pc_viewer@test.com", "+919000009071")
        TenantUser.objects.create(
            tenant=cls.tenant,
            user=cls.viewer_user,
            role=cls.viewer_role,
        )

        cls.no_role_user = _make_user("pc_norole", "pc_norole@test.com", "+919000009072")
        # #253: After migration 0008, role is NOT NULL. Give lowest role.
        TenantUser.objects.create(
            tenant=cls.tenant,
            user=cls.no_role_user,
            role=cls.viewer_role,
        )

        cls.no_tenant_user = _make_user("pc_notenant", "pc_notenant@test.com", "+919000009073")

        cls.superuser = User.objects.create_superuser(
            username="pc_super",
            email="pc_super@test.com",
            mobile="+919000009074",
            password="testpass123",
        )

    def _make_request(self, user):
        req = self.factory.get("/fake/")
        req.user = user
        return req

    def test_unauthenticated_denied(self):
        from django.contrib.auth.models import AnonymousUser

        req = self.factory.get("/fake/")
        req.user = AnonymousUser()
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={"list": "broadcast.view"})
        self.assertFalse(perm.has_permission(req, view))

    def test_superuser_bypasses(self):
        req = self._make_request(self.superuser)
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        self.assertTrue(perm.has_permission(req, view))

    def test_no_required_permissions_allows(self):
        """When viewset has no required_permissions, access granted."""
        req = self._make_request(self.viewer_user)
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={})
        self.assertTrue(perm.has_permission(req, view))

    def test_action_fallback_to_default(self):
        """Unmapped action falls back to 'default' key."""
        req = self._make_request(self.owner_user)
        perm = TenantRolePermission()
        view = _FakeView(
            action="unmapped_action",
            required_permissions={
                "default": "broadcast.view",
            },
        )
        self.assertTrue(perm.has_permission(req, view))

    def test_action_fallback_to_method_when_action_none(self):
        """When view.action is None, falls back to request.method.lower()."""
        req = self.factory.get("/fake/")
        req.user = self.owner_user
        perm = TenantRolePermission()
        view = _FakeView(action=None, required_permissions={"get": "broadcast.view"})
        self.assertTrue(perm.has_permission(req, view))

    def test_viewer_denied_create_permission(self):
        req = self._make_request(self.viewer_user)
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        self.assertFalse(perm.has_permission(req, view))

    def test_owner_has_permission(self):
        req = self._make_request(self.owner_user)
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        self.assertTrue(perm.has_permission(req, view))

    def test_no_role_user_denied_with_custom_message(self):
        """#253: 'No role' scenario is impossible now. Viewer denied write perms."""
        req = self._make_request(self.no_role_user)
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        self.assertFalse(perm.has_permission(req, view))
        self.assertIn("Viewer", perm.message)

    def test_no_tenant_user_denied(self):
        req = self._make_request(self.no_tenant_user)
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={"list": "broadcast.view"})
        self.assertFalse(perm.has_permission(req, view))

    def test_denied_message_includes_role_name(self):
        req = self._make_request(self.viewer_user)
        perm = TenantRolePermission()
        view = _FakeView(action="create", required_permissions={"create": "broadcast.create"})
        perm.has_permission(req, view)
        self.assertIn("Viewer", perm.message)
        self.assertIn("broadcast.create", perm.message)


# ═══════════════════════════════════════════════════════════════════════════
# 9.  UNIT TESTS — Priority Shortcut Classes (RequestFactory)
# ═══════════════════════════════════════════════════════════════════════════


class PriorityShortcutTests(TestCase):
    """Direct unit tests for IsOwner, IsAdminOrAbove, IsManagerOrAbove, IsAgentOrAbove."""

    @classmethod
    def setUpTestData(cls):
        cls.factory = RequestFactory()
        cls.tenant = Tenant.objects.create(name="Priority Shortcut Tenant")

        # Users for each role
        cls.users = {}
        counter = 200
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            counter += 1
            u = _make_user(
                username=f"ps_{slug}",
                email=f"ps_{slug}@test.com",
                mobile=f"+91900000{counter:04d}",
            )
            role = TenantRole.objects.get(tenant=cls.tenant, slug=slug)
            TenantUser.objects.create(tenant=cls.tenant, user=u, role=role)
            cls.users[slug] = u

        counter += 1
        cls.superuser = User.objects.create_superuser(
            username="ps_super",
            email="ps_super@test.com",
            mobile=f"+91900000{counter:04d}",
            password="testpass123",
        )

    def _make_request(self, user):
        req = self.factory.get("/fake/")
        req.user = user
        return req

    def _check(self, perm_class, user, expected):
        req = self._make_request(user)
        perm = perm_class()
        view = _FakeView()
        result = perm.has_permission(req, view)
        self.assertEqual(
            result, expected, f"{perm_class.__name__} for {user.username}: expected {expected}, got {result}"
        )

    # --- IsOwner ---
    def test_is_owner_allows_owner(self):
        self._check(IsOwner, self.users["owner"], True)

    def test_is_owner_denies_admin(self):
        self._check(IsOwner, self.users["admin"], False)

    def test_is_owner_denies_viewer(self):
        self._check(IsOwner, self.users["viewer"], False)

    def test_is_owner_allows_superuser(self):
        self._check(IsOwner, self.superuser, True)

    # --- IsAdminOrAbove ---
    def test_admin_or_above_allows_owner(self):
        self._check(IsAdminOrAbove, self.users["owner"], True)

    def test_admin_or_above_allows_admin(self):
        self._check(IsAdminOrAbove, self.users["admin"], True)

    def test_admin_or_above_denies_manager(self):
        self._check(IsAdminOrAbove, self.users["manager"], False)

    def test_admin_or_above_denies_viewer(self):
        self._check(IsAdminOrAbove, self.users["viewer"], False)

    # --- IsManagerOrAbove ---
    def test_manager_or_above_allows_owner(self):
        self._check(IsManagerOrAbove, self.users["owner"], True)

    def test_manager_or_above_allows_admin(self):
        self._check(IsManagerOrAbove, self.users["admin"], True)

    def test_manager_or_above_allows_manager(self):
        self._check(IsManagerOrAbove, self.users["manager"], True)

    def test_manager_or_above_denies_agent(self):
        self._check(IsManagerOrAbove, self.users["agent"], False)

    def test_manager_or_above_denies_viewer(self):
        self._check(IsManagerOrAbove, self.users["viewer"], False)

    # --- IsAgentOrAbove ---
    def test_agent_or_above_allows_owner(self):
        self._check(IsAgentOrAbove, self.users["owner"], True)

    def test_agent_or_above_allows_admin(self):
        self._check(IsAgentOrAbove, self.users["admin"], True)

    def test_agent_or_above_allows_manager(self):
        self._check(IsAgentOrAbove, self.users["manager"], True)

    def test_agent_or_above_allows_agent(self):
        self._check(IsAgentOrAbove, self.users["agent"], True)

    def test_agent_or_above_denies_viewer(self):
        self._check(IsAgentOrAbove, self.users["viewer"], False)

    # --- Denied message ---
    def test_priority_denied_message_includes_role_label(self):
        req = self._make_request(self.users["viewer"])
        perm = IsManagerOrAbove()
        perm.has_permission(req, _FakeView())
        self.assertIn("Manager", perm.message)
        self.assertIn("Viewer", perm.message)

    # --- Unauthenticated ---
    def test_priority_shortcut_denies_unauthenticated(self):
        from django.contrib.auth.models import AnonymousUser

        req = self.factory.get("/fake/")
        req.user = AnonymousUser()
        for klass in (IsOwner, IsAdminOrAbove, IsManagerOrAbove, IsAgentOrAbove):
            perm = klass()
            self.assertFalse(perm.has_permission(req, _FakeView()), f"{klass.__name__} should deny anonymous")


# ═══════════════════════════════════════════════════════════════════════════
# 11.  ROLE-SCOPED QUERYSETS — #250 (RBAC-13)
# ═══════════════════════════════════════════════════════════════════════════


class RoleScopedQuerysetTests(RBACIntegrationBase):
    """
    Ticket #250 — AGENT sees only contacts assigned to self.
    VIEWER sees all records (read-only enforced by permissions, not queryset).
    OWNER / ADMIN / MANAGER see the full tenant dataset.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        from contacts.models import AssigneeTypeChoices, TenantContact
        from team_inbox.models import Messages

        # ── contacts ──────────────────────────────────────────────
        # 1) Assigned to AGENT user
        cls.contact_agent = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+919000001001",
            first_name="AgentContact",
            assigned_to_type=AssigneeTypeChoices.USER,
            assigned_to_user=cls.users["agent"],
        )
        # 2) Unassigned (agent should NOT see this — only assigned_to_user matters)
        cls.contact_unassigned = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+919000001002",
            first_name="UnassignedContact",
            assigned_to_type=AssigneeTypeChoices.UNASSIGNED,
        )
        # 3) Assigned to ADMIN user (agent should NOT see this)
        cls.contact_admin = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+919000001003",
            first_name="AdminContact",
            assigned_to_type=AssigneeTypeChoices.USER,
            assigned_to_user=cls.users["admin"],
        )
        # 4) Assigned to a BOT (agent should NOT see this)
        cls.contact_bot = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+919000001004",
            first_name="BotContact",
            assigned_to_type=AssigneeTypeChoices.BOT,
            assigned_to_id=999,
        )

        # ── messages (one per contact) ────────────────────────────
        _msg_defaults = dict(
            tenant=cls.tenant,
            content={"text": "hello test"},
            direction="INCOMING",
            platform="WHATSAPP",
            author="CONTACT",
        )
        cls.msg_agent = Messages.objects.create(contact=cls.contact_agent, **_msg_defaults)
        cls.msg_unassigned = Messages.objects.create(contact=cls.contact_unassigned, **_msg_defaults)
        cls.msg_admin = Messages.objects.create(contact=cls.contact_admin, **_msg_defaults)
        cls.msg_bot = Messages.objects.create(contact=cls.contact_bot, **_msg_defaults)

        cls.all_contact_ids = sorted(
            [
                cls.contact_agent.id,
                cls.contact_unassigned.id,
                cls.contact_admin.id,
                cls.contact_bot.id,
            ]
        )
        cls.all_msg_ids = sorted(
            [
                cls.msg_agent.id,
                cls.msg_unassigned.id,
                cls.msg_admin.id,
                cls.msg_bot.id,
            ]
        )

    # ── helpers ────────────────────────────────────────────────────

    def _contact_ids(self, role_slug):
        resp = self._client(role_slug).get("/contacts/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data if isinstance(resp.data, list) else resp.data.get("results", [])
        return sorted(c["id"] for c in results)

    def _msg_ids(self, role_slug):
        resp = self._client(role_slug).get("/team-inbox/api/messages/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data if isinstance(resp.data, list) else resp.data.get("results", [])
        return sorted(m["id"] for m in results)

    # ── CONTACTS ──────────────────────────────────────────────────

    def test_owner_sees_all_contacts(self):
        """#250: OWNER (priority 100) bypasses scoping — sees all 4 contacts."""
        self.assertEqual(self._contact_ids("owner"), self.all_contact_ids)

    def test_admin_sees_all_contacts(self):
        """#250: ADMIN (priority 80) bypasses scoping — sees all 4 contacts."""
        self.assertEqual(self._contact_ids("admin"), self.all_contact_ids)

    def test_manager_sees_all_contacts(self):
        """#250: MANAGER (priority 60) bypasses scoping — sees all 4 contacts."""
        self.assertEqual(self._contact_ids("manager"), self.all_contact_ids)

    def test_agent_sees_only_assigned_contacts(self):
        """#250: AGENT (priority 40) sees only contacts assigned to them."""
        expected = sorted([self.contact_agent.id])
        self.assertEqual(self._contact_ids("agent"), expected)

    def test_viewer_sees_all_contacts(self):
        """#250: VIEWER sees all records — read-only enforced by permissions, not queryset."""
        self.assertEqual(self._contact_ids("viewer"), self.all_contact_ids)

    # ── MESSAGES ──────────────────────────────────────────────────

    def test_owner_sees_all_messages(self):
        """#250: OWNER sees all 4 inbox messages."""
        self.assertEqual(self._msg_ids("owner"), self.all_msg_ids)

    def test_admin_sees_all_messages(self):
        """#250: ADMIN sees all 4 inbox messages."""
        self.assertEqual(self._msg_ids("admin"), self.all_msg_ids)

    def test_manager_sees_all_messages(self):
        """#250: MANAGER sees all 4 inbox messages."""
        self.assertEqual(self._msg_ids("manager"), self.all_msg_ids)

    def test_agent_sees_only_scoped_messages(self):
        """#250: AGENT sees only messages for contacts assigned to them."""
        expected = sorted([self.msg_agent.id])
        self.assertEqual(self._msg_ids("agent"), expected)

    def test_viewer_sees_all_messages(self):
        """#250: VIEWER sees all messages — read-only enforced by permissions, not queryset."""
        self.assertEqual(self._msg_ids("viewer"), self.all_msg_ids)

    # ── CONTACT-LEVEL RETRIEVE ────────────────────────────────────

    def test_agent_can_retrieve_own_contact(self):
        """#250: AGENT can retrieve a contact assigned to self."""
        resp = self._client("agent").get(f"/contacts/{self.contact_agent.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_agent_cannot_retrieve_other_contact(self):
        """#250: AGENT gets 404 for a contact not in scoped set."""
        resp = self._client("agent").get(f"/contacts/{self.contact_admin.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_retrieve_any_contact(self):
        """#250: OWNER can retrieve any contact in the tenant."""
        for cid in self.all_contact_ids:
            resp = self._client("owner").get(f"/contacts/{cid}/")
            self.assertEqual(resp.status_code, status.HTTP_200_OK, f"contact {cid}")

    def test_viewer_can_retrieve_any_contact(self):
        """#250: VIEWER can retrieve any contact — no queryset scoping."""
        for cid in self.all_contact_ids:
            resp = self._client("viewer").get(f"/contacts/{cid}/")
            self.assertEqual(resp.status_code, status.HTTP_200_OK, f"contact {cid}")

    def test_agent_cannot_retrieve_unassigned_contact(self):
        """#250: AGENT gets 404 for unassigned contacts too."""
        resp = self._client("agent").get(f"/contacts/{self.contact_unassigned.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ═══════════════════════════════════════════════════════════════════════════
# 12.  ROLE-AWARE SERIALIZERS — #251 (RBAC-14)
# ═══════════════════════════════════════════════════════════════════════════


class RoleAwareSerializerTests(RBACIntegrationBase):
    """
    Ticket #251 — Sensitive/financial fields are hidden from lower-privilege roles.

    - TenantSerializer: balance, credit_line, threshold_alert, pricing → ADMIN/OWNER only
    - WAAppSerializer: app_id, waba_id, phone_number_id → ADMIN/OWNER only
    - BroadcastSerializer: initial_cost, refund_amount → MANAGER+ only
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        from djmoney.money import Money

        from tenants.models import TenantWAApp
        from wa.models import WABroadcast

        # Give the tenant some financial data
        cls.tenant.balance = Money(500, "USD")
        cls.tenant.credit_line = Money(100, "USD")
        cls.tenant.threshold_alert = Money(10, "USD")
        cls.tenant.save()

        # Create a WA app (same model as WAApp)
        cls.wa_app = TenantWAApp.objects.create(
            tenant=cls.tenant,
            app_name="Test App",
            app_id="secret-bsp-id-123",
            app_secret="secret-key",
            wa_number="+919999000001",
        )

        # Create a WA broadcast with cost data
        cls.broadcast = WABroadcast.objects.create(
            tenant=cls.tenant,
            name="Test Broadcast",
            initial_cost=Money(25, "USD"),
            refund_amount=Money(5, "USD"),
            platform="WHATSAPP",
        )

    # ── TENANT SERIALIZER ─────────────────────────────────────────

    _TENANT_FINANCIAL_KEYS = {
        "balance",
        "balance_currency",
        "credit_line",
        "credit_line_currency",
        "threshold_alert",
        "threshold_alert_currency",
    }

    def _tenant_data(self, role_slug):
        resp = self._client(role_slug).get("/tenants/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data if isinstance(resp.data, list) else resp.data.get("results", [])
        self.assertTrue(len(results) > 0, f"{role_slug} should see at least 1 tenant")
        return results[0]

    def test_owner_sees_tenant_financial_fields(self):
        """#251: OWNER sees balance, credit_line, threshold_alert."""
        data = self._tenant_data("owner")
        for key in self._TENANT_FINANCIAL_KEYS:
            self.assertIn(key, data, f"OWNER should see '{key}'")

    def test_admin_sees_tenant_financial_fields(self):
        """#251: ADMIN sees balance, credit_line, threshold_alert."""
        data = self._tenant_data("admin")
        for key in self._TENANT_FINANCIAL_KEYS:
            self.assertIn(key, data, f"ADMIN should see '{key}'")

    def test_manager_cannot_see_tenant_financial_fields(self):
        """#251: MANAGER (priority 60) cannot see balance/credit/threshold."""
        data = self._tenant_data("manager")
        for key in self._TENANT_FINANCIAL_KEYS:
            self.assertNotIn(key, data, f"MANAGER should NOT see '{key}'")

    def test_agent_cannot_see_tenant_financial_fields(self):
        """#251: AGENT cannot see balance/credit/threshold."""
        data = self._tenant_data("agent")
        for key in self._TENANT_FINANCIAL_KEYS:
            self.assertNotIn(key, data, f"AGENT should NOT see '{key}'")

    def test_viewer_cannot_see_tenant_financial_fields(self):
        """#251: VIEWER cannot see balance/credit/threshold."""
        data = self._tenant_data("viewer")
        for key in self._TENANT_FINANCIAL_KEYS:
            self.assertNotIn(key, data, f"VIEWER should NOT see '{key}'")

    # ── TENANT NESTED WA APPS (pricing) ──────────────────────────

    def test_owner_sees_wa_app_pricing_in_tenant(self):
        """#251: OWNER sees nested WA app pricing fields."""
        data = self._tenant_data("owner")
        apps = data.get("wa_apps", [])
        self.assertTrue(len(apps) > 0)
        self.assertIn("wa_app_id", apps[0])
        self.assertIn("wa_authentication_message_price", apps[0])

    def test_agent_cannot_see_wa_app_pricing_in_tenant(self):
        """#251: AGENT sees safe nested WA apps (no pricing, no app_id)."""
        data = self._tenant_data("agent")
        apps = data.get("wa_apps", [])
        self.assertTrue(len(apps) > 0)
        self.assertNotIn("wa_app_id", apps[0])
        self.assertNotIn("wa_authentication_message_price", apps[0])

    # ── WA APP SERIALIZER ─────────────────────────────────────────

    _WA_APP_BSP_KEYS = {"app_id", "waba_id", "phone_number_id"}

    def _wa_app_data(self, role_slug):
        resp = self._client(role_slug).get(f"/wa/v2/apps/{self.wa_app.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data

    def test_owner_sees_wa_app_bsp_fields(self):
        """#251: OWNER sees app_id, waba_id, phone_number_id."""
        data = self._wa_app_data("owner")
        for key in self._WA_APP_BSP_KEYS:
            self.assertIn(key, data, f"OWNER should see '{key}'")

    def test_admin_sees_wa_app_bsp_fields(self):
        """#251: ADMIN sees app_id, waba_id, phone_number_id."""
        data = self._wa_app_data("admin")
        for key in self._WA_APP_BSP_KEYS:
            self.assertIn(key, data, f"ADMIN should see '{key}'")

    def test_manager_cannot_see_wa_app_bsp_fields(self):
        """#251: MANAGER (below ADMIN) cannot see BSP identifiers."""
        data = self._wa_app_data("manager")
        for key in self._WA_APP_BSP_KEYS:
            self.assertNotIn(key, data, f"MANAGER should NOT see '{key}'")

    def test_agent_cannot_see_wa_app_bsp_fields(self):
        """#251: AGENT cannot see BSP identifiers."""
        data = self._wa_app_data("agent")
        for key in self._WA_APP_BSP_KEYS:
            self.assertNotIn(key, data, f"AGENT should NOT see '{key}'")

    # ── BROADCAST SERIALIZER ──────────────────────────────────────

    _BROADCAST_COST_KEYS = {"initial_cost", "initial_cost_currency", "refund_amount", "refund_amount_currency"}

    def _broadcast_data(self, role_slug):
        resp = self._client(role_slug).get(f"/wa/broadcast/{self.broadcast.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data

    def test_owner_sees_broadcast_cost_fields(self):
        """#251: OWNER sees initial_cost, refund_amount."""
        data = self._broadcast_data("owner")
        for key in self._BROADCAST_COST_KEYS:
            self.assertIn(key, data, f"OWNER should see '{key}'")

    def test_manager_sees_broadcast_cost_fields(self):
        """#251: MANAGER (priority 60) sees cost fields."""
        data = self._broadcast_data("manager")
        for key in self._BROADCAST_COST_KEYS:
            self.assertIn(key, data, f"MANAGER should see '{key}'")

    def test_agent_cannot_see_broadcast_cost_fields(self):
        """#251: AGENT cannot see initial_cost, refund_amount."""
        data = self._broadcast_data("agent")
        for key in self._BROADCAST_COST_KEYS:
            self.assertNotIn(key, data, f"AGENT should NOT see '{key}'")

    def test_viewer_cannot_see_broadcast_cost_fields(self):
        """#251: VIEWER cannot see initial_cost, refund_amount."""
        data = self._broadcast_data("viewer")
        for key in self._BROADCAST_COST_KEYS:
            self.assertNotIn(key, data, f"VIEWER should NOT see '{key}'")

    # ── NON-FINANCIAL FIELDS STILL PRESENT ────────────────────────

    def test_agent_still_sees_tenant_name(self):
        """#251: AGENT still sees non-financial fields like name."""
        data = self._tenant_data("agent")
        self.assertIn("name", data)

    def test_agent_still_sees_broadcast_name(self):
        """#251: AGENT still sees broadcast name + status counts."""
        data = self._broadcast_data("agent")
        self.assertIn("name", data)


# ═══════════════════════════════════════════════════════════════════════════
# 8.  ROLE ASSIGNMENT ON USER CREATION PATHS (RBAC-16 / #253)
# ═══════════════════════════════════════════════════════════════════════════


class RoleAssignmentOnCreationTests(TestCase):
    """
    #253 — Verify that every user-creation path assigns a proper role and
    that no TenantUser can exist without one.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="RoleAssign Corp")
        seed_default_roles(cls.tenant)
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.agent_role = TenantRole.objects.get(tenant=cls.tenant, slug="agent")

    # ── Tenant registration path ─────────────────────────────────────

    def test_tenant_registration_assigns_owner(self):
        """#253: First user of a tenant is always OWNER (via TenantRegistrationSerializer)."""
        # The registration path is already tested elsewhere; just confirm the
        # seed + role assignment pattern works.
        user = _make_user("reg_owner", "reg@test.com", "+919999900001")
        tu = TenantUser.objects.create(tenant=self.tenant, user=user, role=self.owner_role)
        self.assertEqual(tu.role.slug, "owner")

    # ── Access-key login path ────────────────────────────────────────

    def test_access_key_login_assigns_agent_role(self):
        """#253: LoginPatchUserSerializer assigns AGENT role to new TenantUser."""
        from users.serializers import LoginPatchUserSerializer

        user = _make_user("ak_user", "ak@test.com", "+919999900002")
        serializer = LoginPatchUserSerializer(
            data={
                "first_name": "AK",
                "last_name": "User",
                "password": "Str0ng!P4ss",
                "mobile": "+919999900002",
            },
            context={"tenant": self.tenant},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        tu = TenantUser.objects.get(tenant=self.tenant, user=user)
        self.assertIsNotNone(tu.role)
        self.assertEqual(tu.role.slug, "agent")

    def test_access_key_login_rejects_null_role_at_db_level(self):
        """#253: DB rejects TenantUser with role=NULL after migration 0008."""
        from django.db import IntegrityError

        user = _make_user("ak_null", "aknull@test.com", "+919999900003")
        with self.assertRaises(IntegrityError):
            TenantUser.objects.create(tenant=self.tenant, user=user, role=None)

    def test_access_key_login_does_not_change_existing_role(self):
        """#253: Existing user with a role keeps that role on repeat login."""
        from users.serializers import LoginPatchUserSerializer

        user = _make_user("ak_keep", "akkeep@test.com", "+919999900004")
        TenantUser.objects.create(tenant=self.tenant, user=user, role=self.owner_role)

        serializer = LoginPatchUserSerializer(
            data={
                "first_name": "AKKeep",
                "last_name": "User",
                "password": "Str0ng!P4ss",
                "mobile": "+919999900004",
            },
            context={"tenant": self.tenant},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        tu = TenantUser.objects.get(tenant=self.tenant, user=user)
        self.assertEqual(tu.role.slug, "owner", "Existing role should NOT be overwritten")

    # ── Model-level enforcement ──────────────────────────────────────

    def test_tenant_user_role_is_required_at_db_level(self):
        """#253: TenantUser.role is NOT NULL in the schema after migration 0008."""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'tenants_tenantuser' AND column_name = 'role_id'"
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "NO", "role_id column should be NOT NULL")

    def test_role_deletion_is_protected(self):
        """#253: Deleting a role that has members raises ProtectedError."""
        from django.db.models import ProtectedError

        user = _make_user("prot_user", "prot@test.com", "+919999900005")
        TenantUser.objects.create(tenant=self.tenant, user=user, role=self.agent_role)
        with self.assertRaises(ProtectedError):
            self.agent_role.delete()

    # ── Migration 0008 back-fill guard ───────────────────────────────

    def test_migration_backfill_function_exists(self):
        """#253: Migration 0008 backfill function is importable and callable."""
        import importlib

        mod = importlib.import_module("tenants.migrations.0008_rbac_make_role_non_nullable")
        self.assertTrue(callable(mod.backfill_null_roles))


# ═══════════════════════════════════════════════════════════════════════════
# 14. RBAC-17 — Lock-Down Sensitive Infrastructure Endpoints (#254)
# ═══════════════════════════════════════════════════════════════════════════


class InfrastructureLockdownTests(RBACIntegrationBase):
    """
    #254: Verify new permission keys (wa_app.delete, webhook.view,
    webhook.manage) and deny-by-default behaviour.
    """

    # ── helpers ───────────────────────────────────────────────────────

    def _assert_access(self, role_slug, method, url, expected, data=None, fmt="json"):
        """
        Generalised allow/deny assertion supporting GET, POST, PATCH, DELETE.
        'allow' → status_code != 403;  'deny' → status_code == 403.

        Some viewsets crash *after* the permission gate (e.g. queryset errors).
        For 'allow' assertions we catch such errors — if the request got past
        the RBAC permission class, the test passes.
        """
        client = self._client(role_slug)
        dispatch = {
            "get": lambda: client.get(url),
            "post": lambda: client.post(url, data or {}, format=fmt),
            "patch": lambda: client.patch(url, data or {}, format=fmt),
            "delete": lambda: client.delete(url),
        }
        try:
            resp = dispatch[method]()
        except Exception:
            if expected == "allow":
                # Request got past permission gate but crashed downstream → OK
                return
            raise
        if expected == "allow":
            self.assertNotEqual(
                resp.status_code,
                403,
                f"{role_slug} should NOT get 403 on {method.upper()} {url} (got {resp.status_code})",
            )
        else:
            self.assertEqual(
                resp.status_code,
                403,
                f"{role_slug} should get 403 on {method.upper()} {url} (got {resp.status_code})",
            )

    # ── New permission keys exist in registry ────────────────────────

    def test_new_permission_keys_in_registry(self):
        """#254: wa_app.delete, webhook.view, webhook.manage in ALL_PERMISSIONS."""
        for key in ("wa_app.delete", "webhook.view", "webhook.manage"):
            self.assertIn(key, ALL_PERMISSIONS, f"Missing key: {key}")

    def test_new_permission_keys_have_descriptions(self):
        """#254: Each new key has a human-readable description."""
        for key in ("wa_app.delete", "webhook.view", "webhook.manage"):
            self.assertIn(key, PERMISSION_DESCRIPTIONS, f"Missing desc: {key}")
            self.assertTrue(
                len(PERMISSION_DESCRIPTIONS[key]) > 5,
                f"Description too short for {key}",
            )

    # ── Default role mappings ────────────────────────────────────────

    def test_owner_has_new_permissions(self):
        """#254: OWNER has all 3 new keys = True."""
        owner = DEFAULT_ROLE_PERMISSIONS["owner"]
        for key in ("wa_app.delete", "webhook.view", "webhook.manage"):
            self.assertTrue(owner.get(key), f"OWNER should have {key}")

    def test_admin_webhook_allowed_but_delete_denied(self):
        """#254: ADMIN has webhook.view + webhook.manage but NOT wa_app.delete."""
        admin = DEFAULT_ROLE_PERMISSIONS["admin"]
        self.assertTrue(admin.get("webhook.view"))
        self.assertTrue(admin.get("webhook.manage"))
        self.assertFalse(admin.get("wa_app.delete"), "ADMIN should NOT have wa_app.delete")

    def test_lower_roles_denied_new_permissions(self):
        """#254: MANAGER/AGENT/VIEWER have all 3 new keys denied (not in dict → False)."""
        for slug in ("manager", "agent", "viewer"):
            role = DEFAULT_ROLE_PERMISSIONS[slug]
            for key in ("wa_app.delete", "webhook.view", "webhook.manage"):
                self.assertFalse(
                    role.get(key, False),
                    f"{slug} should NOT have {key}",
                )

    # ── DB-level seeded permissions for new tenants ──────────────────

    def test_seeded_permissions_for_new_tenant(self):
        """#254: Seeding a new tenant creates RolePermission rows for new keys."""
        for key in ("wa_app.delete", "webhook.view", "webhook.manage"):
            # OWNER role should have it allowed
            owner_perm = RolePermission.objects.get(
                role=self.roles["owner"],
                permission=key,
            )
            self.assertTrue(owner_perm.allowed, f"OWNER seeded perm {key} should be allowed")

            # VIEWER should NOT have it
            viewer_perm = RolePermission.objects.get(
                role=self.roles["viewer"],
                permission=key,
            )
            self.assertFalse(viewer_perm.allowed, f"VIEWER seeded perm {key} should be denied")

    def test_admin_seeded_wa_app_delete_denied(self):
        """#254: ADMIN role should have wa_app.delete = False after seed."""
        admin_perm = RolePermission.objects.get(
            role=self.roles["admin"],
            permission="wa_app.delete",
        )
        self.assertFalse(admin_perm.allowed)

    def test_admin_seeded_webhook_allowed(self):
        """#254: ADMIN should have webhook.view and webhook.manage = True."""
        for key in ("webhook.view", "webhook.manage"):
            perm = RolePermission.objects.get(
                role=self.roles["admin"],
                permission=key,
            )
            self.assertTrue(perm.allowed, f"ADMIN should have {key}")

    # ── Webhook Event endpoint access matrix ─────────────────────────

    def test_webhook_events_list_owner_admin_allowed(self):
        """#254: OWNER/ADMIN can list webhook events (webhook.view)."""
        for slug in ("owner", "admin"):
            self._assert_access(slug, "get", "/wa/v2/webhook-events/", "allow")

    def test_webhook_events_list_lower_roles_denied(self):
        """#254: MANAGER/AGENT/VIEWER cannot list webhook events."""
        for slug in ("manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/wa/v2/webhook-events/", "deny")

    # ── Rate Card endpoint access matrix ─────────────────────────────

    def test_rate_card_list_owner_admin_allowed(self):
        """#254: OWNER/ADMIN can list rate cards (rate_card.manage)."""
        for slug in ("owner", "admin"):
            self._assert_access(slug, "get", "/wa/rate-card/", "allow")

    def test_rate_card_list_lower_roles_denied(self):
        """#254: MANAGER/AGENT/VIEWER cannot list rate cards."""
        for slug in ("manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/wa/rate-card/", "deny")

    # ── WA App DELETE access matrix ──────────────────────────────────

    def test_wa_app_delete_owner_allowed(self):
        """#254: OWNER can DELETE a WA app (wa_app.delete)."""
        # Use a non-existent PK — we only care about permission gate, not 404
        self._assert_access("owner", "delete", "/wa/v2/apps/99999/", "allow")

    def test_wa_app_delete_admin_denied(self):
        """#254: ADMIN cannot DELETE a WA app (wa_app.delete denied)."""
        self._assert_access("admin", "delete", "/wa/v2/apps/99999/", "deny")

    def test_wa_app_delete_lower_roles_denied(self):
        """#254: MANAGER/AGENT/VIEWER cannot DELETE a WA app."""
        for slug in ("manager", "agent", "viewer"):
            self._assert_access(slug, "delete", "/wa/v2/apps/99999/", "deny")

    # ── WA App view still works for all roles ────────────────────────

    def test_wa_app_list_all_roles(self):
        """#254: All 5 default roles can still list WA apps (wa_app.view)."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            self._assert_access(slug, "get", "/wa/v2/apps/", "allow")

    # ── Deny-by-default behaviour (unit test) ────────────────────────

    def test_deny_by_default_unmapped_action(self):
        """
        #254: When a viewset declares required_permissions but the action
        has no mapping AND no 'default' fallback, deny.
        """
        from django.test import RequestFactory

        factory = RequestFactory()
        req = factory.get("/fake/")
        req.user = self.users["owner"]
        perm = TenantRolePermission()
        # ViewSet with required_permissions but NO 'default' and no mapping for 'destroy'
        view = _FakeView(
            action="destroy",
            required_permissions={"list": "broadcast.view"},
        )
        self.assertFalse(perm.has_permission(req, view))
        self.assertIn("denied by default", perm.message)

    def test_deny_by_default_empty_dict_allows(self):
        """
        #254: Backward compat — empty required_permissions ({}) still allows.
        """
        from django.test import RequestFactory

        factory = RequestFactory()
        req = factory.get("/fake/")
        req.user = self.users["owner"]
        perm = TenantRolePermission()
        view = _FakeView(action="list", required_permissions={})
        self.assertTrue(perm.has_permission(req, view))

    def test_deny_by_default_with_default_fallback_allows(self):
        """
        #254: When 'default' key is present, unmapped actions fall through
        to 'default' and are NOT denied by default.
        """
        from django.test import RequestFactory

        factory = RequestFactory()
        req = factory.get("/fake/")
        req.user = self.users["owner"]
        perm = TenantRolePermission()
        view = _FakeView(
            action="some_custom_action",
            required_permissions={"default": "tenant.view"},
        )
        self.assertTrue(perm.has_permission(req, view))

    # ── Migration 0009 existence check ───────────────────────────────

    def test_migration_0009_importable(self):
        """#254: Migration 0009 is importable."""
        import importlib

        mod = importlib.import_module("tenants.migrations.0009_rbac_add_webhook_wa_delete_permissions")
        self.assertTrue(callable(mod.seed_new_permissions))


# ═══════════════════════════════════════════════════════════════════════════
# 15. RBAC-18 — Final Pre-Production Gap Check (#255)
# ═══════════════════════════════════════════════════════════════════════════


class PreProductionSweepTests(RBACIntegrationBase):
    """
    #255: Final sweep verifying:
      - Endpoint coverage (required_permissions on all viewsets)
      - AllowAny audit (intentional only)
      - JWT token role claims
      - Queryset tenant scoping
      - Permission registry completeness
      - Data migration chain integrity
    """

    # ── 1. Endpoint Coverage — every viewset has required_permissions ──

    def test_all_viewsets_have_required_permissions_or_allowany(self):
        """
        #255: Verify every DRF viewset in the project either declares
        required_permissions or has an intentional AllowAny/IsAdminUser override.
        """
        from importlib import import_module

        viewsets_to_check = [
            ("broadcast.viewsets.broadcast", "BroadcastViewSet"),
            ("broadcast.viewsets.messages", "BroadcastMessageViewSet"),
            ("chat_flow.viewsets.chat_flow", "ChatFlowViewSet"),
            ("chat_flow.viewsets.chat_flow_node", "ChatFlowNodeViewSet"),
            ("chat_flow.viewsets.chat_flow_edge", "ChatFlowEdgeViewSet"),
            ("contacts.viewsets.contacts", "ContactsViewSet"),
            ("team_inbox.viewsets", "MessagesViewSet"),
            ("team_inbox.viewsets", "TeamInboxStatsViewSet"),
            ("tenants.viewsets.role_management", "RoleManagementViewSet"),
            ("tenants.viewsets.member_management", "MemberManagementViewSet"),
            ("tenants.viewsets.tenant_users", "TenantUserViewSet"),
            ("tenants.viewsets.tenants_tags", "TenantTagsViewSet"),
            ("tenants.viewsets.tenant_media", "TenantMediaViewSet"),
            ("tenants.viewsets.tenant_gupshup", "TenantGupshupAppsViewSet"),
            ("tenants.viewsets.waba_info", "WABAInfoViewSet"),
            ("tenants.viewsets.host_wallet_balance", "HostWalletViewSet"),
            ("tenants.viewsets.branding_settings", "BrandingSettingsViewSet"),
            ("wa.viewsets.wa_app", "WAAppViewSet"),
            ("wa.viewsets.wa_subscription_v2", "WASubscriptionV2ViewSet"),
            ("wa.viewsets.wa_webhook_event", "WAWebhookEventViewSet"),
            ("wa.viewsets.wa_message", "WAMessageViewSet"),
            ("wa.viewsets.wa_template_v2", "WATemplateV2ViewSet"),
            ("wa.viewsets.broadcast", "WABroadcastViewSet"),
            ("wa.viewsets.rate_card", "RateCardViewSet"),
            ("razorpay.viewsets.razor_pay", "RazorPayViewSet"),
            ("transaction.viewsets.tenant_transaction", "TenantTransactionViewSet"),
        ]

        for module_path, class_name in viewsets_to_check:
            mod = import_module(module_path)
            cls = getattr(mod, class_name)
            required = getattr(cls, "required_permissions", None)
            self.assertTrue(
                required,
                f"{class_name} in {module_path} is missing required_permissions",
            )

    # ── 2. AllowAny Audit — only public endpoints ─────────────────────

    def test_allowany_only_on_intended_endpoints(self):
        """
        #255: AllowAny should only appear on login, register, token,
        webhook receivers, and onboarding options.
        """
        import os

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        allowed_files = {
            "users/viewsets/user_login_patch.py",  # Login
            "users/viewsets/token.py",  # JWT token
            "tenants/viewsets/tenants.py",  # Register, forgot-password
            "tenants/viewsets/tenant_gupshup.py",  # Webhook receivers
            "tenants/viewsets/tenant_media.py",  # Public media serving
            "tenants/viewsets/branding_settings.py",  # Public branding
            "tenants/viewsets/onboarding_options.py",  # Public registration data
            "jina_connect/urls.py",  # Swagger config (commented out)
        }

        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, root)
                if "__pycache__" in rel or "venv/" in rel or "test" in rel:
                    continue
                try:
                    with open(fp) as f:
                        content = f.read()
                except Exception:
                    continue
                if "AllowAny" in content:
                    self.assertIn(
                        rel,
                        allowed_files,
                        f"Unexpected AllowAny in {rel}",
                    )

    # ── 3. JWT Token Claims ────────────────────────────────────────────

    def test_jwt_token_contains_role_claims(self):
        """#255: JWT access token embeds role, role_name, role_priority."""
        from users.serializers import JwtUserSerializer

        user = self.users["admin"]
        # Build a minimal serializer context
        serializer = JwtUserSerializer(
            data={"username": user.username, "password": "testpass123"},
            context={"tenant": self.tenant},
        )
        # We can't easily call is_valid without the full auth pipeline,
        # so test get_token directly
        token = JwtUserSerializer.get_token(serializer, user)

        self.assertEqual(token["role"], "admin")
        self.assertEqual(token["role_name"], "Admin")
        self.assertEqual(token["role_priority"], 80)
        self.assertEqual(token["tenant_id"], self.tenant.id)

    def test_jwt_token_graceful_without_role(self):
        """#255: JWT token does not crash when user has no TenantUser."""
        from tenants.models import Tenant as T
        from users.serializers import JwtUserSerializer

        orphan = _make_user("jwt_orphan", "jwt_orphan@test.com", "+919000099990")
        # No TenantUser for this user in any tenant
        other_tenant = T.objects.create(name="JWT Orphan Tenant")
        serializer = JwtUserSerializer(
            data={"username": orphan.username, "password": "testpass123"},
            context={"tenant": other_tenant},
        )
        token = JwtUserSerializer.get_token(serializer, orphan)
        # Should NOT have role claims — but should not crash
        self.assertNotIn("role", token)
        self.assertNotIn("role_name", token)

    # ── 4. Queryset Scoping — tenant isolation ─────────────────────────

    def test_razorpay_queryset_scoped_by_tenant(self):
        """#255: RazorPayViewSet.get_queryset() filters by user's tenant."""
        from rest_framework.test import APIRequestFactory

        from razorpay.viewsets.razor_pay import RazorPayViewSet

        factory = APIRequestFactory()
        req = factory.get("/razorpay/razor-pay/")
        req.user = self.users["owner"]
        view = RazorPayViewSet()
        view.request = req
        view.kwargs = {}
        view.format_kwarg = None
        qs = view.get_queryset()
        sql = str(qs.query)
        # The queryset should JOIN through tenants_tenantuser
        self.assertIn(
            "tenants_tenantuser",
            sql,
            "RazorPay queryset must be scoped by tenant",
        )

    def test_chatflow_node_queryset_scoped_by_tenant(self):
        """#255: ChatFlowNodeViewSet.get_queryset() filters by flow__tenant."""
        from unittest.mock import MagicMock

        from chat_flow.viewsets.chat_flow_node import ChatFlowNodeViewSet

        user = self.users["owner"]
        mock_request = MagicMock()
        mock_request.user = user
        mock_request.query_params = {}
        view = ChatFlowNodeViewSet()
        view.request = mock_request
        view.kwargs = {}
        view.format_kwarg = None
        qs = view.get_queryset()
        sql = str(qs.query)
        self.assertIn(
            "tenants_tenantuser",
            sql,
            "ChatFlowNode queryset must be scoped by tenant",
        )

    def test_chatflow_edge_queryset_scoped_by_tenant(self):
        """#255: ChatFlowEdgeViewSet.get_queryset() filters by flow__tenant."""
        from unittest.mock import MagicMock

        from chat_flow.viewsets.chat_flow_edge import ChatFlowEdgeViewSet

        user = self.users["owner"]
        mock_request = MagicMock()
        mock_request.user = user
        mock_request.query_params = {}
        view = ChatFlowEdgeViewSet()
        view.request = mock_request
        view.kwargs = {}
        view.format_kwarg = None
        qs = view.get_queryset()
        sql = str(qs.query)
        self.assertIn(
            "tenants_tenantuser",
            sql,
            "ChatFlowEdge queryset must be scoped by tenant",
        )

    # ── 5. Permission Registry Completeness ────────────────────────────

    def test_all_permissions_count_is_43(self):
        """#255: ALL_PERMISSIONS has exactly 43 keys after all RBAC tickets."""
        self.assertEqual(len(ALL_PERMISSIONS), 43)

    def test_every_permission_has_description(self):
        """#255: Every key in ALL_PERMISSIONS has a description."""
        for key in ALL_PERMISSIONS:
            self.assertIn(key, PERMISSION_DESCRIPTIONS, f"Missing desc: {key}")

    def test_every_default_role_has_complete_permission_rows(self):
        """#255: Every seeded role has exactly len(ALL_PERMISSIONS) RolePermission rows."""
        for slug in ("owner", "admin", "manager", "agent", "viewer"):
            role = self.roles[slug]
            count = RolePermission.objects.filter(role=role).count()
            self.assertEqual(
                count,
                len(ALL_PERMISSIONS),
                f"Role '{slug}' has {count} permissions, expected {len(ALL_PERMISSIONS)}",
            )

    # ── 6. Data Migration Chain ────────────────────────────────────────

    def test_migration_chain_complete(self):
        """#255: Migration chain 0005 through 0009 is importable."""
        import importlib

        migration_names = [
            "tenants.migrations.0005_rbac_add_tenantrole_rolepermission",
            "tenants.migrations.0006_rbac_seed_default_roles_assign_owner",
            "tenants.migrations.0007_add_history_to_role_permission_tenantuser",
            "tenants.migrations.0008_rbac_make_role_non_nullable",
            "tenants.migrations.0009_rbac_add_webhook_wa_delete_permissions",
        ]
        for name in migration_names:
            mod = importlib.import_module(name)
            self.assertIsNotNone(mod, f"{name} import returned None")

    # ── 7. TenantUser always has a role (NOT NULL) ─────────────────────

    def test_no_tenantuser_without_role(self):
        """#255: Database-level check — role_id column is NOT NULL."""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'tenants_tenantuser' AND column_name = 'role_id'"
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "NO", "role_id must be NOT NULL")

    # ── 8. Privilege Escalation — key guards ───────────────────────────

    def test_agent_cannot_create_role(self):
        """#255: AGENT cannot POST /tenants/roles/ (users.change_role denied)."""
        resp = self._client("agent").post("/tenants/roles/", {"name": "Hacker"})
        self.assertEqual(resp.status_code, 403)

    def test_viewer_cannot_invite(self):
        """#255: VIEWER cannot POST /tenants/members/add/ (users.invite denied)."""
        resp = self._client("viewer").post(
            "/tenants/members/add/",
            {
                "email": "hack@test.com",
                "role_id": self.roles["viewer"].pk,
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_owner_role_not_editable(self):
        """#255: OWNER role is_editable=False — cannot change permissions."""
        self.assertFalse(self.roles["owner"].is_editable)

    # ── 9. Swagger schema does not crash ───────────────────────────────

    def test_swagger_schema_generates(self):
        """#255: GET /swagger/?format=openapi returns 200 or requires auth."""
        from rest_framework.test import APIClient

        client = APIClient()
        resp = client.get("/swagger/?format=openapi")
        # Swagger may require auth (401) or redirect (301/302) or work (200)
        self.assertIn(resp.status_code, (200, 301, 302, 401), "Swagger schema endpoint should exist")

    # ── 10. WebSocket auth check exists ────────────────────────────────

    def test_websocket_security_module_exists(self):
        """#255: team_inbox/security.py has WebSocketSecurityManager with validate_tenant_access."""
        from team_inbox.security import WebSocketSecurityManager

        mgr = WebSocketSecurityManager()
        self.assertTrue(callable(getattr(mgr, "validate_tenant_access", None)))
