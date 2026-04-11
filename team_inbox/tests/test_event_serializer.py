"""
Tests for #267: EventSerializer provides a consistent, single-source
serialization for Event model instances.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from contacts.models import TenantContact
from team_inbox.models import Event, EventTypeChoices
from team_inbox.serializers import EventSerializer
from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


class EventSerializerTests(TestCase):
    """Verify EventSerializer output contains all expected fields."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="EvtSer Tenant")
        cls.owner_role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.user_a = User.objects.create_user(
            username="evt_a",
            email="evt_a@t.com",
            mobile="+910000099991",
            password="testpass123",
            first_name="Alice",
            last_name="A",
        )
        cls.user_b = User.objects.create_user(
            username="evt_b",
            email="evt_b@t.com",
            mobile="+910000099992",
            password="testpass123",
            first_name="Bob",
            last_name="B",
        )
        TenantUser.objects.create(
            user=cls.user_a,
            tenant=cls.tenant,
            role=cls.owner_role,
            is_active=True,
        )
        cls.contact = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+910000099993",
            first_name="Charlie",
            last_name="C",
        )

    def _create_event(self, **overrides):
        defaults = dict(
            tenant=self.tenant,
            contact=self.contact,
            event_type=EventTypeChoices.TICKET_ASSIGNED,
            note="Follow up with customer",
            created_by_type="USER",
            created_by_id=self.user_a.id,
            created_by_user=self.user_a,
            assigned_by_type="USER",
            assigned_by_id=self.user_a.id,
            assigned_by_user=self.user_a,
            assigned_to_type="USER",
            assigned_to_id=self.user_b.id,
            assigned_to_user=self.user_b,
            icon="assign",
            color_background="#E3F2FD",
            color_text="#1565C0",
            event_data={"instructions": "Priority customer"},
        )
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    # ── Field presence ───────────────────────────────────────────

    def test_all_fields_present(self):
        """Serializer output contains every expected field."""
        evt = self._create_event()
        data = EventSerializer(evt).data
        expected_keys = {
            "id",
            "event_type",
            "event_type_display",
            "note",
            "created_by_name",
            "assigned_by_name",
            "assigned_to_name",
            "icon",
            "color_background",
            "color_text",
            "event_data",
            "created_at",
            "timestamp",
        }
        self.assertEqual(set(data.keys()), expected_keys)

    # ── Field values ─────────────────────────────────────────────

    def test_event_type_and_display(self):
        """event_type is the DB value, event_type_display is human-readable."""
        evt = self._create_event()
        data = EventSerializer(evt).data
        self.assertEqual(data["event_type"], "TICKET_ASSIGNED")
        self.assertEqual(data["event_type_display"], "Ticket Assigned")

    def test_actor_names(self):
        """Actor name properties are serialized correctly."""
        evt = self._create_event()
        data = EventSerializer(evt).data
        self.assertEqual(data["created_by_name"], "Alice A")
        self.assertEqual(data["assigned_by_name"], "Alice A")
        self.assertEqual(data["assigned_to_name"], "Bob B")

    def test_note_and_event_data(self):
        """Note and event_data are included."""
        evt = self._create_event()
        data = EventSerializer(evt).data
        self.assertEqual(data["note"], "Follow up with customer")
        self.assertEqual(data["event_data"]["instructions"], "Priority customer")

    def test_display_fields(self):
        """Icon and color fields are included."""
        evt = self._create_event()
        data = EventSerializer(evt).data
        self.assertEqual(data["icon"], "assign")
        self.assertEqual(data["color_background"], "#E3F2FD")
        self.assertEqual(data["color_text"], "#1565C0")

    def test_timestamps_present(self):
        """Both created_at and timestamp are serialized."""
        evt = self._create_event()
        data = EventSerializer(evt).data
        self.assertIsNotNone(data["created_at"])
        self.assertIsNotNone(data["timestamp"])

    # ── Null / empty cases ───────────────────────────────────────

    def test_null_actors(self):
        """Events with no actor FKs serialize gracefully."""
        evt = self._create_event(
            assigned_by_type=None,
            assigned_by_id=None,
            assigned_by_user=None,
            assigned_to_type=None,
            assigned_to_id=None,
            assigned_to_user=None,
        )
        data = EventSerializer(evt).data
        self.assertIsNone(data["assigned_by_name"])
        self.assertIsNone(data["assigned_to_name"])

    def test_empty_event_data(self):
        """Empty event_data serializes as empty dict."""
        evt = self._create_event(event_data={})
        data = EventSerializer(evt).data
        self.assertEqual(data["event_data"], {})

    # ── Read-only ────────────────────────────────────────────────

    def test_serializer_is_read_only(self):
        """All fields are read_only — serializer doesn't accept writes."""
        payload = {
            "event_type": "TICKET_CLOSED",
            "note": "should be ignored",
        }
        serializer = EventSerializer(data=payload)
        # valid because all fields are read-only (no required writable fields)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data, {})
