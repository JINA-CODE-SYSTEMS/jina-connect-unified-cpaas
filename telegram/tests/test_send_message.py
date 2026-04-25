"""Tests for Telegram ad-hoc message send endpoint (#98) and scheduled send (#120)."""

from __future__ import annotations

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from telegram.models import TelegramBotApp, TelegramOutboundMessage


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name="TG Send Tenant")


@pytest.fixture()
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="tg_send", email="tgsend@test.com", mobile="+919500000001", password="testpass123"
    )


@pytest.fixture()
def role(tenant):
    from tenants.models import TenantRole

    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    return role


@pytest.fixture()
def tenant_user(tenant, user, role):
    from tenants.models import TenantUser

    return TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)


@pytest.fixture()
def bot_app(tenant):
    return TelegramBotApp.objects.create(
        tenant=tenant,
        bot_token="123456:ABC-DEF",
        bot_username="test_bot",
        is_active=True,
    )


@pytest.fixture()
def api_client(tenant_user):
    client = APIClient()
    client.force_authenticate(user=tenant_user.user)
    return client


def _fake_sender_send_text(self, chat_id, text, **kwargs):
    """Patched send_text that returns success without hitting Telegram API."""
    return {"success": True, "message_id": "12345", "outbound_id": "fake-uuid"}


def _fake_sender_send_media(self, chat_id, media_type, media_url, caption=None, **kwargs):
    return {"success": True, "message_id": "12346", "outbound_id": "fake-uuid"}


# ---------------------------------------------------------------------------
# Ad-hoc send endpoint tests (#98)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTelegramSendEndpoint:
    def test_send_text_success(self, api_client, bot_app, monkeypatch):
        """POST /telegram/v1/messages/send/ with text returns 200."""
        from telegram.services.message_sender import TelegramMessageSender

        monkeypatch.setattr(TelegramMessageSender, "send_text", _fake_sender_send_text)

        resp = api_client.post(
            "/telegram/v1/messages/send/",
            {"chat_id": "99999", "text": "Hello from tests"},
            format="json",
        )

        assert resp.status_code == 200
        assert resp.data["success"] is True

    def test_send_text_buttons_builds_reply_markup(self, api_client, bot_app, monkeypatch):
        """Buttons are converted to Telegram inline_keyboard and passed to send_text."""
        from telegram.services.message_sender import TelegramMessageSender

        captured = {}

        def fake_send_text(self, chat_id, text, **kwargs):
            captured["chat_id"] = chat_id
            captured["text"] = text
            captured["reply_markup"] = kwargs.get("reply_markup")
            return {"success": True, "message_id": "12345", "outbound_id": "fake-uuid"}

        monkeypatch.setattr(TelegramMessageSender, "send_text", fake_send_text)

        resp = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "chat_id": "99999",
                "text": "Choose one",
                "buttons": [
                    {"type": "URL", "text": "Visit", "url": "https://example.com"},
                    {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+911234567890"},
                    {"type": "QUICK_REPLY", "text": "Yes"},
                ],
            },
            format="json",
        )

        assert resp.status_code == 200
        assert captured["chat_id"] == "99999"
        assert captured["text"] == "Choose one"
        assert captured["reply_markup"] == {
            "inline_keyboard": [
                [{"text": "Visit", "url": "https://example.com"}],
                [{"text": "Call", "url": "tel:+911234567890"}],
                [{"text": "Yes", "callback_data": "quick_reply:Yes"}],
            ]
        }

    def test_send_media_success(self, api_client, bot_app, monkeypatch):
        """POST /telegram/v1/messages/send/ with media_url returns 200."""
        from telegram.services.message_sender import TelegramMessageSender

        monkeypatch.setattr(TelegramMessageSender, "send_media", _fake_sender_send_media)

        resp = api_client.post(
            "/telegram/v1/messages/send/",
            {"chat_id": "99999", "media_url": "https://example.com/img.jpg", "media_type": "photo"},
            format="json",
        )

        assert resp.status_code == 200
        assert resp.data["success"] is True

    @pytest.mark.parametrize(
        ("field_name", "media_url", "expected_type"),
        [
            ("photo", "https://example.com/img.jpg", "photo"),
            ("video", "https://example.com/video.mp4", "video"),
            ("document", "https://example.com/file.pdf", "document"),
        ],
    )
    def test_media_aliases_route_to_send_media(
        self,
        api_client,
        bot_app,
        monkeypatch,
        field_name,
        media_url,
        expected_type,
    ):
        """Frontend media aliases map to the correct Telegram media_type."""
        from telegram.services.message_sender import TelegramMessageSender

        captured = {}

        def fake_send_media(self, chat_id, media_type, media_url, caption=None, **kwargs):
            captured["chat_id"] = chat_id
            captured["media_type"] = media_type
            captured["media_url"] = media_url
            captured["caption"] = caption
            captured["reply_markup"] = kwargs.get("reply_markup")
            return {"success": True, "message_id": "12346", "outbound_id": "fake-uuid"}

        monkeypatch.setattr(TelegramMessageSender, "send_media", fake_send_media)

        resp = api_client.post(
            "/telegram/v1/messages/send/",
            {"chat_id": "99999", field_name: media_url},
            format="json",
        )

        assert resp.status_code == 200
        assert captured["chat_id"] == "99999"
        assert captured["media_type"] == expected_type
        assert captured["media_url"] == media_url
        assert captured["caption"] is None
        assert captured["reply_markup"] is None

    def test_media_url_takes_precedence_over_alias_fields(self, api_client, bot_app, monkeypatch):
        """media_url remains the source of truth when alias fields are also present."""
        from telegram.services.message_sender import TelegramMessageSender

        captured = {}

        def fake_send_media(self, chat_id, media_type, media_url, caption=None, **kwargs):
            captured["media_type"] = media_type
            captured["media_url"] = media_url
            return {"success": True, "message_id": "12346", "outbound_id": "fake-uuid"}

        monkeypatch.setattr(TelegramMessageSender, "send_media", fake_send_media)

        resp = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "chat_id": "99999",
                "media_url": "https://example.com/primary.pdf",
                "media_type": "document",
                "photo": "https://example.com/ignored.jpg",
            },
            format="json",
        )

        assert resp.status_code == 200
        assert captured["media_type"] == "document"
        assert captured["media_url"] == "https://example.com/primary.pdf"

    def test_send_media_with_buttons_passes_reply_markup(self, api_client, bot_app, monkeypatch):
        """Buttons are preserved when sending media payloads."""
        from telegram.services.message_sender import TelegramMessageSender

        captured = {}

        def fake_send_media(self, chat_id, media_type, media_url, caption=None, **kwargs):
            captured["chat_id"] = chat_id
            captured["media_type"] = media_type
            captured["media_url"] = media_url
            captured["caption"] = caption
            captured["reply_markup"] = kwargs.get("reply_markup")
            return {"success": True, "message_id": "12346", "outbound_id": "fake-uuid"}

        monkeypatch.setattr(TelegramMessageSender, "send_media", fake_send_media)

        resp = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "chat_id": "99999",
                "photo": "https://example.com/img.jpg",
                "text": "See attachment",
                "buttons": [{"type": "QUICK_REPLY", "text": "Acknowledge"}],
            },
            format="json",
        )

        assert resp.status_code == 200
        assert captured["chat_id"] == "99999"
        assert captured["media_type"] == "photo"
        assert captured["media_url"] == "https://example.com/img.jpg"
        assert captured["caption"] == "See attachment"
        assert captured["reply_markup"] == {
            "inline_keyboard": [[{"text": "Acknowledge", "callback_data": "quick_reply:Acknowledge"}]]
        }

    def test_rejects_empty_payload(self, api_client, bot_app):
        """text and media_url both absent → 400."""
        resp = api_client.post("/telegram/v1/messages/send/", {"chat_id": "99999"}, format="json")

        assert resp.status_code == 400

    def test_no_bot_app_returns_400(self, api_client, tenant):
        """If no active TelegramBotApp exists, returns 400."""
        resp = api_client.post(
            "/telegram/v1/messages/send/",
            {"chat_id": "99999", "text": "hi"},
            format="json",
        )

        assert resp.status_code == 400
        assert "No active Telegram bot" in resp.data["error"]

    def test_unauthenticated_returns_401(self, bot_app):
        """Unauthenticated request returns 401."""
        client = APIClient()
        resp = client.post(
            "/telegram/v1/messages/send/",
            {"chat_id": "99999", "text": "hi"},
            format="json",
        )

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scheduled send task tests (#120)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendScheduledTelegramMessages:
    def test_sends_due_message(self, bot_app, monkeypatch):
        """Due PENDING messages are sent and status updated to SENT."""
        from telegram.services.bot_client import TelegramBotClient
        from telegram.tasks import send_scheduled_telegram_messages

        msg = TelegramOutboundMessage.objects.create(
            tenant=bot_app.tenant,
            bot_app=bot_app,
            chat_id=12345,
            message_type="TEXT",
            status="PENDING",
            scheduled_time=timezone.now() - timezone.timedelta(minutes=1),
            request_payload={"text": "Scheduled hello"},
        )

        monkeypatch.setattr(
            TelegramBotClient,
            "send_message",
            lambda self, chat_id, **kwargs: {"ok": True, "result": {"message_id": 9999}},
        )

        result = send_scheduled_telegram_messages()

        assert result["sent"] == 1
        msg.refresh_from_db()
        assert msg.status == "SENT"
        assert msg.provider_message_id == 9999

    def test_skips_non_due_message(self, bot_app, monkeypatch):
        """Messages with future scheduled_time are NOT sent."""
        from telegram.tasks import send_scheduled_telegram_messages

        TelegramOutboundMessage.objects.create(
            tenant=bot_app.tenant,
            bot_app=bot_app,
            chat_id=12345,
            message_type="TEXT",
            status="PENDING",
            scheduled_time=timezone.now() + timezone.timedelta(hours=1),
            request_payload={"text": "Future hello"},
        )

        result = send_scheduled_telegram_messages()
        assert result["sent"] == 0

    def test_failed_send_marks_failed(self, bot_app, monkeypatch):
        """If send raises an exception, message is marked FAILED."""
        from telegram.services.bot_client import TelegramBotClient
        from telegram.tasks import send_scheduled_telegram_messages

        msg = TelegramOutboundMessage.objects.create(
            tenant=bot_app.tenant,
            bot_app=bot_app,
            chat_id=12345,
            message_type="TEXT",
            status="PENDING",
            scheduled_time=timezone.now() - timezone.timedelta(minutes=1),
            request_payload={"text": "Will fail"},
        )

        monkeypatch.setattr(
            TelegramBotClient,
            "send_message",
            lambda self, chat_id, **kwargs: (_ for _ in ()).throw(Exception("API error")),
        )

        result = send_scheduled_telegram_messages()

        assert result["sent"] == 0
        msg.refresh_from_db()
        assert msg.status == "FAILED"
        assert msg.sent_at is None
        assert msg.failed_at is not None
        assert msg.error_message

    def test_rate_limited_stays_pending(self, bot_app, monkeypatch):
        """429 rate-limit error maps to PENDING for retry via TELEGRAM_ERROR_MAP (#125)."""
        from telegram.services.bot_client import TelegramAPIError, TelegramBotClient
        from telegram.tasks import send_scheduled_telegram_messages

        msg = TelegramOutboundMessage.objects.create(
            tenant=bot_app.tenant,
            bot_app=bot_app,
            chat_id=12345,
            message_type="TEXT",
            status="PENDING",
            scheduled_time=timezone.now() - timezone.timedelta(minutes=1),
            request_payload={"text": "Rate limited"},
        )

        def raise_429(self, chat_id, **kwargs):
            raise TelegramAPIError(429, "Too Many Requests: retry after 30")

        monkeypatch.setattr(TelegramBotClient, "send_message", raise_429)

        result = send_scheduled_telegram_messages()

        assert result["sent"] == 0
        msg.refresh_from_db()
        assert msg.status == "PENDING"
        assert msg.failed_at is None

    def test_chat_id_popped_from_payload(self, bot_app, monkeypatch):
        """chat_id in request_payload doesn't conflict with explicit kwarg (#20)."""
        from telegram.services.bot_client import TelegramBotClient
        from telegram.tasks import send_scheduled_telegram_messages

        TelegramOutboundMessage.objects.create(
            tenant=bot_app.tenant,
            bot_app=bot_app,
            chat_id=12345,
            message_type="TEXT",
            status="PENDING",
            scheduled_time=timezone.now() - timezone.timedelta(minutes=1),
            request_payload={"chat_id": 99999, "text": "Conflict test"},
        )

        captured_kwargs = {}

        def fake_send(self, chat_id, **kwargs):
            captured_kwargs["chat_id"] = chat_id
            captured_kwargs.update(kwargs)
            return {"ok": True, "result": {"message_id": 1}}

        monkeypatch.setattr(TelegramBotClient, "send_message", fake_send)

        send_scheduled_telegram_messages()

        # The explicit chat_id from the model field should be used, not from payload
        assert captured_kwargs["chat_id"] == 12345

    def test_stale_sending_recovered_to_pending(self, bot_app, monkeypatch):
        """Rows stuck in SENDING for > 5 min (by updated_at) are recovered and retried."""
        from telegram.services.bot_client import TelegramBotClient
        from telegram.tasks import send_scheduled_telegram_messages

        msg = TelegramOutboundMessage.objects.create(
            tenant=bot_app.tenant,
            bot_app=bot_app,
            chat_id=12345,
            message_type="TEXT",
            status="SENDING",
            scheduled_time=timezone.now() - timezone.timedelta(minutes=10),
            request_payload={"text": "Stuck message"},
        )
        # Backdate updated_at so the row looks stale (auto_now prevents direct set)
        TelegramOutboundMessage.objects.filter(pk=msg.pk).update(
            updated_at=timezone.now() - timezone.timedelta(minutes=10)
        )

        monkeypatch.setattr(
            TelegramBotClient,
            "send_message",
            lambda self, chat_id, **kwargs: {"ok": True, "result": {"message_id": 7777}},
        )

        result = send_scheduled_telegram_messages()

        msg.refresh_from_db()
        # The stale SENDING row should have been recovered to PENDING,
        # then claimed and sent successfully in the same tick.
        assert msg.status == "SENT"
        assert msg.provider_message_id == 7777
        assert result["sent"] == 1

    def test_recently_claimed_sending_not_recovered(self, bot_app, monkeypatch):
        """A SENDING row claimed moments ago must NOT be reset (no duplicate send)."""
        from telegram.tasks import send_scheduled_telegram_messages

        msg = TelegramOutboundMessage.objects.create(
            tenant=bot_app.tenant,
            bot_app=bot_app,
            chat_id=12345,
            message_type="TEXT",
            status="SENDING",
            # Scheduled long ago, but updated_at is fresh (just claimed)
            scheduled_time=timezone.now() - timezone.timedelta(hours=1),
            request_payload={"text": "In-flight message"},
        )
        # updated_at is auto-set to now by create(), so it's fresh — no backdate

        result = send_scheduled_telegram_messages()

        msg.refresh_from_db()
        # Must still be SENDING — the sweep should NOT touch it
        assert msg.status == "SENDING"
        assert result["sent"] == 0


# ---------------------------------------------------------------------------
# Contact ID lookup tests (PR #135)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTelegramSendWithContactId:
    """Test Telegram message sending with contact_id parameter for telegram_chat_id lookup."""

    @pytest.fixture()
    def contact(self, tenant):
        """Contact with telegram_chat_id."""
        from contacts.models import TenantContact

        return TenantContact.objects.create(
            tenant=tenant,
            first_name="Test",
            last_name="User",
            phone="+919876543210",
            telegram_chat_id="987654321",
        )

    @pytest.fixture()
    def contact_no_telegram(self, tenant):
        """Contact without telegram_chat_id."""
        from contacts.models import TenantContact

        return TenantContact.objects.create(
            tenant=tenant,
            first_name="No",
            last_name="Telegram",
            phone="+919999999999",
            telegram_chat_id=None,
        )

    @pytest.fixture()
    def other_tenant(self, db):
        """Different tenant for cross-tenant isolation tests."""
        from tenants.models import Tenant

        return Tenant.objects.create(name="Other Tenant")

    @pytest.fixture()
    def other_contact(self, other_tenant):
        """Contact belonging to a different tenant."""
        from contacts.models import TenantContact

        return TenantContact.objects.create(
            tenant=other_tenant,
            first_name="Other",
            last_name="Contact",
            phone="+918888888888",
            telegram_chat_id="111222333",
        )

    def test_send_text_with_contact_id_only(self, api_client, bot_app, contact, monkeypatch):
        """Test sending text message with only contact_id (no chat_id)."""
        from telegram.services.message_sender import TelegramMessageSender

        monkeypatch.setattr(TelegramMessageSender, "send_text", _fake_sender_send_text)

        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": contact.id,
                "text": "Hello from contact_id test",
            },
            format="json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_send_text_with_chat_id_only(self, api_client, bot_app, monkeypatch):
        """Test sending with only chat_id (original behavior - backward compatibility)."""
        from telegram.services.message_sender import TelegramMessageSender

        monkeypatch.setattr(TelegramMessageSender, "send_text", _fake_sender_send_text)

        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "chat_id": "111222333",
                "text": "Hello from chat_id test",
            },
            format="json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_send_with_both_chat_id_and_contact_id(self, api_client, bot_app, contact, monkeypatch):
        """Test that chat_id takes precedence when both are provided."""
        from telegram.services.message_sender import TelegramMessageSender

        call_args = []

        def capture_send_text(self, chat_id, text, **kwargs):
            call_args.append({"chat_id": chat_id, "text": text})
            return _fake_sender_send_text(self, chat_id, text, **kwargs)

        monkeypatch.setattr(TelegramMessageSender, "send_text", capture_send_text)

        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "chat_id": "explicit_chat_id",
                "contact_id": contact.id,
                "text": "Test precedence",
            },
            format="json",
        )

        assert response.status_code == 200
        # Verify explicit chat_id was used (not looked up from contact)
        assert call_args[0]["chat_id"] == "explicit_chat_id"

    def test_send_without_chat_id_or_contact_id(self, api_client, bot_app):
        """Test validation error when neither chat_id nor contact_id provided."""
        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "text": "Missing identifiers",
            },
            format="json",
        )

        assert response.status_code == 400
        error_msg = str(response.data).lower()
        assert "chat_id" in error_msg or "contact_id" in error_msg

    def test_send_with_invalid_contact_id(self, api_client, bot_app):
        """Test error when contact_id does not exist."""
        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": 99999,
                "text": "Invalid contact",
            },
            format="json",
        )

        assert response.status_code == 400
        assert "not found" in response.json()["error"].lower()

    def test_send_with_contact_without_telegram_chat_id(self, api_client, bot_app, contact_no_telegram):
        """Test error when contact doesn't have telegram_chat_id."""
        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": contact_no_telegram.id,
                "text": "No telegram chat id",
            },
            format="json",
        )

        assert response.status_code == 400
        assert "telegram_chat_id" in response.json()["error"].lower()

    def test_send_cross_tenant_isolation(self, api_client, bot_app, other_contact):
        """Test that contact_id from a different tenant is rejected."""
        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": other_contact.id,
                "text": "Cross-tenant attempt",
            },
            format="json",
        )

        assert response.status_code == 400
        assert "not found" in response.json()["error"].lower()

    def test_send_media_with_contact_id(self, api_client, bot_app, contact, monkeypatch):
        """Test sending media message using contact_id lookup."""
        from telegram.services.message_sender import TelegramMessageSender

        monkeypatch.setattr(TelegramMessageSender, "send_media", _fake_sender_send_media)

        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": contact.id,
                "media_url": "https://example.com/photo.jpg",
                "media_type": "photo",
                "text": "Caption text",
            },
            format="json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_outbound_message_links_contact(self, api_client, bot_app, contact, monkeypatch):
        """Test that TelegramOutboundMessage.contact is properly linked when using contact_id."""
        from telegram.services.message_sender import TelegramMessageSender

        # Create a real outbound message instead of just faking the response
        def real_send_text(self, chat_id, text, contact=None, **kwargs):
            msg = TelegramOutboundMessage.objects.create(
                tenant=self.bot_app.tenant,
                bot_app=self.bot_app,
                chat_id=chat_id,
                message_type="TEXT",
                contact=contact,
                status="SENT",
                provider_message_id=12345,
                request_payload={"text": text},
            )
            return {"success": True, "message_id": 12345, "outbound_id": str(msg.id)}

        monkeypatch.setattr(TelegramMessageSender, "send_text", real_send_text)

        response = api_client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": contact.id,
                "text": "Test contact linkage",
            },
            format="json",
        )

        assert response.status_code == 200

        # Verify outbound message was created with correct contact link
        msg = TelegramOutboundMessage.objects.filter(
            tenant=bot_app.tenant,
            contact=contact,
        ).first()
        assert msg is not None
        assert msg.contact == contact
        assert str(msg.chat_id) == str(contact.telegram_chat_id)
