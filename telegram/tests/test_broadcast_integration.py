"""
Tests for broadcast → Telegram integration (handle_telegram_message).
"""
from unittest.mock import patch

import pytest

from broadcast.models import Broadcast, BroadcastMessage
from broadcast.tasks import handle_telegram_message
from contacts.models import TenantContact
from telegram.models import TelegramBotApp
from tenants.models import Tenant


@pytest.mark.django_db
class TestBroadcastTelegramIntegration:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant = Tenant.objects.create(name="BC TG Tenant")
        self.bot_app = TelegramBotApp.objects.create(
            tenant=self.tenant,
            bot_token="444:DDD-broadcasttest",
            bot_username="bc_bot",
            bot_user_id=444,
        )
        self.contact = TenantContact.objects.create(
            tenant=self.tenant,
            phone="+919999000001",
            first_name="BC",
            telegram_chat_id=88888,
            source="TELEGRAM",
        )
        self.broadcast = Broadcast.objects.create(
            tenant=self.tenant,
            name="TG Test Broadcast",
            platform="TELEGRAM",
            status="SENDING",
            placeholder_data={"text": "Hello from broadcast!"},
        )
        self.broadcast.recipients.add(self.contact)
        self.message = BroadcastMessage.objects.create(
            broadcast=self.broadcast,
            contact=self.contact,
            status="PENDING",
        )

    @patch("telegram.services.message_sender.TelegramMessageSender.send_text")
    def test_sends_text_message(self, mock_send_text):
        mock_send_text.return_value = {
            "success": True,
            "message_id": "42",
            "outbound_id": "abc",
        }

        result = handle_telegram_message(self.message)
        assert result["success"] is True
        assert result["message_id"] == "42"
        mock_send_text.assert_called_once()

    @patch("telegram.services.message_sender.TelegramMessageSender.send_media")
    def test_sends_media_message(self, mock_send_media):
        self.broadcast.placeholder_data = {
            "text": "Look at this",
            "media_url": "https://example.com/img.jpg",
            "media_type": "photo",
        }
        self.broadcast.save()

        mock_send_media.return_value = {
            "success": True,
            "message_id": "43",
        }

        result = handle_telegram_message(self.message)
        assert result["success"] is True
        mock_send_media.assert_called_once()

    def test_no_bot_app_returns_error(self):
        self.bot_app.is_active = False
        self.bot_app.save()

        result = handle_telegram_message(self.message)
        assert result["success"] is False
        assert "No active Telegram bot" in result["error"]

    def test_no_chat_id_returns_error(self):
        self.contact.telegram_chat_id = None
        self.contact.save()

        result = handle_telegram_message(self.message)
        assert result["success"] is False
        assert "no telegram_chat_id" in result["error"]

    def test_no_content_returns_error(self):
        self.broadcast.placeholder_data = {}
        self.broadcast.save()

        result = handle_telegram_message(self.message)
        assert result["success"] is False
        assert "no text or media" in result["error"]
