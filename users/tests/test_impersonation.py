"""Tests for read-only, time-boxed, audited impersonation (#300).

#301 closed the accidental version of this feature: superuser credentials plus
an organisation's ``X-ACCESS-KEY`` produced a token that organisation's own
owner could not be told apart from, unbounded and unrecorded. This is the
deliberate replacement, and these tests pin the four things that make it
bounded rather than a second way in:

* every write is refused while impersonating — **at the API, with the token**,
  not in the UI
* the token expires on its own and cannot be refreshed
* an audit row exists for every session and names the real user
* a non-superuser cannot obtain one for any organisation, including their own

Run:
    DB_NAME=... python3 -m pytest users/tests/test_impersonation.py
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from tenants.models import Tenant, TenantRole, TenantUser
from users.impersonation import (
    IMPERSONATION_TOKEN_LIFETIME,
    WRITE_EXEMPT_VIEW_NAMES,
    issue_impersonation_token,
)
from users.models import ImpersonationSession

User = get_user_model()

PASSWORD = "testpass123"


class ImpersonationTestBase(TestCase):
    """A platform admin, a customer organisation, and that customer's owner."""

    @classmethod
    def setUpTestData(cls):
        cls.customer = Tenant.objects.create(name="Customer Org")
        cls.platform_org = Tenant.objects.create(name="Platform Org")

        cls.admin = User.objects.create_superuser(
            username="imp_admin",
            email="imp_admin@test.com",
            mobile="+919140000001",
            password=PASSWORD,
        )
        TenantUser.objects.create(
            tenant=cls.platform_org,
            user=cls.admin,
            role=TenantRole.objects.get(tenant=cls.platform_org, slug="owner"),
        )

        # The customer's own owner — full rights inside the organisation.
        cls.owner = User.objects.create_user(
            username="imp_owner",
            email="imp_owner@test.com",
            mobile="+919140000002",
            password=PASSWORD,
        )
        TenantUser.objects.create(
            tenant=cls.customer,
            user=cls.owner,
            role=TenantRole.objects.get(tenant=cls.customer, slug="owner"),
        )

    def start_url(self, tenant_id=None):
        return reverse("impersonation-start", args=[tenant_id or self.customer.pk])

    def client_for(self, user):
        """An APIClient holding ``user``'s ordinary access token."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        return client

    def impersonating_client(self, actor=None, tenant=None):
        """An APIClient holding an impersonation token, plus its audit row."""
        raw, session = issue_impersonation_token(actor or self.admin, tenant or self.customer)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client, session, raw


class ImpersonationIssuingTests(ImpersonationTestBase):
    """Who may start a session, and what the token says."""

    def test_superuser_gets_a_read_only_token_scoped_to_the_target(self):
        response = self.client_for(self.admin).post(self.start_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        claims = AccessToken(response.data["access"])
        self.assertEqual(claims["tenant_id"], self.customer.pk)
        # The claim the whole feature keys on, carrying the *real* user.
        self.assertEqual(claims["impersonated_by"], self.admin.pk)
        self.assertEqual(claims["impersonated_by_username"], self.admin.username)
        self.assertEqual(claims["impersonated_tenant_name"], self.customer.name)
        self.assertTrue(claims["read_only"])

    def test_no_refresh_token_is_issued(self):
        """A forgotten tab must not be renewable into a standing key."""
        response = self.client_for(self.admin).post(self.start_url())

        self.assertNotIn("refresh", response.data)
        self.assertEqual(AccessToken(response.data["access"])["token_type"], "access")

    def test_token_is_time_boxed_to_the_impersonation_lifetime(self):
        """Not SIMPLE_JWT's 90-day ACCESS_TOKEN_LIFETIME."""
        response = self.client_for(self.admin).post(self.start_url())

        claims = AccessToken(response.data["access"])
        lifetime = claims["exp"] - claims["iat"]
        self.assertEqual(lifetime, int(IMPERSONATION_TOKEN_LIFETIME.total_seconds()))
        self.assertLessEqual(lifetime, 30 * 60)
        self.assertGreaterEqual(lifetime, 15 * 60)
        self.assertEqual(response.data["expires_in"], lifetime)

    def test_non_superuser_cannot_impersonate_another_organisation(self):
        response = self.client_for(self.owner).post(reverse("impersonation-start", args=[self.platform_org.pk]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ImpersonationSession.objects.exists())

    def test_non_superuser_cannot_impersonate_their_own_organisation(self):
        """Acceptance names this case explicitly: their own is no exception."""
        response = self.client_for(self.owner).post(self.start_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ImpersonationSession.objects.exists())

    def test_anonymous_cannot_impersonate(self):
        response = APIClient().post(self.start_url())

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(ImpersonationSession.objects.exists())

    def test_superuser_flag_is_read_from_the_database_not_the_token(self):
        """A demoted admin's 90-day token must not still mint session keys.

        ``CustomJWTAuthentication`` sets ``is_superuser`` from the token claim,
        so without a database check the window for a revoked platform admin is
        as long as their last token's lifetime.
        """
        client = self.client_for(self.admin)
        self.admin.is_superuser = False
        self.admin.save(update_fields=["is_superuser"])

        response = client.post(self.start_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unknown_organisation_is_a_404_and_writes_no_audit_row(self):
        response = self.client_for(self.admin).post(reverse("impersonation-start", args=[99_999]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(ImpersonationSession.objects.exists())


class ImpersonationAuditTests(ImpersonationTestBase):
    """The audit row, and the fact that the token depends on it."""

    def test_every_session_writes_a_row_naming_the_real_user(self):
        response = self.client_for(self.admin).post(self.start_url())

        session = ImpersonationSession.objects.get(pk=response.data["session_id"])
        self.assertEqual(session.actor, self.admin)
        self.assertEqual(session.actor_username, self.admin.username)
        self.assertEqual(session.tenant, self.customer)
        self.assertEqual(session.tenant_name, self.customer.name)
        self.assertEqual(session.token_jti, AccessToken(response.data["access"])["jti"])
        self.assertIsNotNone(session.started_at)
        self.assertIsNone(session.ended_at)
        self.assertTrue(session.is_live)

    def test_row_names_the_real_user_and_not_the_organisations(self):
        """The point of the record: it must not read as the customer's own act."""
        self.client_for(self.admin).post(self.start_url())

        session = ImpersonationSession.objects.get()
        self.assertNotEqual(session.actor, self.owner)
        self.assertEqual(session.actor_username, "imp_admin")

    def test_audit_row_survives_deletion_of_the_actor(self):
        """An audit trail that disappears with the employee is not one."""
        self.client_for(self.admin).post(self.start_url())
        self.admin.delete()

        session = ImpersonationSession.objects.get()
        self.assertIsNone(session.actor)
        self.assertEqual(session.actor_username, "imp_admin")

    def test_expiry_is_recorded_alongside_the_start(self):
        response = self.client_for(self.admin).post(self.start_url())

        session = ImpersonationSession.objects.get()
        self.assertAlmostEqual(
            (session.expires_at - session.started_at).total_seconds(),
            IMPERSONATION_TOKEN_LIFETIME.total_seconds(),
            delta=5,
        )
        self.assertEqual(response.data["expires_at"], session.expires_at.isoformat())

    def test_token_without_an_audit_row_does_not_authenticate(self):
        """The audit trail is load-bearing, not a side-effect.

        If a row can be deleted or skipped and the token keeps working, the
        claim "an audit row exists for every session" holds only as long as
        nobody tries.
        """
        client, session, _raw = self.impersonating_client()
        session.delete()

        response = client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_ending_a_session_records_it_and_retires_the_token(self):
        client, session, _raw = self.impersonating_client()

        ended = client.post(reverse("impersonation-end"))
        self.assertEqual(ended.status_code, status.HTTP_200_OK)

        session.refresh_from_db()
        self.assertIsNotNone(session.ended_at)
        self.assertFalse(session.is_live)

        # The exit is a server-side control, not a client-side one: the token
        # the browser was holding stops working even if it is kept.
        after = client.get(reverse("user-list"))
        self.assertEqual(after.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_end_is_the_only_write_an_impersonated_session_may_make(self):
        self.assertEqual(WRITE_EXEMPT_VIEW_NAMES, frozenset({"impersonation-end"}))

    def test_end_with_an_ordinary_token_is_a_bad_request(self):
        response = self.client_for(self.admin).post(reverse("impersonation-end"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ImpersonationReadOnlyTests(ImpersonationTestBase):
    """Every write refused, with the token, at the API."""

    def test_reads_work(self):
        """Otherwise the rest of this proves only that the token is broken."""
        client, _session, _raw = self.impersonating_client()

        response = client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patch_is_refused(self):
        """Even on the actor's own profile, which they own outright."""
        client, _session, _raw = self.impersonating_client()

        response = client.patch(reverse("user-detail", args=[self.admin.pk]), {"first_name": "Taken"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.admin.refresh_from_db()
        self.assertNotEqual(self.admin.first_name, "Taken")

    def test_the_same_patch_succeeds_with_an_ordinary_token(self):
        """The same actor, the same endpoint, the same body — so the refusal
        above is about the session being impersonated and nothing else."""
        response = self.client_for(self.admin).patch(
            reverse("user-detail", args=[self.admin.pk]), {"first_name": "Allowed"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, "Allowed")

    def test_post_is_refused_on_a_tenant_endpoint(self):
        client, _session, _raw = self.impersonating_client()

        response = client.post(reverse("tenants:tenant-list"), {"name": "Created While Viewing"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Tenant.objects.filter(name="Created While Viewing").exists())

        # Not 403 for the same superuser holding an ordinary token — whatever
        # the serializer makes of the payload, the request is not refused.
        ordinary = self.client_for(self.admin).post(
            reverse("tenants:tenant-list"), {"name": "Created While Viewing"}, format="json"
        )
        self.assertNotEqual(ordinary.status_code, status.HTTP_403_FORBIDDEN)

    def test_put_is_refused_before_anything_else_looks_at_it(self):
        """The refusal happens in authentication, so it lands before the
        viewset's method whitelist would answer 405."""
        client, _session, _raw = self.impersonating_client()

        response = client.put(reverse("tenants:tenant-detail", args=[self.customer.pk]), {"name": "Renamed"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name, "Customer Org")

    def test_delete_is_refused(self):
        client, _session, _raw = self.impersonating_client()

        response = client.delete(reverse("tenants:tenant-detail", args=[self.customer.pk]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Tenant.objects.filter(pk=self.customer.pk).exists())

    def test_refusal_covers_endpoints_with_no_rbac_permission_class(self):
        """The refusal cannot depend on a viewset opting in.

        ``/users/set-initial-password/`` is AllowAny and has no
        ``required_permissions``, so nothing in the RBAC layer would see this
        request at all. It is refused because the check also runs in
        authentication, which every JWT request passes through.
        """
        client, _session, _raw = self.impersonating_client()

        response = client.post(
            reverse("set-initial-password-list"),
            {"username": self.owner.username, "temporary_password": PASSWORD, "new_password": "An0ther!Pass"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_impersonation_cannot_be_chained(self):
        """Starting a session is itself a write, so a borrowed token cannot
        mint another one — for this organisation or any other."""
        client, _session, _raw = self.impersonating_client()

        response = client.post(reverse("impersonation-start", args=[self.platform_org.pk]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ImpersonationSession.objects.count(), 1)

    def test_refusal_explains_itself(self):
        client, _session, _raw = self.impersonating_client()

        response = client.post(reverse("tenants:tenant-list"), {"name": "Nope"}, format="json")

        self.assertIn("read-only", str(response.data).lower())


class ImpersonationExpiryTests(ImpersonationTestBase):
    """It ends on its own, and nothing renews it."""

    def test_expired_token_is_refused(self):
        raw, session = issue_impersonation_token(self.admin, self.customer)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

        # Past the recorded expiry. The JWT's own ``exp`` does the same job a
        # moment later; this asserts the session record alone is enough.
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["expires_at"])

        response = client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_past_its_own_exp_is_refused(self):
        """Expiry is the token's own, not only the audit row's: the row here
        says the session is live and the request is still refused."""
        with patch("users.impersonation.IMPERSONATION_TOKEN_LIFETIME", timedelta(seconds=-1)):
            raw, session = issue_impersonation_token(self.admin, self.customer)

        session.expires_at = timezone.now() + timedelta(hours=1)
        session.save(update_fields=["expires_at"])
        self.assertTrue(session.is_live)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

        response = client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_impersonation_token_cannot_be_refreshed(self):
        """It is an access token and there is no refresh token to present."""
        response = self.client_for(self.admin).post(self.start_url())
        access = response.data["access"]

        refreshed = APIClient().post(reverse("token_refresh"), {"refresh": access}, format="json")

        self.assertIn(
            refreshed.status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED),
        )
        self.assertNotIn("access", refreshed.data)


class ImpersonationWebSocketTests(TransactionTestCase):
    """A socket has no HTTP method for the read-only rule to bite on.

    ``TeamInboxConsumer`` both reads the inbox and sends messages over one
    connection, so "refuse every non-safe method" never reaches it. An
    impersonation token therefore does not authenticate a socket at all.

    ``TransactionTestCase`` because the authentication path crosses the
    sync/async boundary to reach the database — the same reason
    ``team_inbox/tests/test_jwt_auth.py`` uses it.
    """

    def setUp(self):
        self.customer = Tenant.objects.create(name="WS Customer Org")
        self.admin = User.objects.create_superuser(
            username="imp_ws_admin",
            email="imp_ws_admin@test.com",
            mobile="+919150000001",
            password=PASSWORD,
        )
        self.member = User.objects.create_user(
            username="imp_ws_member",
            email="imp_ws_member@test.com",
            mobile="+919150000002",
            password=PASSWORD,
        )
        TenantUser.objects.create(
            tenant=self.customer,
            user=self.member,
            role=TenantRole.objects.get(tenant=self.customer, slug="owner"),
        )

    def _scope(self, raw_token):
        return {
            "type": "websocket",
            "headers": [(b"authorization", f"Bearer {raw_token}".encode())],
            "query_string": b"",
        }

    def test_websocket_authentication_refuses_an_impersonation_token(self):
        from asgiref.sync import async_to_sync

        from team_inbox.security import WebSocketSecurityManager

        raw, _session = issue_impersonation_token(self.admin, self.customer)

        user = async_to_sync(WebSocketSecurityManager().authenticate_jwt)(self._scope(raw))

        self.assertIsNone(user)

    def test_websocket_authentication_still_accepts_an_ordinary_token(self):
        from asgiref.sync import async_to_sync

        from team_inbox.security import WebSocketSecurityManager

        user = async_to_sync(WebSocketSecurityManager().authenticate_jwt)(
            self._scope(AccessToken.for_user(self.member))
        )

        self.assertIsNotNone(user)
        self.assertEqual(user.pk, self.member.pk)

    def test_team_inbox_consumer_refuses_an_impersonation_token(self):
        from asgiref.sync import async_to_sync
        from django.contrib.auth.models import AnonymousUser

        from team_inbox.consumers import TeamInboxConsumer

        raw, _session = issue_impersonation_token(self.admin, self.customer)
        consumer = TeamInboxConsumer()
        consumer.scope = self._scope(raw)

        async_to_sync(consumer.authenticate_user)()

        self.assertIsInstance(consumer.user, AnonymousUser)
