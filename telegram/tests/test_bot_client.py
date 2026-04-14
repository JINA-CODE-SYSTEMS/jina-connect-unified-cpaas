"""
Tests for TelegramBotClient — HTTP methods, error handling, retry logic, token masking.
"""

from unittest.mock import MagicMock, patch

import pytest

from telegram.services.bot_client import TelegramAPIError, TelegramBotClient


class TestTelegramBotClient:
    """Unit tests for the low-level Telegram Bot API client."""

    def setup_method(self):
        self.client = TelegramBotClient(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")

    def test_repr_masks_token(self):
        assert "***" in repr(self.client)
        assert "ABC-DEF" not in repr(self.client)

    def test_masked_token_shows_last_4(self):
        assert self.client._masked.endswith("w11")
        assert len(self.client._masked) == 7  # "***" + last 4

    @patch("telegram.services.bot_client.requests.post")
    def test_get_me_success(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"ok": True, "result": {"id": 123, "is_bot": True, "username": "test_bot"}},
        )
        result = self.client.get_me()
        assert result["id"] == 123
        assert result["is_bot"] is True

    @patch("telegram.services.bot_client.requests.post")
    def test_send_message_success(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"ok": True, "result": {"message_id": 42, "chat": {"id": 100}}},
        )
        result = self.client.send_message(chat_id=100, text="Hello!")
        assert result["message_id"] == 42

        call_args = mock_post.call_args
        assert call_args[1]["json"]["chat_id"] == 100
        assert call_args[1]["json"]["text"] == "Hello!"

    @patch("telegram.services.bot_client.requests.post")
    def test_send_photo_success(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"ok": True, "result": {"message_id": 43}},
        )
        result = self.client.send_photo(chat_id=100, photo="https://example.com/img.jpg", caption="Look!")
        assert result["message_id"] == 43

    @patch("telegram.services.bot_client.requests.post")
    def test_api_error_raises(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
            status_code=400,
        )
        with pytest.raises(TelegramAPIError) as exc_info:
            self.client.send_message(chat_id=999, text="fail")
        assert exc_info.value.status_code == 400
        assert "chat not found" in exc_info.value.description

    @patch("telegram.services.bot_client.requests.post")
    def test_403_blocked_raises(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"},
            status_code=403,
        )
        with pytest.raises(TelegramAPIError) as exc_info:
            self.client.send_message(chat_id=100, text="hi")
        assert exc_info.value.status_code == 403

    @patch("telegram.services.bot_client.requests.post")
    def test_answer_callback_query(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"ok": True, "result": True},
        )
        result = self.client.answer_callback_query("callback_123")
        assert result is True

    @patch("telegram.services.bot_client.requests.post")
    def test_get_file(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {
                "ok": True,
                "result": {"file_id": "abc123", "file_path": "photos/file_0.jpg"},
            },
        )
        result = self.client.get_file("abc123")
        assert result["file_path"] == "photos/file_0.jpg"

    def test_get_file_url(self):
        url = self.client.get_file_url("photos/file_0.jpg")
        assert url.endswith("/photos/file_0.jpg")
        # Token should be in URL (needed for download) but let's just check structure
        assert "/file/bot" in url
