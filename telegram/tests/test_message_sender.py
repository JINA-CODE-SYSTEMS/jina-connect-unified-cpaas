"""
Tests for TelegramMessageSender — send text/media, persistence, rate limiting.
"""

from unittest.mock import MagicMock, patch

import pytest

from telegram.services.message_sender import TelegramMessageSender


@pytest.mark.django_db
class TestTelegramMessageSender:
    @pytest.fixture(autouse=True)
    def setup(self, bot_app, contact):
        self.bot_app = bot_app
        self.contact = contact
        self.chat_id = str(contact.telegram_chat_id)

    def _make_sender(self):
        """Create a sender with a mocked bot client (avoids real HTTP)."""
        with patch("telegram.services.bot_client.TelegramBotClient"):
            sender = TelegramMessageSender(self.bot_app)
        sender.client = MagicMock()
        return sender

    @patch("telegram.services.rate_limiter.check_rate_limit", return_value=True)
    def test_send_text_success(self, mock_rate):
        sender = self._make_sender()
        sender.client.send_message.return_value = {"message_id": 42, "chat": {"id": 99887766}}

        result = sender.send_text(chat_id=self.chat_id, text="Hello!", contact=self.contact)
        assert result["success"] is True
        assert result["message_id"] == "42"
        sender.client.send_message.assert_called_once()

    @patch("telegram.services.rate_limiter.check_rate_limit", return_value=True)
    def test_send_media_photo(self, mock_rate):
        sender = self._make_sender()
        sender.client.send_photo.return_value = {"message_id": 43}

        result = sender.send_media(
            chat_id=self.chat_id,
            media_type="photo",
            media_url="https://example.com/img.jpg",
            caption="Look at this",
            contact=self.contact,
        )
        assert result["success"] is True
        sender.client.send_photo.assert_called_once()

    @patch("telegram.services.rate_limiter.check_rate_limit", return_value=True)
    def test_send_media_document(self, mock_rate):
        sender = self._make_sender()
        sender.client.send_document.return_value = {"message_id": 44}

        result = sender.send_media(
            chat_id=self.chat_id,
            media_type="document",
            media_url="https://example.com/file.pdf",
            contact=self.contact,
        )
        assert result["success"] is True

    @patch("telegram.services.rate_limiter.check_rate_limit", return_value=True)
    def test_send_media_unsupported_type(self, mock_rate):
        sender = self._make_sender()
        result = sender.send_media(
            chat_id=self.chat_id,
            media_type="sticker",
            media_url="https://example.com/sticker.webp",
        )
        assert result["success"] is False
        assert "Unsupported media type" in result["error"]

    @patch("telegram.services.rate_limiter.check_rate_limit", return_value=True)
    def test_send_keyboard(self, mock_rate):
        sender = self._make_sender()
        sender.client.send_message.return_value = {"message_id": 45}

        buttons = [[{"text": "Yes", "callback_data": "yes"}, {"text": "No", "callback_data": "no"}]]
        result = sender.send_keyboard(
            chat_id=self.chat_id,
            text="Choose:",
            keyboard=buttons,
            contact=self.contact,
        )
        assert result["success"] is True

    @patch("telegram.services.rate_limiter.check_rate_limit", return_value=False)
    def test_rate_limit_blocks_send_text(self, mock_rate):
        sender = TelegramMessageSender(self.bot_app)
        result = sender.send_text(chat_id=self.chat_id, text="Should be blocked")
        assert result["success"] is False
        assert "Rate limit" in result["error"]

    @patch("telegram.services.rate_limiter.check_rate_limit", return_value=False)
    def test_rate_limit_blocks_send_media(self, mock_rate):
        sender = TelegramMessageSender(self.bot_app)
        result = sender.send_media(
            chat_id=self.chat_id,
            media_type="photo",
            media_url="https://example.com/img.jpg",
        )
        assert result["success"] is False
        assert "Rate limit" in result["error"]

    @patch("telegram.services.rate_limiter.check_rate_limit", return_value=False)
    def test_rate_limit_blocks_send_keyboard(self, mock_rate):
        sender = TelegramMessageSender(self.bot_app)
        result = sender.send_keyboard(
            chat_id=self.chat_id,
            text="Pick one",
            keyboard=[[{"text": "A", "callback_data": "a"}]],
        )
        assert result["success"] is False
        assert "Rate limit" in result["error"]

    def test_get_channel_name(self):
        sender = TelegramMessageSender(self.bot_app)
        assert sender.get_channel_name() == "TELEGRAM"
