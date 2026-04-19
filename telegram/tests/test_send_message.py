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
