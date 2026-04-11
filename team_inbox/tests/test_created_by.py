"""
Tests for #263: Messages.created_by property handles CONTACT messages
without crashing on AttributeError.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from contacts.models import TenantContact
from team_inbox.models import Messages
from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


class CreatedByPropertyTests(TestCase):
    """Verify created_by returns the right string for every author type."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="CreatedByTest Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.user = User.objects.create_user(
            username="cb_user",
            email="cb_user@t.com",
            mobile="+910000055551",
            password="testpass123",
            first_name="Jane",
            last_name="Doe",
        )
        cls.tenant_user = TenantUser.objects.create(
            user=cls.user,
            tenant=cls.tenant,
            role=cls.owner_role,
            is_active=True,
        )

        cls.contact_full = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+910000055552",
            first_name="Alice",
            last_name="Smith",
        )
        cls.contact_first_only = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+910000055553",
            first_name="Bob",
            last_name="",
        )
        cls.contact_no_name = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+910000055554",
            first_name="",
            last_name="",
        )

    def _msg(self, author, contact=None, tenant_user=None, **kw):
        return Messages.objects.create(
            tenant=self.tenant,
            content={"type": "text", "body": {"text": "hi"}},
            direction="INCOMING" if author == "CONTACT" else "OUTGOING",
            platform="WHATSAPP",
            author=author,
            contact=contact,
            tenant_user=tenant_user,  # This is a User FK, not TenantUser
            **kw,
        )

    # ── CONTACT author ───────────────────────────────────────────

    def test_contact_full_name(self):
        """CONTACT with first_name + last_name returns full name."""
        msg = self._msg("CONTACT", contact=self.contact_full)
        self.assertEqual(msg.created_by, "Alice Smith")

    def test_contact_first_name_only(self):
        """CONTACT with only first_name returns first name."""
        msg = self._msg("CONTACT", contact=self.contact_first_only)
        self.assertEqual(msg.created_by, "Bob")

    def test_contact_no_name_falls_back_to_phone(self):
        """CONTACT with empty names falls back to phone number."""
        msg = self._msg("CONTACT", contact=self.contact_no_name)
        self.assertEqual(msg.created_by, str(self.contact_no_name.phone))

    def test_contact_null_returns_unknown(self):
        """CONTACT with no contact FK returns 'Unknown Contact'."""
        msg = self._msg("CONTACT", contact=None)
        self.assertEqual(msg.created_by, "Unknown Contact")

    # ── USER author ──────────────────────────────────────────────

    def test_user_returns_full_name(self):
        """USER author returns tenant_user's full name."""
        msg = self._msg("USER", tenant_user=self.user)
        self.assertEqual(msg.created_by, "Jane Doe")

    def test_user_no_tenant_user_returns_unknown(self):
        """USER with null tenant_user returns 'Unknown User'."""
        msg = self._msg("USER", tenant_user=None)
        self.assertEqual(msg.created_by, "Unknown User")

    # ── BOT author ───────────────────────────────────────────────

    def test_bot_returns_none(self):
        """BOT author returns None."""
        msg = self._msg("BOT")
        self.assertIsNone(msg.created_by)
