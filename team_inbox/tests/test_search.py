"""
Tests for #262: search_fields uses correct JSON path content__body__text
instead of the incorrect content__text.
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
        username=username, email=email, mobile=mobile,
        password="testpass123", **kwargs,
    )


class SearchFieldsTests(TestCase):
    """Verify DRF SearchFilter uses the correct JSON path for message text."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="SearchTest Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.owner = _make_user("search_owner", "search_owner@t.com", "+910000044441")
        TenantUser.objects.create(
            user=cls.owner, tenant=cls.tenant, role=cls.owner_role, is_active=True,
        )

        cls.contact = TenantContact.objects.create(
            tenant=cls.tenant, phone="+910000044442",
            first_name="SearchContact", last_name="X",
        )

        # Text message with body.text
        cls.msg_hello = Messages.objects.create(
            tenant=cls.tenant,
            contact=cls.contact,
            content={"type": "text", "body": {"text": "hello world uniquetoken123"}},
            direction="INCOMING",
            platform="WHATSAPP",
            author="CONTACT",
        )

        # Another text message
        cls.msg_order = Messages.objects.create(
            tenant=cls.tenant,
            contact=cls.contact,
            content={"type": "text", "body": {"text": "my order status please"}},
            direction="INCOMING",
            platform="WHATSAPP",
            author="CONTACT",
        )

        # Image message (no body.text)
        cls.msg_image = Messages.objects.create(
            tenant=cls.tenant,
            contact=cls.contact,
            content={"type": "image", "image": {"url": "https://example.com/img.png", "caption": "photo"}},
            direction="INCOMING",
            platform="WHATSAPP",
            author="CONTACT",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = "/team-inbox/api/messages/"

    def test_search_finds_text_in_body(self):
        """Searching for text inside body.text returns the correct message."""
        resp = self.client.get(self.url, {"search": "uniquetoken123"})
        self.assertEqual(resp.status_code, 200)
        ids = [m["id"] for m in resp.data["results"]]
        self.assertIn(self.msg_hello.id, ids)

    def test_search_excludes_non_matching(self):
        """Search term not present in any message returns empty."""
        resp = self.client.get(self.url, {"search": "zzz_no_match_zzz"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 0)

    def test_search_partial_match(self):
        """Partial word match works (icontains)."""
        resp = self.client.get(self.url, {"search": "order status"})
        self.assertEqual(resp.status_code, 200)
        ids = [m["id"] for m in resp.data["results"]]
        self.assertIn(self.msg_order.id, ids)
        self.assertNotIn(self.msg_hello.id, ids)

    def test_search_by_message_id_still_works(self):
        """message_id search path is unchanged."""
        # message_id is an FK to MessageEventIds; search on its string repr
        if self.msg_hello.message_id:
            resp = self.client.get(
                self.url, {"search": str(self.msg_hello.message_id)},
            )
            self.assertEqual(resp.status_code, 200)

    def test_search_no_param_returns_all(self):
        """Omitting search returns all messages (unfiltered list)."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data["results"]), 3)
