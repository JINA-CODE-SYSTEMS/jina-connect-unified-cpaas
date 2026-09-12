"""Tests that ``/token/`` refuses to cross a tenant boundary (#327).

The tenant a token is scoped to comes from the ``X-ACCESS-KEY`` header, chosen
by the caller, and a superuser used to be exempt from the membership check.
Superuser credentials plus any organisation's access key therefore produced a
token whose claims were identical to one that organisation's own owner would
receive — unbounded, writable, and needing nothing but the customer's key.

#301 kept that exemption and made it loud, because removing it before there was
another way to do support work would have locked operators out. #300 shipped
that other way: ``POST /impersonate/{tenant_id}/``, read-only, 15 minutes,
non-refreshable, and unusable without a live audit row. So #327 removes the
exemption, and these tests pin the refusal.

The two things they hold onto while doing it: the refusal must not reach a
member using a key for an organisation they do belong to, and it must not reach
a superuser with no membership anywhere — whose token names no organisation, and
who needs ``/token/`` to start an impersonation session at all.

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

# Claims that only ever appeared on a borrowed token. No token issued by
# ``/token/`` may carry one now that a borrowed token cannot be issued.
BORROWED_CLAIMS = ("cross_tenant", "home_tenant_id")


class CrossTenantTokenTests(TestCase):
    """A superuser reaching an organisation it does not belong to: refused."""

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

        # Tenant B's own owner — the token the superuser used to be handed.
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

    def test_superuser_with_another_tenants_key_is_refused(self):
        """#327: the exemption is gone, so this is a 401 and not a token.

        This is the test that fails against the old code, by design — it was
        written the other way round under #301 to pin the bypass.
        """
        response = self._obtain(self.superuser.username, self.key_b)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_refusal_is_the_same_for_a_superuser_as_for_anyone_else(self):
        """Being a superuser buys nothing here any more — same status, same body.

        Pinned as a pair because the whole defect was a superuser taking a
        different branch through this check than everybody else.
        """
        superuser_response = self._obtain(self.superuser.username, self.key_b)
        member_response = self._obtain(self.member_a.username, self.key_b)

        self.assertEqual(superuser_response.status_code, member_response.status_code)
        self.assertEqual(str(superuser_response.data["detail"]), str(member_response.data["detail"]))

    def test_nothing_is_logged_because_nothing_is_issued(self):
        """#301's warning was the audit trail for a token that was still minted.

        There is no token to account for now, so the refusal is an ordinary 401
        and the warning is gone with the path it described.
        """
        with self.assertNoLogs(TOKEN_LOGGER, level="WARNING"):
            response = self._obtain(self.superuser.username, self.key_b)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_tenant_bs_own_owner_is_unaffected(self):
        """The fix refuses the borrower, not the organisation's own members."""
        response = self._obtain(self.owner_b.username, self.key_b)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AccessToken(response.data["access"])["tenant_id"], self.tenant_b.id)

    def test_no_issued_token_carries_the_borrowed_claims(self):
        """``cross_tenant``/``home_tenant_id`` marked a token no one can get now.

        The writer is kept in the module pending a check of the web client, so
        this asserts on the tokens rather than on the code: whichever way that
        check goes, every token ``/token/`` still issues must be unmarked.
        """
        for username, key in (
            (self.superuser.username, self.key_a),
            (self.superuser.username, None),
            (self.owner_b.username, self.key_b),
            (self.member_a.username, self.key_a),
        ):
            with self.subTest(username=username, key=bool(key)):
                response = self._obtain(username, key)
                self.assertEqual(response.status_code, status.HTTP_200_OK)

                claims = AccessToken(response.data["access"]).payload
                for claim in BORROWED_CLAIMS:
                    self.assertNotIn(claim, claims)
                    self.assertNotIn(claim, response.data)

    def test_impersonate_is_the_way_in_and_it_still_works(self):
        """The replacement the removal depends on, driven with a real token.

        If this breaks, #327 has taken away the only way an operator can see a
        customer's organisation, which is the outcome #301 refused to risk.
        """
        login = self._obtain(self.superuser.username, self.key_a)
        self.assertEqual(login.status_code, status.HTTP_200_OK)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = client.post(reverse("impersonation-start", args=[self.tenant_b.pk]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["read_only"])
        self.assertEqual(response.data["organisation"]["id"], self.tenant_b.pk)
        # Time-boxed, and there is nothing to refresh.
        self.assertEqual(response.data["expires_in"], 15 * 60)
        self.assertNotIn("refresh", response.data)

    # ── Boundaries the removal must not cross ─────────────────────────

    def test_superuser_with_its_own_tenants_key_still_works(self):
        """A member's key is their own to use, superuser or not."""
        response = self._obtain(self.superuser.username, self.key_a)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AccessToken(response.data["access"])["tenant_id"], self.tenant_a.id)

    def test_superuser_without_a_key_gets_its_own_tenant(self):
        """No header means the user's own tenant, which is not a crossing."""
        response = self._obtain(self.superuser.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AccessToken(response.data["access"])["tenant_id"], self.tenant_a.id)

    def test_tenantless_superuser_still_gets_a_token(self):
        """A superuser with no TenantUser anywhere — what ``createsuperuser``
        leaves behind — belongs to no organisation, so the membership check has
        nothing to compare against. Its token names no tenant and so reaches no
        customer's data, and it is the token ``/impersonate/`` is started with:
        refusing it would lock a fresh platform admin out of the replacement
        path and make #327 the lockout #301 declined to ship."""
        loner = User.objects.create_superuser(
            username="ct_loner",
            email="ct_loner@test.com",
            mobile="+919120000004",
            password=PASSWORD,
        )

        response = self._obtain(loner.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = AccessToken(response.data["access"])
        self.assertIsNone(claims["tenant_id"])

    def test_tenantless_superuser_still_cannot_name_another_tenant(self):
        """Having no organisation of its own is not a licence to borrow one.

        The exemption above is for a token scoped to nothing; presenting a key
        scopes the token to that organisation, which is the thing being refused.
        """
        loner = User.objects.create_superuser(
            username="ct_loner_key",
            email="ct_loner_key@test.com",
            mobile="+919120000005",
            password=PASSWORD,
        )

        response = self._obtain(loner.username, self.key_b)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.data)

    def test_tenantless_non_superuser_is_still_refused(self):
        """The narrowed exemption must not have widened for ordinary users."""
        stray = User.objects.create_user(
            username="ct_stray",
            email="ct_stray@test.com",
            mobile="+919120000006",
            password=PASSWORD,
        )

        response = self._obtain(stray.username)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.data)

    def test_non_superuser_cannot_use_another_tenants_key(self):
        """The membership check always refused everyone else; it still does."""
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
    """``/users/user-login-patch/`` provisions accounts on the key alone (#301).

    Unchanged by #327. The endpoint still has no tenant authorisation behind the
    shared secret — #301's item 3, reported and not fixed, and deliberately left
    for its own ticket rather than widened into this one.
    """

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
