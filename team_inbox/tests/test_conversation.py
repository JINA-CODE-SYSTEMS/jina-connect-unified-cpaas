"""
Tests for #261: conversation action filters by FK field contact_id,
not the non-existent JSON key content__contact_id.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from contacts.models import TenantContact
from team_inbox.models import Messages
from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


def _make_user(username, email, mobile, **kwargs):
    return User.objects.create_user(
        username=username,
        email=email,
        mobile=mobile,
        password="testpass123",
        **kwargs,
    )


class ConversationActionTests(TestCase):
    """Verify the conversation action correctly filters by contact FK."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="ConvTest Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.owner = _make_user("conv_owner", "conv_owner@t.com", "+910000022221")
        TenantUser.objects.create(
            user=cls.owner,
            tenant=cls.tenant,
            role=cls.owner_role,
            is_active=True,
        )

        # Two contacts in the same tenant
        cls.contact_a = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+910000033331",
            first_name="Alice",
            last_name="A",
        )
        cls.contact_b = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+910000033332",
            first_name="Bob",
            last_name="B",
        )

        # Messages for contact_a
        for i in range(3):
            Messages.objects.create(
                tenant=cls.tenant,
                contact=cls.contact_a,
                content={"text": f"msg_a_{i}"},
                direction="INCOMING",
                platform="WHATSAPP",
                author="CONTACT",
            )

        # Messages for contact_b
        for i in range(2):
            Messages.objects.create(
                tenant=cls.tenant,
                contact=cls.contact_b,
                content={"text": f"msg_b_{i}"},
                direction="OUTGOING",
                platform="WHATSAPP",
                author="USER",
            )

        # Message on a different platform (should not appear)
        Messages.objects.create(
            tenant=cls.tenant,
            contact=cls.contact_a,
            content={"text": "sms_msg"},
            direction="INCOMING",
            platform="SMS",
            author="CONTACT",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = "/team-inbox/api/messages/conversation/"

    # ---- Basic filtering ----

    def test_conversation_filters_by_contact_id(self):
        """Passing contact_id returns only that contact's messages."""
        resp = self.client.get(
            self.url,
            {"platform": "WHATSAPP", "contact_id": self.contact_a.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 3)
        texts = {m["content"]["text"] for m in resp.data}
        self.assertTrue(all(t.startswith("msg_a_") for t in texts))

    def test_conversation_filters_contact_b(self):
        """Same check for the second contact."""
        resp = self.client.get(
            self.url,
            {"platform": "WHATSAPP", "contact_id": self.contact_b.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 2)
        texts = {m["content"]["text"] for m in resp.data}
        self.assertTrue(all(t.startswith("msg_b_") for t in texts))

    def test_conversation_without_contact_id_returns_all(self):
        """Omitting contact_id returns all messages for the platform."""
        resp = self.client.get(self.url, {"platform": "WHATSAPP"})
        self.assertEqual(resp.status_code, 200)
        # 3 from contact_a + 2 from contact_b
        self.assertEqual(len(resp.data), 5)

    def test_conversation_nonexistent_contact_returns_empty(self):
        """A contact_id that has no messages returns an empty list."""
        resp = self.client.get(
            self.url,
            {"platform": "WHATSAPP", "contact_id": 999999},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 0)

    # ---- Platform filtering ----

    def test_conversation_platform_filter(self):
        """Platform filter isolates SMS messages from WHATSAPP."""
        resp = self.client.get(
            self.url,
            {"platform": "SMS", "contact_id": self.contact_a.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["content"]["text"], "sms_msg")

    def test_conversation_requires_platform(self):
        """Missing platform returns 400."""
        resp = self.client.get(
            self.url,
            {"contact_id": self.contact_a.id},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.data)

    # ---- Ordering ----

    def test_conversation_ordered_by_timestamp(self):
        """Messages are returned in ascending timestamp order."""
        resp = self.client.get(
            self.url,
            {"platform": "WHATSAPP", "contact_id": self.contact_a.id},
        )
        timestamps = [m["timestamp"] for m in resp.data]
        self.assertEqual(timestamps, sorted(timestamps))
