"""
Tests for #264: bulk_delete uses POST instead of DELETE to avoid
proxy body-stripping issues.
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


class BulkDeleteTests(TestCase):
    """Verify bulk_delete accepts POST and works correctly."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="BulkDel Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.owner = _make_user("bd_owner", "bd_owner@t.com", "+910000066661")
        TenantUser.objects.create(
            user=cls.owner,
            tenant=cls.tenant,
            role=cls.owner_role,
            is_active=True,
        )
        cls.contact = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+910000066662",
            first_name="DelContact",
            last_name="X",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = "/team-inbox/api/messages/bulk-delete/"

    def _create_messages(self, count=3):
        from team_inbox.models import MessageEventIds

        msgs = []
        for i in range(count):
            event = MessageEventIds.objects.create()
            msgs.append(
                Messages.objects.create(
                    tenant=self.tenant,
                    contact=self.contact,
                    message_id=event,
                    content={"type": "text", "body": {"text": f"del_{i}"}},
                    direction="INCOMING",
                    platform="WHATSAPP",
                    author="CONTACT",
                )
            )
        return msgs

    # ── Method tests ─────────────────────────────────────────────

    def test_post_accepted(self):
        """POST method is accepted (not DELETE)."""
        msgs = self._create_messages(2)
        resp = self.client.post(
            self.url,
            {"message_ids": [m.message_id_id for m in msgs]},
            format="json",
        )
        self.assertIn(resp.status_code, [200, 204])

    def test_delete_method_not_allowed(self):
        """DELETE method is no longer accepted."""
        resp = self.client.delete(
            self.url,
            {"message_ids": [1]},
            format="json",
        )
        self.assertEqual(resp.status_code, 405)

    # ── Functional tests ─────────────────────────────────────────

    def test_deletes_messages_by_ids(self):
        """Supplying message_ids deletes matching messages."""
        msgs = self._create_messages(3)
        ids_to_delete = [m.message_id_id for m in msgs[:2]]
        resp = self.client.post(
            self.url,
            {"message_ids": ids_to_delete},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["deleted_count"], 2)
        # Third message still exists
        self.assertTrue(Messages.objects.filter(id=msgs[2].id).exists())

    def test_empty_ids_returns_400(self):
        """Empty message_ids returns 400."""
        resp = self.client.post(self.url, {"message_ids": []}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_missing_ids_returns_400(self):
        """No message_ids key returns 400."""
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_nonexistent_ids_delete_zero(self):
        """IDs that don't exist result in deleted_count=0."""
        resp = self.client.post(
            self.url,
            {"message_ids": [999998, 999999]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["deleted_count"], 0)

    def test_unauthenticated_returns_401(self):
        """Unauthenticated request is rejected."""
        client = APIClient()
        resp = client.post(self.url, {"message_ids": [1]}, format="json")
        self.assertEqual(resp.status_code, 401)
