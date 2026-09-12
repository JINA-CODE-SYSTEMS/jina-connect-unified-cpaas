"""Tests for the cross-tenant token path on ``/token/`` (#301).

The tenant a token is scoped to comes from the ``X-ACCESS-KEY`` header, chosen
by the caller, and a superuser was exempt from the membership check with a bare
``pass``. Superuser credentials plus any organisation's access key therefore
produced a token whose claims were identical to one that organisation's own
owner would receive.

These tests pin the two things that make a borrowed token visible — a WARNING
in the log and claims on the token — and the boundaries around them: a
non-superuser is still refused outright, a member's own token is not marked, and
a revoked key stops working.

Run:
    DB_NAME=... .venv/bin/python -m pytest users/tests/test_cross_tenant_token.py
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from tenants.models import Tenant, TenantAccessKey, TenantRole, TenantUser

User = get_user_model()

PASSWORD = "testpass123"
TOKEN_LOGGER = "users.viewsets.token"


class CrossTenantTokenTests(TestCase):
    """A superuser reaching an organisation it does not belong to."""

    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("token_obtain_pair")

        cls.tenant_a = Tenant.objects.create(name="Tenant A")
        cls.tenant_b = Tenant.objects.create(name="Tenant B")

        _, cls.key_a = TenantAccessKey.issue(cls.tenant_a)
        cls.key_b_obj, cls.key_b = TenantAccessKey.issue(cls.tenant_b)

        # Superuser whose only membership is tenant A.
        cls.superuser = User.objects.create_superuser(
            username="ct_super",
            email="ct_super@test.com",
            mobile="+919120000001",
            password=PASSWORD,
        )
        TenantUser.objects.create(
            tenant=cls.tenant_a,
            user=cls.superuser,
            role=TenantRole.objects.get(tenant=cls.tenant_a, slug="owner"),
        )

        # An ordinary member of tenant A, for the non-superuser case.
        cls.member_a = User.objects.create_user(
            username="ct_member_a",
            email="ct_member_a@test.com",
            mobile="+919120000002",
            password=PASSWORD,
        )
        TenantUser.objects.create(
            tenant=cls.tenant_a,
            user=cls.member_a,
            role=TenantRole.objects.get(tenant=cls.tenant_a, slug="agent"),
        )

        # Tenant B's own owner — the token this must stay distinguishable from.
        cls.owner_b = User.objects.create_user(
            username="ct_owner_b",
            email="ct_owner_b@test.com",
            mobile="+919120000003",
            password=PASSWORD,
        )
        TenantUser.objects.create(
            tenant=cls.tenant_b,
            user=cls.owner_b,
            role=TenantRole.objects.get(tenant=cls.tenant_b, slug="owner"),
        )

    def _obtain(self, username, access_key=None):
        client = APIClient()
        headers = {"HTTP_X_ACCESS_KEY": access_key} if access_key else {}
        return client.post(self.url, {"username": username, "password": PASSWORD}, format="json", **headers)

    # ── The acceptance criterion ──────────────────────────────────────

    def test_superuser_with_another_tenants_key_is_logged_and_marked(self):
        """#301: a borrowed token is never issued silently."""
        with self.assertLogs(TOKEN_LOGGER, level="WARNING") as captured:
            response = self._obtain(self.superuser.username, self.key_b)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # The response says so, so a client can show the session is borrowed.
        self.assertTrue(response.data["cross_tenant"])

        # And so does the token, for everything that only ever sees the token.
        claims = AccessToken(response.data["access"])
        self.assertEqual(claims["tenant_id"], self.tenant_b.id)
        self.assertTrue(claims["cross_tenant"])
        self.assertEqual(claims["home_tenant_id"], self.tenant_a.id)

        logged = "\n".join(captured.output)
        self.assertIn("Cross-tenant token issued", logged)
        self.assertIn(f"tenant_id={self.tenant_b.id}", logged)
        self.assertIn(self.superuser.username, logged)

    def test_borrowed_token_differs_from_the_tenants_own_owners(self):
        """#301: the two tokens were indistinguishable; they must not be."""
        owner_claims = AccessToken(self._obtain(self.owner_b.username, self.key_b).data["access"])

        with self.assertLogs(TOKEN_LOGGER, level="WARNING"):
            borrowed_claims = AccessToken(self._obtain(self.superuser.username, self.key_b).data["access"])

        # Same tenant reached — that part of the bypass is unchanged for now.
        self.assertEqual(owner_claims["tenant_id"], borrowed_claims["tenant_id"])

        # But only one of them admits to being borrowed.
        self.assertNotIn("cross_tenant", owner_claims.payload)
        self.assertTrue(borrowed_claims["cross_tenant"])

    # ── Boundaries ────────────────────────────────────────────────────

    def test_superuser_with_its_own_tenants_key_is_not_marked(self):
        """A member's token is an ordinary token, superuser or not."""
        with self.assertNoLogs(TOKEN_LOGGER, level="WARNING"):
            response = self._obtain(self.superuser.username, self.key_a)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("cross_tenant", response.data)

        claims = AccessToken(response.data["access"])
        self.assertEqual(claims["tenant_id"], self.tenant_a.id)
        self.assertNotIn("cross_tenant", claims.payload)

    def test_superuser_without_a_key_gets_its_own_tenant(self):
        """No header means the user's own tenant, which is not a crossing."""
        with self.assertNoLogs(TOKEN_LOGGER, level="WARNING"):
            response = self._obtain(self.superuser.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AccessToken(response.data["access"])["tenant_id"], self.tenant_a.id)

    def test_tenantless_superuser_still_gets_a_token(self):
        """A superuser with no TenantUser anywhere — what ``createsuperuser``
        leaves behind — is not borrowing anything, so it must not be treated as
        a crossing. Treating it as one would reach for the pk of a tenant that
        is None and turn every such login into a 500."""
        loner = User.objects.create_superuser(
            username="ct_loner",
            email="ct_loner@test.com",
            mobile="+919120000004",
            password=PASSWORD,
        )

        with self.assertNoLogs(TOKEN_LOGGER, level="WARNING"):
            response = self._obtain(loner.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = AccessToken(response.data["access"])
        self.assertIsNone(claims["tenant_id"])
        self.assertNotIn("cross_tenant", claims.payload)

    def test_non_superuser_cannot_use_another_tenants_key(self):
        """The membership check still refuses everyone else outright."""
        response = self._obtain(self.member_a.username, self.key_b)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.data)

    def test_revoked_key_cannot_name_a_tenant(self):
        """#301: revocation has to bite on the path the key is used on."""
        self.key_b_obj.revoke()

        response = self._obtain(self.superuser.username, self.key_b)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.data)

    def test_unknown_key_is_rejected(self):
        response = self._obtain(self.superuser.username, "jc_not-a-real-key")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.data)


class LoginPatchAccessKeyTests(TestCase):
    """``/users/user-login-patch/`` provisions accounts on the key alone (#301)."""

    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("user-login-patch-list")
        cls.tenant = Tenant.objects.create(name="Login Patch Tenant")
        cls.key_obj, cls.key = TenantAccessKey.issue(cls.tenant)

    def _create(self, access_key=None, mobile="+919130000001"):
        client = APIClient()
        headers = {"HTTP_X_ACCESS_KEY": access_key} if access_key else {}
        return client.post(
            self.url,
            {"first_name": "Patch", "last_name": "User", "password": "Str0ng!P4ss", "mobile": mobile},
            format="json",
            **headers,
        )

    def test_missing_access_key_is_rejected_not_a_server_error(self):
        """No key meant tenant=None, a created user and a 500 on TenantUser."""
        response = self._create()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(User.objects.filter(mobile="+919130000001").exists())
        self.assertFalse(TenantUser.objects.filter(tenant__isnull=True).exists())

    def test_revoked_access_key_cannot_provision_an_account(self):
        """A leaked key is an account-provisioning primitive — revoking one
        has to take that away, which needs revocation to exist at all."""
        self.key_obj.revoke()

        response = self._create(self.key)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(User.objects.filter(mobile="+919130000001").exists())

    def test_valid_access_key_still_provisions(self):
        """The documented flow keeps working — this fix is not a removal."""
        response = self._create(self.key)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(mobile="+919130000001")
        self.assertTrue(TenantUser.objects.filter(tenant=self.tenant, user=user).exists())
