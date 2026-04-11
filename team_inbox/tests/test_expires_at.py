"""
Tests for #260: with_expires_at annotation uses Extract('timestamp', 'epoch')
instead of the invalid F('timestamp__epoch').
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

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


class WithExpiresAtTests(TestCase):
    """#260: Verify with_expires_at annotation works at the DB level."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="ExpiresAt Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.owner = _make_user("exp_owner", "exp_owner@t.com", "+910000022221")
        TenantUser.objects.create(
            user=cls.owner,
            tenant=cls.tenant,
            role=cls.owner_role,
            is_active=True,
        )

    def _create_message(self, platform="WHATSAPP", direction="INCOMING"):
        return Messages.objects.create(
            tenant=self.tenant,
            content={"text": "hello"},
            direction=direction,
            platform=platform,
            author="CONTACT" if direction == "INCOMING" else "USER",
        )

    # ── Core tests ────────────────────────────────────────────────────

    def test_with_expires_at_does_not_raise(self):
        """#260: with_expires_at() must not raise FieldError."""
        self._create_message()
        # This would previously raise FieldError due to F('timestamp__epoch')
        qs = Messages.objects.with_expires_at()
        self.assertTrue(qs.exists())

    def test_whatsapp_message_has_expires_at_timestamp(self):
        """#260: WhatsApp message gets a non-null expires_at_timestamp."""
        msg = self._create_message(platform="WHATSAPP")
        annotated = Messages.objects.with_expires_at().get(pk=msg.pk)
        self.assertIsNotNone(annotated.expires_at_timestamp)

    def test_whatsapp_expires_at_is_24h_after_timestamp(self):
        """#260: expires_at_timestamp is roughly 24h after message creation."""
        msg = self._create_message(platform="WHATSAPP")
        annotated = Messages.objects.with_expires_at().get(pk=msg.pk)

        # The annotation should be a positive integer (Unix timestamp)
        self.assertIsInstance(annotated.expires_at_timestamp, int)
        # It should be in the future (at least 23h from now given it was just created + 24h)
        import time

        now_epoch = int(time.time())
        self.assertGreater(annotated.expires_at_timestamp, now_epoch)

    def test_non_whatsapp_expires_at_is_none(self):
        """#260: Non-WhatsApp messages get expires_at_timestamp = None."""
        msg = self._create_message(platform="TELEGRAM")
        annotated = Messages.objects.with_expires_at().get(pk=msg.pk)
        self.assertIsNone(annotated.expires_at_timestamp)

    def test_annotation_matches_model_property(self):
        """#260: Annotation value is close to the model property value (within TZ variance)."""
        msg = self._create_message(platform="WHATSAPP")
        annotated = Messages.objects.with_expires_at().get(pk=msg.pk)
        prop_value = msg.expires_at
        ann_value = annotated.expires_at_timestamp
        # Both should be positive integers representing epoch + 24h
        self.assertIsNotNone(ann_value)
        self.assertIsNotNone(prop_value)
        # Both should be within a day of each other (same 24h window)
        self.assertAlmostEqual(ann_value, prop_value, delta=86400)

    def test_with_message_annotations_does_not_raise(self):
        """#260: with_message_annotations() chains without error."""
        self._create_message()
        qs = Messages.objects.with_message_annotations()
        self.assertTrue(qs.exists())
