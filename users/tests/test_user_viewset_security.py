"""
Security tests for UserViewSet lockdown (issue #249).

Verifies:
  - Unauthenticated requests return 401
  - Authenticated users only see users within their tenant
  - Sensitive fields hidden from peer users
  - Self-profile returns full fields
  - PATCH restricted to own profile
  - POST (create) disabled

Run:
    python manage.py test users.tests.test_user_viewset_security --verbosity=2 --no-input
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


class UserViewSetSecurityTests(TestCase):
    """Penetration-style tests for the locked-down UserViewSet."""

    @classmethod
    def setUpTestData(cls):
        # ── Tenant A ──────────────────────────────────────────────────
        cls.tenant_a = Tenant.objects.create(name="Tenant A")
        cls.user_a1 = User.objects.create_user(
            username="user_a1", email="a1@test.com",
            mobile="+919100000001", password="testpass123",
        )
        cls.user_a2 = User.objects.create_user(
            username="user_a2", email="a2@test.com",
            mobile="+919100000002", password="testpass123",
        )
        role_a = TenantRole.objects.get(tenant=cls.tenant_a, slug="agent")
        TenantUser.objects.create(tenant=cls.tenant_a, user=cls.user_a1, role=role_a)
        TenantUser.objects.create(tenant=cls.tenant_a, user=cls.user_a2, role=role_a)

        # ── Tenant B ──────────────────────────────────────────────────
        cls.tenant_b = Tenant.objects.create(name="Tenant B")
        cls.user_b1 = User.objects.create_user(
            username="user_b1", email="b1@test.com",
            mobile="+919100000003", password="testpass123",
        )
        role_b = TenantRole.objects.get(tenant=cls.tenant_b, slug="agent")
        TenantUser.objects.create(tenant=cls.tenant_b, user=cls.user_b1, role=role_b)

        # ── Superuser ────────────────────────────────────────────────
        cls.superuser = User.objects.create_superuser(
            username="super_sec", email="super@test.com",
            mobile="+919100000009", password="testpass123",
        )

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    # ──────────────────────────────────────────────────────────────────
    # 1. Unauthenticated → 401
    # ──────────────────────────────────────────────────────────────────
    def test_list_unauthenticated_returns_401(self):
        resp = APIClient().get("/users/user/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_retrieve_unauthenticated_returns_401(self):
        resp = APIClient().get(f"/users/user/{self.user_a1.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_unauthenticated_returns_401(self):
        resp = APIClient().post("/users/user/", {"username": "hack"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_patch_unauthenticated_returns_401(self):
        resp = APIClient().patch(
            f"/users/user/{self.user_a1.pk}/", {"first_name": "Hacked"},
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    # ──────────────────────────────────────────────────────────────────
    # 2. Tenant scoping — only see own tenant's users
    # ──────────────────────────────────────────────────────────────────
    def test_list_returns_only_own_tenant_users(self):
        resp = self._client(self.user_a1).get("/users/user/")
        self.assertEqual(resp.status_code, 200)
        user_ids = {u["id"] for u in resp.data["results"]}
        self.assertIn(self.user_a1.pk, user_ids)
        self.assertIn(self.user_a2.pk, user_ids)
        self.assertNotIn(self.user_b1.pk, user_ids)

    def test_retrieve_cross_tenant_user_returns_404(self):
        resp = self._client(self.user_a1).get(f"/users/user/{self.user_b1.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_superuser_sees_all_users(self):
        resp = self._client(self.superuser).get("/users/user/")
        self.assertEqual(resp.status_code, 200)
        user_ids = {u["id"] for u in resp.data["results"]}
        self.assertIn(self.user_a1.pk, user_ids)
        self.assertIn(self.user_b1.pk, user_ids)

    # ──────────────────────────────────────────────────────────────────
    # 3. Sensitive fields hidden from peers
    # ──────────────────────────────────────────────────────────────────
    def test_list_hides_sensitive_fields(self):
        resp = self._client(self.user_a1).get("/users/user/")
        self.assertEqual(resp.status_code, 200)
        for user_data in resp.data["results"]:
            self.assertNotIn("email", user_data)
            self.assertNotIn("mobile", user_data)
            self.assertNotIn("address", user_data)
            self.assertNotIn("password", user_data)
            self.assertNotIn("birth_date", user_data)
            # Safe fields present
            self.assertIn("id", user_data)
            self.assertIn("username", user_data)
            self.assertIn("first_name", user_data)
            self.assertIn("last_name", user_data)

    def test_retrieve_peer_hides_sensitive_fields(self):
        resp = self._client(self.user_a1).get(f"/users/user/{self.user_a2.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("email", resp.data)
        self.assertNotIn("mobile", resp.data)
        self.assertNotIn("address", resp.data)
        self.assertIn("id", resp.data)
        self.assertIn("username", resp.data)

    # ──────────────────────────────────────────────────────────────────
    # 4. Self-profile shows full fields
    # ──────────────────────────────────────────────────────────────────
    def test_retrieve_self_by_pk_shows_full_fields(self):
        resp = self._client(self.user_a1).get(f"/users/user/{self.user_a1.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("email", resp.data)
        self.assertIn("mobile", resp.data)
        self.assertIn("birth_date", resp.data)
        self.assertIn("address", resp.data)

    def test_retrieve_self_by_me_shows_full_fields(self):
        resp = self._client(self.user_a1).get("/users/user/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("email", resp.data)
        self.assertEqual(resp.data["id"], self.user_a1.pk)
        self.assertEqual(resp.data["email"], "a1@test.com")

    # ──────────────────────────────────────────────────────────────────
    # 5. PATCH restricted to own profile
    # ──────────────────────────────────────────────────────────────────
    def test_patch_own_profile_allowed(self):
        resp = self._client(self.user_a1).patch(
            f"/users/user/{self.user_a1.pk}/",
            {"first_name": "Updated"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["first_name"], "Updated")

    def test_patch_via_me_allowed(self):
        resp = self._client(self.user_a1).patch(
            "/users/user/me/",
            {"last_name": "Via Me"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["last_name"], "Via Me")

    def test_patch_other_user_denied(self):
        resp = self._client(self.user_a1).patch(
            f"/users/user/{self.user_a2.pk}/",
            {"first_name": "Hacked"},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_patch_cross_tenant_user_denied(self):
        resp = self._client(self.user_a1).patch(
            f"/users/user/{self.user_b1.pk}/",
            {"first_name": "CrossHack"},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_patch_cannot_change_username(self):
        resp = self._client(self.user_a1).patch(
            f"/users/user/{self.user_a1.pk}/",
            {"username": "hacked_username"},
        )
        self.assertEqual(resp.status_code, 200)
        self.user_a1.refresh_from_db()
        self.assertEqual(self.user_a1.username, "user_a1")  # unchanged

    # ──────────────────────────────────────────────────────────────────
    # 6. POST (create) disabled
    # ──────────────────────────────────────────────────────────────────
    def test_post_create_disabled(self):
        resp = self._client(self.user_a1).post("/users/user/", {
            "username": "newuser",
            "email": "new@test.com",
            "mobile": "+919100000099",
            "password": "testpass123",
        })
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    # ──────────────────────────────────────────────────────────────────
    # 7. DELETE disabled (http_method_names excludes it)
    # ──────────────────────────────────────────────────────────────────
    def test_delete_disabled(self):
        resp = self._client(self.user_a1).delete(f"/users/user/{self.user_a1.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
