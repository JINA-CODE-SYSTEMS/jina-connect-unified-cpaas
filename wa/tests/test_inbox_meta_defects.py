"""Team-inbox defects on META Direct (#274).

Covers the send-side and ingest-side halves of the ticket:

  * **Outbound media by ID rendered blank.** META Direct's session upload
    returns a media *ID*, the Cloud API payload must carry ``id`` (it
    rejects ``id`` and ``link`` together), and the timeline builder reads
    ``link`` — so an agent's own image arrived in their own thread as an
    empty bubble. The serializer now back-fills ``media_url`` from the
    media library.
  * **No 24h service-window pre-flight.** Free-form sends after the window
    shut were POSTed anyway and came back as 131047.
  * **Media download failure returned the bare media ID as a URL.**
  * **``supports_typing_indicator`` was declared by both adapters and
    implemented by neither.**
  * **A documented WebSocket client→server type the consumer never
    handled.**

HOW TO RUN:
    DJANGO_SETTINGS_MODULE=jina_connect.settings python -m pytest wa/tests/test_inbox_meta_defects.py -v
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate


def make_tenant_wa_app_contact(bsp="META"):
    """Tenant + META-configured WAApp + contact, all uniquely named."""
    from contacts.models import TenantContact
    from tenants.models import Tenant
    from wa.models import WAApp

    tenant = Tenant.objects.create(name=f"Inbox274 {uuid.uuid4().hex[:8]}", is_active=True)
    wa_app = WAApp.objects.create(
        tenant=tenant,
        app_name=f"App {uuid.uuid4().hex[:6]}",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret=f"secret_{uuid.uuid4().hex[:8]}",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=f"waba_{uuid.uuid4().hex[:8]}",
        phone_number_id=f"phone_{uuid.uuid4().hex[:8]}",
        bsp=bsp,
        bsp_credentials={"access_token": "EAAtest"},
        is_verified=True,
        is_active=True,
    )
    contact = TenantContact.objects.create(
        tenant=tenant,
        first_name="Inbox",
        phone=f"+1{uuid.uuid4().int % 10**10:010d}",
    )
    return tenant, wa_app, contact


# =============================================================================
# 1. Outbound media sent by media_id
# =============================================================================


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="inbox274-"))
class OutboundMediaByIdTests(TestCase):
    """#274.1 — a media_id-only send must still render in the timeline."""

    def setUp(self):
        self.tenant, self.wa_app, self.contact = make_tenant_wa_app_contact()

    def _tenant_media(self, **fields):
        from tenants.models import TenantMedia

        return TenantMedia.objects.create(
            tenant=self.tenant,
            media=SimpleUploadedFile(f"{uuid.uuid4().hex[:8]}.jpg", b"\xff\xd8\xff", content_type="image/jpeg"),
            **fields,
        )

    def _create_message(self, **extra):
        """Run the create serializer the way the inbox composer does."""
        from wa.serializers.wa_message import WAMessageCreateSerializer

        payload = {
            "wa_app": str(self.wa_app.pk),
            "contact": self.contact.pk,
            "message_type": "IMAGE",
        }
        payload.update(extra)
        serializer = WAMessageCreateSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        # post_save queues the send; nothing here wants a provider call.
        with patch("wa.signals._dispatch"):
            return serializer.save(direction="OUTBOUND", status="PENDING")

    def test_media_id_backfills_media_url_from_handle(self):
        """The session upload stores the META ID in wa_handle_id.handleId."""
        media = self._tenant_media(wa_handle_id={"handleId": "meta-media-123"})

        message = self._create_message(media_id="meta-media-123")

        self.assertTrue(message.media_url, "media_url must be back-filled from the media library")
        self.assertIn(media.media.name.split("/")[-1], message.media_url)

    def test_media_id_backfills_media_url_from_media_id_column(self):
        """Carousel-card uploads land in TenantMedia.media_id instead."""
        self._tenant_media(media_id="meta-media-456", card_index=0)

        message = self._create_message(media_id="meta-media-456")

        self.assertTrue(message.media_url)

    def test_backfilled_url_is_absolute(self):
        """The timeline is rendered by a browser, not by this host."""
        self._tenant_media(wa_handle_id={"handleId": "meta-media-abs"})

        message = self._create_message(media_id="meta-media-abs")

        self.assertTrue(message.media_url.startswith("http"), message.media_url)

    def test_cloud_api_payload_still_uses_id_not_link(self):
        """Back-filling must not put both keys in the payload — META 400s."""
        self._tenant_media(wa_handle_id={"handleId": "meta-media-789"})

        message = self._create_message(media_id="meta-media-789")

        self.assertEqual(message.raw_payload["image"], {"id": "meta-media-789"})

    def test_timeline_renders_the_backfilled_url(self):
        """The end the agent actually sees: a non-empty url in content."""
        from wa.tasks import _create_team_inbox_message_v2

        self._tenant_media(wa_handle_id={"handleId": "meta-media-timeline"})
        message = self._create_message(media_id="meta-media-timeline")

        result = _create_team_inbox_message_v2(message, self.wa_app)

        self.assertTrue(result["created"], result.get("error"))
        from team_inbox.models import Messages

        content = Messages.objects.get(pk=result["message_id"]).content
        self.assertTrue(content["image"]["url"], "the agent's own image rendered with an empty URL")

    def test_unknown_media_id_leaves_media_url_empty(self):
        """Media uploaded straight to META never passed through the library."""
        message = self._create_message(media_id="not-in-the-library")

        self.assertFalse(message.media_url)

    def test_explicit_media_url_is_not_overwritten(self):
        """A client that sends both keeps the URL it chose."""
        self._tenant_media(wa_handle_id={"handleId": "meta-media-both"})

        message = self._create_message(
            media_id="meta-media-both",
            media_url="https://cdn.example.com/given.jpg",
        )

        self.assertEqual(message.media_url, "https://cdn.example.com/given.jpg")


# =============================================================================
# 2. 24-hour service-window pre-flight
# =============================================================================


class ServiceWindowPreflightTests(TestCase):
    """#274.2 — refuse a free-form send the window has already closed on."""

    def setUp(self):
        self.tenant, self.wa_app, self.contact = make_tenant_wa_app_contact()

    def _conversation(self, *, expires_in):
        from wa.models import WaConversation

        now = timezone.now()
        return WaConversation.objects.create(
            wa_app=self.wa_app,
            contact=self.contact,
            first_message_at=now - timedelta(hours=2),
            last_inbound_at=now - timedelta(hours=2),
            service_window_expires_at=now + expires_in,
        )

    def _message(self, message_type="TEXT", **extra):
        from wa.models import MessageDirection, MessageStatus, WAMessage

        defaults = {
            "wa_app": self.wa_app,
            "contact": self.contact,
            "direction": MessageDirection.OUTBOUND,
            "status": MessageStatus.PENDING,
            "message_type": message_type,
            "text": "hello",
            "raw_payload": {
                "messaging_product": "whatsapp",
                "to": str(self.contact.phone).lstrip("+"),
                "type": message_type.lower(),
                "text": {"body": "hello"},
            },
        }
        defaults.update(extra)
        # The post_save signal would send it immediately; this test drives
        # send_outgoing_message itself.
        with patch("wa.signals._dispatch"):
            return WAMessage.objects.create(**defaults)

    # ── the service-layer decision ────────────────────────────────────

    def test_open_window_allows_send(self):
        from wa.services.conversations import outbound_window_error

        self._conversation(expires_in=timedelta(hours=3))

        self.assertIsNone(outbound_window_error(wa_app=self.wa_app, contact=self.contact))

    def test_expired_window_explains_itself(self):
        from wa.services.conversations import outbound_window_error

        self._conversation(expires_in=-timedelta(minutes=1))

        error = outbound_window_error(wa_app=self.wa_app, contact=self.contact)
        self.assertIsNotNone(error)
        self.assertIn("131047", error)

    def test_no_conversation_row_is_unknown_not_closed(self):
        """Contacts predating #189 must not all become unsendable."""
        from wa.services.conversations import outbound_window_error

        self.assertIsNone(outbound_window_error(wa_app=self.wa_app, contact=self.contact))

    # ── the send path ─────────────────────────────────────────────────

    def test_send_is_refused_when_window_closed(self):
        from wa.models import MessageStatus, WAMessage
        from wa.services.conversations import SERVICE_WINDOW_CLOSED_CODE
        from wa.tasks import send_outgoing_message

        self._conversation(expires_in=-timedelta(minutes=1))
        message = self._message()

        with patch("wa.utility.apis.meta.session_message_api.SessionMessageAPI.send_message") as send:
            result = send_outgoing_message(str(message.pk))

        send.assert_not_called()
        self.assertEqual(result["status"], "failed")
        message = WAMessage.objects.get(pk=message.pk)
        self.assertEqual(message.status, MessageStatus.FAILED)
        self.assertEqual(message.error_code, SERVICE_WINDOW_CLOSED_CODE)
        self.assertIn("24-hour", message.error_message)
        self.assertIsNotNone(message.failed_at)

    def test_refused_send_still_appears_in_the_timeline(self):
        """A reply that never left must not vanish from the agent's thread."""
        from team_inbox.models import Messages
        from wa.tasks import send_outgoing_message

        self._conversation(expires_in=-timedelta(minutes=1))
        message = self._message()

        with patch("wa.utility.apis.meta.session_message_api.SessionMessageAPI.send_message"):
            result = send_outgoing_message(str(message.pk))

        self.assertTrue(result["team_inbox_created"], result.get("team_inbox_error"))
        inbox_message = Messages.objects.get(pk=result["team_inbox_message_id"])
        self.assertEqual(inbox_message.outgoing_status, "FAILED")
        self.assertIn("24-hour", inbox_message.outgoing_error)

    def test_template_is_exempt_from_the_window(self):
        """Templates are the one thing WhatsApp accepts outside the window."""
        from wa.tasks import send_outgoing_message

        self._conversation(expires_in=-timedelta(minutes=1))
        message = self._message(
            message_type="TEMPLATE",
            raw_payload={
                "messaging_product": "whatsapp",
                "to": str(self.contact.phone).lstrip("+"),
                "type": "template",
                "template": {"name": "hello", "language": {"code": "en"}},
            },
        )

        with patch(
            "wa.utility.apis.meta.session_message_api.SessionMessageAPI.send_message",
            return_value={"messages": [{"id": "wamid.T"}]},
        ) as send:
            result = send_outgoing_message(str(message.pk))

        send.assert_called_once()
        self.assertEqual(result["status"], "sent")

    def test_open_window_sends(self):
        from wa.tasks import send_outgoing_message

        self._conversation(expires_in=timedelta(hours=3))
        message = self._message()

        with patch(
            "wa.utility.apis.meta.session_message_api.SessionMessageAPI.send_message",
            return_value={"messages": [{"id": "wamid.OK"}]},
        ) as send:
            result = send_outgoing_message(str(message.pk))

        send.assert_called_once()
        self.assertEqual(result["status"], "sent")


# =============================================================================
# 4. Media download failure
# =============================================================================


class MediaDownloadFailureTests(TestCase):
    """#274.4 — a failed download must look like a failure."""

    def setUp(self):
        self.tenant, self.wa_app, self.contact = make_tenant_wa_app_contact()

    def test_returns_empty_string_when_credentials_missing(self):
        from wa.tasks import _download_and_save_meta_media

        self.wa_app.bsp_credentials = {}
        with self.settings(META_PERM_TOKEN=""):
            url = _download_and_save_meta_media(self.wa_app, "media-abc")

        self.assertEqual(url, "", "the bare media ID used to land in content['image']['url']")

    def test_returns_empty_string_when_the_api_raises(self):
        from wa.tasks import _download_and_save_meta_media

        with patch("wa.utility.apis.meta.media_api.MetaMediaAPI.get_media_url", side_effect=Exception("boom")):
            url = _download_and_save_meta_media(self.wa_app, "media-def")

        self.assertEqual(url, "")

    def test_returns_empty_string_when_download_is_empty(self):
        from wa.tasks import _download_and_save_meta_media

        with (
            patch(
                "wa.utility.apis.meta.media_api.MetaMediaAPI.get_media_url",
                return_value={"url": "https://lookaside.example/x", "mime_type": "image/jpeg"},
            ),
            patch("wa.utility.apis.meta.media_api.MetaMediaAPI.download_media", return_value=b""),
        ):
            url = _download_and_save_meta_media(self.wa_app, "media-ghi")

        self.assertEqual(url, "")

    def test_content_marks_the_failure_instead_of_a_blank_bubble(self):
        from wa.tasks import _build_team_inbox_content

        instance = MagicMock(pk=uuid.uuid4())
        content = _build_team_inbox_content(
            {"message_type": "image", "image_link": "", "mime_type": "image/jpeg", "message_id": "wamid.X"},
            instance,
        )

        self.assertEqual(content["type"], "image")
        self.assertEqual(content["image"]["url"], "")
        self.assertEqual(content["image"]["error"], "download_failed")
        self.assertEqual(content["image"]["mime_type"], "image/jpeg")

    def test_failed_document_keeps_its_filename_and_caption(self):
        from wa.tasks import _build_team_inbox_content

        content = _build_team_inbox_content(
            {
                "message_type": "document",
                "document_link": "",
                "file_name": "invoice.pdf",
                "text": "here you go",
                "message_id": "wamid.Y",
            },
            MagicMock(pk=uuid.uuid4()),
        )

        self.assertEqual(content["type"], "document")
        self.assertEqual(content["document"]["error"], "download_failed")
        self.assertEqual(content["document"]["filename"], "invoice.pdf")
        self.assertEqual(content["document"]["caption"], "here you go")

    def test_successful_download_is_untouched(self):
        from wa.tasks import _build_team_inbox_content

        content = _build_team_inbox_content(
            {"message_type": "image", "image_link": "https://host/x.jpg", "message_id": "wamid.Z"},
            MagicMock(pk=uuid.uuid4()),
        )

        self.assertEqual(content["image"]["url"], "https://host/x.jpg")
        self.assertNotIn("error", content["image"])


# =============================================================================
# 5. Typing-indicator capability flag
# =============================================================================


class TypingIndicatorFlagTests(TestCase):
    """#274.5 — neither adapter may advertise a typing indicator."""

    def test_neither_adapter_declares_typing_indicator(self):
        from wa.adapters.gupshup import GupshupAdapter
        from wa.adapters.meta_direct import MetaDirectAdapter

        for cls in (MetaDirectAdapter, GupshupAdapter):
            with self.subTest(adapter=cls.__name__):
                self.assertFalse(
                    cls.capabilities.supports_typing_indicator,
                    f"{cls.__name__} declares a typing indicator it does not send (#266 rule)",
                )


# =============================================================================
# 6. Stale WebSocket documentation
# =============================================================================


class WebSocketDocsTests(TestCase):
    """#274.6 — every documented client→server type must be handled."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        cls.user = get_user_model().objects.create_user(
            username="ws_docs_reader",
            email="ws_docs@t.com",
            mobile="+910000099991",
            password="testpass123",
        )

    def _documented(self):
        from team_inbox.websocket_docs import websocket_info

        request = APIRequestFactory().get("/team-inbox/websocket-info/")
        force_authenticate(request, user=self.user)
        return websocket_info(request).data

    def test_documented_client_types_are_all_handled(self):
        from team_inbox.consumers import TeamInboxConsumer

        documented = self._documented()["supported_message_types"]["client_to_server"]

        self.assertTrue(documented)
        for message_type in documented:
            with self.subTest(message_type=message_type):
                self.assertTrue(
                    hasattr(TeamInboxConsumer, f"handle_{message_type}"),
                    f"docs advertise {message_type!r} but receive() has no handler for it",
                )

    def test_send_message_is_no_longer_advertised(self):
        """Replies are posted over REST — the socket never accepted them."""
        docs = self._documented()

        self.assertNotIn("send_message", docs["supported_message_types"]["client_to_server"])
        self.assertNotIn("send_message", docs["message_examples"])
