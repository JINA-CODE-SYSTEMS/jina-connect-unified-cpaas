"""Read receipts for inbound WhatsApp messages (#274.3).

Marking a message read moved our own rows and broadcast to the team; the
customer never saw blue ticks because nothing called META. These tests pin
the missing call, the single-call-per-batch behaviour, and the deliberate
Gupshup no-op — a provider gap that has to be visible, not silent.
"""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from team_inbox.models import MessageEventIds, Messages
from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()

MARK_READ_PATCH = "wa.utility.apis.meta.session_message_api.SessionMessageAPI.mark_read"


class ReadReceiptTestBase(TestCase):
    """Tenant with one META WA app, an agent, and a contact."""

    def setUp(self):
        from contacts.models import TenantContact
        from wa.models import WAApp

        self.tenant = Tenant.objects.create(name=f"Receipts {uuid.uuid4().hex[:6]}")
        self.owner = User.objects.create_user(
            username=f"receipt_owner_{uuid.uuid4().hex[:6]}",
            email=f"{uuid.uuid4().hex[:6]}@t.com",
            mobile=f"+91{uuid.uuid4().int % 10**10:010d}",
            password="testpass123",
        )
        TenantUser.objects.create(
            user=self.owner,
            tenant=self.tenant,
            role=TenantRole.objects.get(tenant=self.tenant, slug="owner"),
            is_active=True,
        )
        self.wa_app = WAApp.objects.create(
            tenant=self.tenant,
            app_name="Receipts App",
            app_id=f"app_{uuid.uuid4().hex[:8]}",
            app_secret="secret",
            wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
            waba_id=f"waba_{uuid.uuid4().hex[:8]}",
            phone_number_id="PHONE_NUMBER_ID",
            bsp="META",
            bsp_credentials={"access_token": "EAAtest"},
            is_verified=True,
            is_active=True,
        )
        self.contact = TenantContact.objects.create(
            tenant=self.tenant,
            first_name="Cust",
            phone=f"+1{uuid.uuid4().int % 10**10:010d}",
        )

    def _inbound(self, wa_message_id="wamid.ONE", **extra):
        content = {"type": "text", "body": {"text": "hi"}}
        if wa_message_id:
            content["_meta"] = {"wa_message_id": wa_message_id}
        return Messages.objects.create(
            tenant=self.tenant,
            message_id=MessageEventIds.objects.create(),
            content=content,
            direction="INCOMING",
            platform="WHATSAPP",
            author="CONTACT",
            contact=self.contact,
            **extra,
        )

    def _client(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        return client


class SendReadReceiptTests(ReadReceiptTestBase):
    """The helper itself."""

    def test_acknowledges_the_inbound_message(self):
        from team_inbox.utils.read_receipts import send_read_receipt

        message = self._inbound("wamid.ABC")

        with patch(MARK_READ_PATCH, return_value={"success": True}) as mark_read:
            result = send_read_receipt([message])

        mark_read.assert_called_once_with("wamid.ABC")
        self.assertTrue(result["sent"])

    def test_only_the_newest_message_is_acknowledged(self):
        """META marks every earlier message of the conversation read too."""
        from team_inbox.utils.read_receipts import send_read_receipt

        older = self._inbound("wamid.OLD")
        newer = self._inbound("wamid.NEW")
        self.assertLess(older.timestamp, newer.timestamp)

        with patch(MARK_READ_PATCH, return_value={"success": True}) as mark_read:
            send_read_receipt([older, newer])

        mark_read.assert_called_once_with("wamid.NEW")

    def test_gupshup_is_skipped_explicitly(self):
        """Gupshup exposes no read-receipt endpoint — say so, don't pretend."""
        from team_inbox.utils.read_receipts import send_read_receipt

        self.wa_app.bsp = "GUPSHUP"
        self.wa_app.save(update_fields=["bsp"])
        message = self._inbound()

        with patch(MARK_READ_PATCH) as mark_read:
            result = send_read_receipt([message])

        mark_read.assert_not_called()
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "provider_unsupported:GUPSHUP")

    def test_message_without_provider_id_is_skipped(self):
        from team_inbox.utils.read_receipts import send_read_receipt

        message = self._inbound(wa_message_id=None)

        with patch(MARK_READ_PATCH) as mark_read:
            result = send_read_receipt([message])

        mark_read.assert_not_called()
        self.assertEqual(result["reason"], "no_provider_message_id")

    def test_outgoing_messages_are_never_acknowledged(self):
        from team_inbox.utils.read_receipts import send_read_receipt

        outgoing = Messages.objects.create(
            tenant=self.tenant,
            message_id=MessageEventIds.objects.create(),
            content={"type": "text", "body": {"text": "hi"}, "_meta": {"wa_message_id": "wamid.OUT"}},
            direction="OUTGOING",
            platform="WHATSAPP",
            author="USER",
            contact=self.contact,
        )

        with patch(MARK_READ_PATCH) as mark_read:
            result = send_read_receipt([outgoing])

        mark_read.assert_not_called()
        self.assertEqual(result["reason"], "no_whatsapp_inbound")

    def test_provider_failure_does_not_propagate(self):
        """A missing blue tick must never fail the mark-as-read behind it."""
        from team_inbox.utils.read_receipts import send_read_receipt

        message = self._inbound()

        with patch(MARK_READ_PATCH, side_effect=Exception("graph 500")):
            result = send_read_receipt([message])

        self.assertFalse(result["sent"])
        self.assertTrue(result["reason"].startswith("error:"))


class ConsumerReceiptTests(ReadReceiptTestBase):
    """The WebSocket path — what the inbox UI actually marks read with.

    The two handlers are wrapped in ``database_sync_to_async``, whose
    wrapper closes the connection it borrowed and pulls the rug from under
    a sync ``TestCase``. ``_body`` reaches the undecorated function — the
    part these tests are about.
    """

    def _consumer(self):
        from team_inbox.consumers import TeamInboxConsumer

        consumer = TeamInboxConsumer()
        consumer.tenant_id = str(self.tenant.id)
        consumer.user = self.owner
        return consumer

    @staticmethod
    def _body(method_name):
        from team_inbox.consumers import TeamInboxConsumer

        # Straight out of __dict__: attribute access would hand back the
        # async wrapper rather than the function it wraps.
        return TeamInboxConsumer.__dict__[method_name].func

    def test_mark_messages_as_read_sends_a_receipt(self):
        message = self._inbound("wamid.WS")

        with patch(MARK_READ_PATCH, return_value={"success": True}) as mark_read:
            marked = self._body("mark_messages_as_read")(self._consumer(), [message.pk])

        self.assertEqual(marked, [message.pk])
        mark_read.assert_called_once_with("wamid.WS")

    def test_mark_contact_messages_as_read_sends_a_receipt(self):
        self._inbound("wamid.WS1")
        newest = self._inbound("wamid.WS2")

        with patch(MARK_READ_PATCH, return_value={"success": True}) as mark_read:
            marked = self._body("mark_contact_messages_as_read")(self._consumer(), self.contact.pk)

        self.assertEqual(len(marked), 2)
        mark_read.assert_called_once_with(newest.content["_meta"]["wa_message_id"])


class MarkAsReadEndpointReceiptTests(ReadReceiptTestBase):
    """The REST endpoints that agents actually hit."""

    def test_mark_as_read_sends_a_receipt(self):
        message = self._inbound("wamid.REST")

        with patch(MARK_READ_PATCH, return_value={"success": True}) as mark_read:
            response = self._client().post(f"/team-inbox/api/messages/{message.pk}/mark_as_read/")

        self.assertEqual(response.status_code, 200)
        mark_read.assert_called_once_with("wamid.REST")

    def test_mark_multiple_sends_one_receipt_for_the_batch(self):
        first = self._inbound("wamid.B1")
        second = self._inbound("wamid.B2")

        with patch(MARK_READ_PATCH, return_value={"success": True}) as mark_read:
            response = self._client().post(
                "/team-inbox/api/messages/mark_multiple_as_read/",
                {"message_ids": [first.pk, second.pk]},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        mark_read.assert_called_once_with("wamid.B2")

    def test_already_read_message_sends_nothing(self):
        message = self._inbound("wamid.DUP", is_read=True)

        with patch(MARK_READ_PATCH) as mark_read:
            self._client().post(f"/team-inbox/api/messages/{message.pk}/mark_as_read/")

        mark_read.assert_not_called()


class OutgoingErrorFieldTests(ReadReceiptTestBase):
    """#274.2 — the inbox has to say *why* a send failed."""

    def _failed_outgoing(self, error_message):
        from wa.models import MessageDirection, MessageStatus, WAMessage

        with patch("wa.signals._dispatch"):
            wa_message = WAMessage.objects.create(
                wa_app=self.wa_app,
                contact=self.contact,
                direction=MessageDirection.OUTBOUND,
                status=MessageStatus.FAILED,
                message_type="TEXT",
                text="too late",
                error_code="SERVICE_WINDOW_CLOSED",
                error_message=error_message,
            )
        return Messages.objects.create(
            tenant=self.tenant,
            message_id=MessageEventIds.objects.create(),
            content={"type": "text", "body": {"text": "too late"}},
            direction="OUTGOING",
            platform="WHATSAPP",
            author="USER",
            contact=self.contact,
            outgoing_message=wa_message,
        )

    def test_serializer_exposes_the_failure_reason(self):
        from team_inbox.serializers import MessagesSerializer

        message = self._failed_outgoing("The 24-hour customer service window closed at …")

        data = MessagesSerializer(message).data

        self.assertEqual(data["outgoing_status"], "FAILED")
        self.assertIn("24-hour", data["outgoing_error"])

    def test_incoming_message_has_no_outgoing_error(self):
        from team_inbox.serializers import MessagesSerializer

        data = MessagesSerializer(self._inbound()).data

        self.assertIsNone(data["outgoing_error"])
