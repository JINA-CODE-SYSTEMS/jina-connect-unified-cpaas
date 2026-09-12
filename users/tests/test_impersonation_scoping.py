"""An impersonated session reads one organisation, not all of them (#326).

#300 made "view as organisation" read-only, time-boxed and audited, but not
scoped: ``BaseTenantModelViewSet.get_queryset`` returned ``.all()`` for
superusers, and an impersonation token keeps ``is_superuser`` true on purpose —
it has to, or ``TenantRolePermission`` would 403 the reads the feature exists
for. So the session listed every organisation's rows under a banner naming one.

Not an escalation: the actor's own ordinary token already reads everything. But
support staff can answer with the wrong customer's figures, or call a record
missing when it belongs to someone else, and the audit row records which
organisation was *viewed* rather than what was *readable*.

**Every test here creates rows in two organisations and asserts the other
organisation's rows are absent.** Asserting only that the impersonated
organisation's rows are present passes against the unscoped code and so proves
nothing — that is the whole point of the two-organisation setup. Identifying
values are per-organisation and unique in shape (``…-4a7c`` / ``…-9e15``), so an
assertion cannot be satisfied by an unrelated occurrence of the same value
elsewhere in the response.

Run:
    DB_NAME=... python3 -m pytest users/tests/test_impersonation_scoping.py
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from broadcast.models import Broadcast
from chat_flow.models import ChatFlow, ChatFlowNode
from contacts.models import TenantContact
from notifications.models import Notification, NotificationType
from rcs.models import RCSApp, RCSOutboundMessage
from sms.models import SMSApp, SMSOutboundMessage
from tenants.models import Tenant, TenantRole, TenantUser, TenantWAApp, WABAInfo
from users.impersonation import impersonated_tenant_id, issue_impersonation_token

User = get_user_model()

# Per-organisation suffixes. Distinct in shape as well as value so that a match
# cannot come from an id, a count, a timestamp or another organisation's field.
A = "4a7c"
B = "9e15"


def results_of(response):
    """The rows in a DRF list response, paginated or not."""
    data = response.data
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


def ids_in(response):
    return {str(row["id"]) for row in results_of(response)}


class ImpersonatedTenantIdTests(SimpleTestCase):
    """``impersonated_tenant_id`` on its own — including the case no request can
    reach with a token this module issued, which is exactly why it needs a test."""

    def test_an_ordinary_request_is_not_scoped(self):
        self.assertIsNone(impersonated_tenant_id(SimpleNamespace(user=SimpleNamespace())))

    def test_an_impersonated_request_yields_the_organisation_on_the_token(self):
        user = SimpleNamespace(impersonated_by=7, tenant_id=42)

        self.assertEqual(impersonated_tenant_id(SimpleNamespace(user=user)), 42)

    def test_a_borrowed_token_naming_no_organisation_is_refused(self):
        """It must not return None: None means "leave the queryset alone", which
        would hand the session every organisation — #326 by another route."""
        user = SimpleNamespace(impersonated_by=7, tenant_id=None)

        with self.assertRaises(PermissionDenied):
            impersonated_tenant_id(SimpleNamespace(user=user))

    def test_it_does_not_trigger_authentication_on_the_request(self):
        """Reading ``request.auth`` would *run* authentication, and on a request
        with no authenticators that replaces ``request.user`` with
        ``AnonymousUser`` — silently breaking the membership filtering that runs
        after this in ``get_queryset``."""
        django_request = APIRequestFactory().get("/")
        actor = SimpleNamespace(username="actor")
        django_request.user = actor
        from rest_framework.request import Request

        drf_request = Request(django_request)
        drf_request.user = actor

        self.assertIsNone(impersonated_tenant_id(drf_request))
        self.assertIs(drf_request.user, actor)


class ImpersonationScopingTests(TestCase):
    """Two organisations, each with its own rows; one of them impersonated."""

    @classmethod
    def setUpTestData(cls):
        # Neutral fixture names on purpose — this is a public repository.
        cls.org_a = Tenant.objects.create(name=f"Org Alpha {A}")
        cls.org_b = Tenant.objects.create(name=f"Org Beta {B}")
        cls.platform_org = Tenant.objects.create(name="Platform Org")

        cls.admin = User.objects.create_superuser(
            username="scope_admin",
            email="scope_admin@test.com",
            mobile="+919150000001",
            password="testpass123",
        )
        TenantUser.objects.create(
            tenant=cls.platform_org,
            user=cls.admin,
            role=TenantRole.objects.get(tenant=cls.platform_org, slug="owner"),
        )

        # ── contacts (tenant path: "tenant") ───────────────────────────
        cls.contact_a = TenantContact.objects.create(tenant=cls.org_a, phone="+919150001001", first_name=f"Contact-{A}")
        cls.contact_b = TenantContact.objects.create(tenant=cls.org_b, phone="+919150002001", first_name=f"Contact-{B}")

        # ── notifications (tenant path: "tenant") ──────────────────────
        cls.notification_a = Notification.objects.create(
            tenant=cls.org_a,
            notification_type=NotificationType.LOW_BALANCE,
            title=f"Notice-{A}",
        )
        cls.notification_b = Notification.objects.create(
            tenant=cls.org_b,
            notification_type=NotificationType.LOW_BALANCE,
            title=f"Notice-{B}",
        )

        # ── broadcasts (tenant path: "tenant") ─────────────────────────
        cls.broadcast_a = Broadcast.objects.create(tenant=cls.org_a, name=f"Campaign-{A}")
        cls.broadcast_b = Broadcast.objects.create(tenant=cls.org_b, name=f"Campaign-{B}")

        # ── chat flow nodes (tenant path: "flow__tenant" — two hops) ───
        cls.flow_a = ChatFlow.objects.create(tenant=cls.org_a, name=f"Flow-{A}", flow_data={})
        cls.flow_b = ChatFlow.objects.create(tenant=cls.org_b, name=f"Flow-{B}", flow_data={})
        cls.node_a = ChatFlowNode.objects.create(
            flow=cls.flow_a, node_id=f"node-{A}", node_type="text", position_x=0, position_y=0
        )
        cls.node_b = ChatFlowNode.objects.create(
            flow=cls.flow_b, node_id=f"node-{B}", node_type="text", position_x=0, position_y=0
        )

        # ── WABA info (tenant path: "wa_app__tenant", via an override) ─
        cls.wa_app_a = TenantWAApp.objects.create(
            tenant=cls.org_a, app_name=f"app-{A}", app_id=f"app-id-{A}", app_secret="s", wa_number="+919150003001"
        )
        cls.wa_app_b = TenantWAApp.objects.create(
            tenant=cls.org_b, app_name=f"app-{B}", app_id=f"app-id-{B}", app_secret="s", wa_number="+919150004001"
        )
        # A signal already made a blank WABAInfo for each app, so update rather
        # than create.
        cls.waba_a = WABAInfo.objects.get(wa_app=cls.wa_app_a)
        cls.waba_a.verified_name = f"Verified-{A}"
        cls.waba_a.save(update_fields=["verified_name"])
        cls.waba_b = WABAInfo.objects.get(wa_app=cls.wa_app_b)
        cls.waba_b.verified_name = f"Verified-{B}"
        cls.waba_b.save(update_fields=["verified_name"])

        # ── SMS, including a row in the *actor's own* organisation ──────
        # The SMS override filtered by membership, so an impersonated session
        # was served the actor's own traffic under another customer's banner.
        # That row has to exist for the test to be able to catch it.
        cls.sms_a = cls._sms_message(cls.org_a, A, "+919150005001")
        cls.sms_b = cls._sms_message(cls.org_b, B, "+919150006001")
        cls.sms_actor_own = cls._sms_message(cls.platform_org, "own", "+919150007001")

        # ── RCS ────────────────────────────────────────────────────────
        cls.rcs_a = cls._rcs_message(cls.org_a, A, "+919150008001")
        cls.rcs_b = cls._rcs_message(cls.org_b, B, "+919150008002")

    @staticmethod
    def _sms_message(tenant, suffix, number):
        app = SMSApp.objects.create(tenant=tenant, sender_id=f"snd-{suffix}", webhook_secret=f"sms-hook-{suffix}")
        return SMSOutboundMessage.objects.create(
            tenant=tenant, sms_app=app, to_number=number, message_text=f"SMS-{suffix}"
        )

    @staticmethod
    def _rcs_message(tenant, suffix, number):
        app = RCSApp.objects.create(
            tenant=tenant, agent_id=f"agent-{suffix}", webhook_client_token=f"rcs-hook-{suffix}"
        )
        return RCSOutboundMessage.objects.create(
            tenant=tenant, rcs_app=app, to_phone=number, message_content={"text": f"RCS-{suffix}"}
        )

    # ── clients ────────────────────────────────────────────────────────

    def viewing(self, tenant=None):
        """A client holding an impersonation token for ``tenant`` (Org Alpha)."""
        raw, _session = issue_impersonation_token(self.admin, tenant or self.org_a)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client

    def ordinary_admin(self):
        """The same superuser, holding their own ordinary token.

        ``is_superuser`` is stamped the way ``users.serializers`` stamps it at
        login — ``CustomJWTAuthentication`` reads the flag off the claim, so a
        bare ``AccessToken.for_user`` would authenticate as a non-superuser and
        this test would be measuring the wrong read path.
        """
        token = AccessToken.for_user(self.admin)
        token["is_superuser"] = True
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    # ── helpers ────────────────────────────────────────────────────────

    def assert_scoped_list(self, url_name, mine, theirs, marker):
        """List ``url_name`` while impersonating Org Alpha.

        Asserts the other organisation's row is absent — by id, and by the
        unique string it carries, so a field echoing the value elsewhere in the
        response cannot satisfy the assertion.
        """
        response = self.viewing().get(reverse(url_name), {"page_size": 1000})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        returned = ids_in(response)
        self.assertIn(str(mine.pk), returned, f"{url_name}: the impersonated organisation's own row is missing")
        self.assertNotIn(str(theirs.pk), returned, f"{url_name}: another organisation's row was readable")
        self.assertNotContains(response, marker, msg_prefix=f"{url_name}: another organisation's data leaked")
        return response

    # ── list ───────────────────────────────────────────────────────────

    def test_contacts_list_excludes_the_other_organisation(self):
        self.assert_scoped_list("contacts:tenant-contacts-list", self.contact_a, self.contact_b, f"Contact-{B}")

    def test_notifications_list_excludes_the_other_organisation(self):
        self.assert_scoped_list(
            "notifications:notifications-list", self.notification_a, self.notification_b, f"Notice-{B}"
        )

    def test_broadcasts_list_excludes_the_other_organisation(self):
        self.assert_scoped_list("broadcast:tenant-broadcasts-list", self.broadcast_a, self.broadcast_b, f"Campaign-{B}")

    def test_chat_flows_list_excludes_the_other_organisation(self):
        self.assert_scoped_list("chat_flow:chatflow-list", self.flow_a, self.flow_b, f"Flow-{B}")

    def test_a_two_hop_tenant_path_is_scoped_too(self):
        """ChatFlowNode reaches its tenant through ``flow__tenant``."""
        self.assert_scoped_list("chat_flow:chatflownode-list", self.node_a, self.node_b, f"node-{B}")

    def test_an_override_that_builds_its_own_queryset_is_scoped(self):
        """``WABAInfoViewSet.get_queryset`` never calls the base implementation,
        so it had to be scoped in place — and if it were missed, the base class
        could not save it."""
        self.assert_scoped_list("tenants:wabainfo-list", self.waba_a, self.waba_b, f"Verified-{B}")

    def test_the_organisation_list_shows_only_the_one_being_viewed(self):
        """``TenantViewSet`` served the whole customer list to an impersonated
        session — the most directly misleading case of all."""
        response = self.viewing().get(reverse("tenants:tenant-list"), {"page_size": 1000})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(ids_in(response), {str(self.org_a.pk)})
        self.assertNotContains(response, f"Org Beta {B}")

    def test_a_membership_filtered_override_shows_the_viewed_organisation(self):
        """``SMSOutboundMessageViewSet`` filtered by membership, so it did not
        leak every organisation — it served the *actor's own* organisation's
        traffic under the other customer's banner, which is worse: it looks
        plausible. All three organisations have SMS rows here."""
        response = self.viewing().get(reverse("sms:sms-messages-list"), {"page_size": 1000})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(ids_in(response), {str(self.sms_a.pk)})
        self.assertNotContains(response, f"SMS-{B}")
        self.assertNotContains(response, "SMS-own")

    def test_an_override_that_found_no_membership_now_shows_the_organisation(self):
        """``RCSOutboundMessageViewSet`` resolved the tenant from the actor's
        membership, which an impersonated session has none of — so it showed an
        empty list where the organisation has messages. One organisation's rows,
        and they are the right organisation's."""
        response = self.viewing().get(reverse("rcs:rcs-messages-list"), {"page_size": 1000})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(ids_in(response), {str(self.rcs_a.pk)})
        self.assertNotContains(response, f"RCS-{B}")

    def test_the_webhook_branch_that_bypasses_tenant_filtering_is_still_scoped(self):
        """``TenantGupshupAppsViewSet`` returns every app for its webhook
        actions, on purpose — external callers own no membership. But those
        actions are ``detail=True`` and accept GET, so they were a detail read of
        any organisation's app. Unchanged for the webhook callers themselves,
        which carry no impersonation token."""
        client = self.viewing()
        url = "tenants:tenantgupshup-webhook-billing"

        mine = client.get(reverse(url, args=[self.wa_app_a.app_id]))
        theirs = client.get(reverse(url, args=[self.wa_app_b.app_id]))

        self.assertEqual(mine.status_code, status.HTTP_200_OK)
        self.assertEqual(theirs.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotContains(theirs, f"app-id-{B}", status_code=status.HTTP_404_NOT_FOUND)

    # ── retrieve ───────────────────────────────────────────────────────

    def test_retrieving_another_organisations_row_is_a_404(self):
        client = self.viewing()

        mine = client.get(reverse("contacts:tenant-contacts-detail", args=[self.contact_a.pk]))
        theirs = client.get(reverse("contacts:tenant-contacts-detail", args=[self.contact_b.pk]))

        self.assertEqual(mine.status_code, status.HTTP_200_OK)
        self.assertEqual(theirs.status_code, status.HTTP_404_NOT_FOUND)

    def test_retrieving_another_organisation_itself_is_a_404(self):
        client = self.viewing()

        mine = client.get(reverse("tenants:tenant-detail", args=[self.org_a.pk]))
        theirs = client.get(reverse("tenants:tenant-detail", args=[self.org_b.pk]))

        self.assertEqual(mine.status_code, status.HTTP_200_OK)
        self.assertEqual(theirs.status_code, status.HTTP_404_NOT_FOUND)

    # ── custom read actions ────────────────────────────────────────────

    def test_a_read_action_built_on_the_base_queryset_is_scoped(self):
        """``unread-count`` counts through ``super().get_queryset()``, so an
        unscoped base leaked as a number rather than as rows — harder to notice
        and just as wrong."""
        response = self.viewing().get(reverse("notifications:notifications-unread-count"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["unread_count"], 1)

    # ── switching organisations ─────────────────────────────────────────

    def test_the_scope_follows_the_organisation_named_in_the_token(self):
        """Impersonating Org Beta shows Org Beta's rows and not Org Alpha's —
        so the scoping tracks the token rather than being any fixed subset."""
        response = self.viewing(self.org_b).get(reverse("contacts:tenant-contacts-list"), {"page_size": 1000})

        returned = ids_in(response)
        self.assertEqual(returned, {str(self.contact_b.pk)})
        self.assertNotContains(response, f"Contact-{A}")

    # ── what is deliberately unchanged ─────────────────────────────────

    def test_an_ordinary_superuser_token_still_reads_every_organisation(self):
        """#326 narrows impersonated sessions only. Narrowing the ordinary
        superuser read path is a separate decision and is not taken here, so
        this pins the behaviour rather than leaving it to be assumed."""
        response = self.ordinary_admin().get(reverse("contacts:tenant-contacts-list"), {"page_size": 1000})

        returned = ids_in(response)
        self.assertIn(str(self.contact_a.pk), returned)
        self.assertIn(str(self.contact_b.pk), returned)

    def test_writes_are_still_refused_while_impersonating(self):
        """Impersonation stays read-only; scoping reads must not have opened a
        write path by making the session look more like a member."""
        response = self.viewing().post(
            reverse("contacts:tenant-contacts-list"),
            {"phone": "+919150009999", "first_name": "Should Not Exist"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(TenantContact.objects.filter(first_name="Should Not Exist").exists())
