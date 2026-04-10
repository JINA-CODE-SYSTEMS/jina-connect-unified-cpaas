"""
Tests for #256: mark_as_read and mark_multiple_as_read REST endpoints.
Ensures these actions actually update is_read, read_at, read_by in the DB.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from team_inbox.models import Messages
from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


def _make_user(username, email, mobile, **kwargs):
    return User.objects.create_user(
        username=username, email=email, mobile=mobile,
        password="testpass123", **kwargs,
    )


class MarkAsReadTestBase(TestCase):
    """Shared setup for mark-as-read tests."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="ReadTest Tenant")
        # Roles are auto-seeded; grab owner & agent
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.agent_role = TenantRole.objects.get(tenant=cls.tenant, slug="agent")

        cls.owner = _make_user("read_owner", "read_owner@t.com", "+910000011111")
        cls.agent = _make_user("read_agent", "read_agent@t.com", "+910000011112")

        TenantUser.objects.create(
            user=cls.owner, tenant=cls.tenant, role=cls.owner_role, is_active=True,
        )
        TenantUser.objects.create(
            user=cls.agent, tenant=cls.tenant, role=cls.agent_role, is_active=True,
        )

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _create_message(self, direction="INCOMING", is_read=False, **kwargs):
        return Messages.objects.create(
            tenant=self.tenant,
            content={"text": "hello"},
            direction=direction,
            platform="WHATSAPP",
            author="CONTACT" if direction == "INCOMING" else "USER",
            is_read=is_read,
            **kwargs,
        )


class SingleMarkAsReadTests(MarkAsReadTestBase):
    """#256: POST /team-inbox/api/messages/{pk}/mark_as_read/"""

    def test_marks_incoming_message_as_read(self):
        """Incoming unread message → is_read=True, read_at set, read_by=user."""
        msg = self._create_message()
        self.assertFalse(msg.is_read)

        resp = self._client(self.owner).post(
            f"/team-inbox/api/messages/{msg.pk}/mark_as_read/"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "Message marked as read")

        msg.refresh_from_db()
        self.assertTrue(msg.is_read)
        self.assertIsNotNone(msg.read_at)
        self.assertEqual(msg.read_by_id, self.owner.pk)

    def test_already_read_returns_already_status(self):
        """Already-read message returns 'already read' without re-updating."""
        msg = self._create_message(is_read=True)
        resp = self._client(self.owner).post(
            f"/team-inbox/api/messages/{msg.pk}/mark_as_read/"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "Message already read")

    def test_outgoing_message_rejected(self):
        """Outgoing messages cannot be marked as read → 400."""
        msg = self._create_message(direction="OUTGOING")
        resp = self._client(self.owner).post(
            f"/team-inbox/api/messages/{msg.pk}/mark_as_read/"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("incoming", resp.data["status"].lower())

    def test_nonexistent_message_returns_404(self):
        resp = self._client(self.owner).post(
            "/team-inbox/api/messages/999999/mark_as_read/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_returns_401(self):
        msg = self._create_message()
        resp = APIClient().post(
            f"/team-inbox/api/messages/{msg.pk}/mark_as_read/"
        )
        self.assertEqual(resp.status_code, 401)


class BulkMarkAsReadTests(MarkAsReadTestBase):
    """#256: POST /team-inbox/api/messages/mark_multiple_as_read/"""

    URL = "/team-inbox/api/messages/mark_multiple_as_read/"

    def test_marks_multiple_by_ids(self):
        """Batch mark by PKs updates all unread incoming messages."""
        m1 = self._create_message()
        m2 = self._create_message()
        m3 = self._create_message(direction="OUTGOING")  # should be skipped

        resp = self._client(self.owner).post(
            self.URL,
            {"message_ids": [m1.pk, m2.pk, m3.pk]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["marked_ids"]), 2)

        m1.refresh_from_db()
        m2.refresh_from_db()
        m3.refresh_from_db()
        self.assertTrue(m1.is_read)
        self.assertTrue(m2.is_read)
        self.assertFalse(m3.is_read)  # outgoing — untouched

    def test_marks_by_contact_id(self):
        """When contact_id supplied without message_ids, marks all for that contact."""
        from contacts.models import TenantContact

        contact = TenantContact.objects.create(
            tenant=self.tenant,
            first_name="Mark",
            last_name="Read",
        )
        m1 = self._create_message(contact=contact)
        m2 = self._create_message(contact=contact)
        m_other = self._create_message()  # no contact — should be skipped

        resp = self._client(self.owner).post(
            self.URL,
            {"contact_id": contact.pk},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["marked_ids"]), 2)

        m1.refresh_from_db()
        m2.refresh_from_db()
        m_other.refresh_from_db()
        self.assertTrue(m1.is_read)
        self.assertTrue(m2.is_read)
        self.assertFalse(m_other.is_read)

    def test_skips_already_read(self):
        """Already read messages are not counted."""
        m1 = self._create_message(is_read=True)
        m2 = self._create_message()

        resp = self._client(self.owner).post(
            self.URL,
            {"message_ids": [m1.pk, m2.pk]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["marked_ids"]), 1)
        self.assertIn(m2.pk, resp.data["marked_ids"])

    def test_empty_payload_returns_400(self):
        resp = self._client(self.owner).post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_no_matching_ids_returns_zero(self):
        """IDs that don't exist or are outgoing → 0 marked."""
        resp = self._client(self.owner).post(
            self.URL,
            {"message_ids": [999998, 999999]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["marked_ids"]), 0)

    def test_unauthenticated_returns_401(self):
        resp = APIClient().post(self.URL, {"message_ids": [1]}, format="json")
        self.assertEqual(resp.status_code, 401)
