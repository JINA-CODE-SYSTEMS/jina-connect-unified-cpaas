"""Unit tests for Telegram media extraction and downloads."""

from unittest.mock import MagicMock, patch

import pytest

from telegram.services.media_handler import TelegramMediaHandler


class TestTelegramMediaHandler:
    def setup_method(self):
        self.client = MagicMock()
        self.handler = TelegramMediaHandler(self.client)

    @patch("telegram.services.media_handler.requests.get")
    def test_download_file_success(self, mock_get):
        self.client.get_file.return_value = {"file_path": "photos/file_1.jpg"}
        self.client.get_file_url.return_value = "https://api.telegram.org/file/file_1.jpg"

        resp = MagicMock()
        resp.content = b"binary-image"
        mock_get.return_value = resp

        content, file_path = self.handler.download_file("file-id-1")

        self.client.get_file.assert_called_once_with("file-id-1")
        self.client.get_file_url.assert_called_once_with("photos/file_1.jpg")
        mock_get.assert_called_once_with("https://api.telegram.org/file/file_1.jpg", timeout=60)
        resp.raise_for_status.assert_called_once()
        assert content == b"binary-image"
        assert file_path == "photos/file_1.jpg"

    def test_get_media_from_message_photo_uses_largest(self):
        message = {
            "photo": [
                {"file_id": "small"},
                {"file_id": "large"},
            ]
        }

        media = self.handler.get_media_from_message(message)

        assert media == {
            "media_type": "photo",
            "file_id": "large",
            "file_name": "photo.jpg",
            "mime_type": "image/jpeg",
        }

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            (
                {
                    "document": {
                        "file_id": "doc-id",
                        "file_name": "invoice.pdf",
                        "mime_type": "application/pdf",
                    }
                },
                {
                    "media_type": "document",
                    "file_id": "doc-id",
                    "file_name": "invoice.pdf",
                    "mime_type": "application/pdf",
                },
            ),
            (
                {
                    "video": {
                        "file_id": "vid-id",
                        "file_name": "clip.mp4",
                        "mime_type": "video/mp4",
                    }
                },
                {
                    "media_type": "video",
                    "file_id": "vid-id",
                    "file_name": "clip.mp4",
                    "mime_type": "video/mp4",
                },
            ),
            (
                {
                    "audio": {
                        "file_id": "aud-id",
                        "file_name": "song.mp3",
                        "mime_type": "audio/mpeg",
                    }
                },
                {
                    "media_type": "audio",
                    "file_id": "aud-id",
                    "file_name": "song.mp3",
                    "mime_type": "audio/mpeg",
                },
            ),
            (
                {
                    "voice": {
                        "file_id": "voice-id",
                        "mime_type": "audio/ogg",
                    }
                },
                {
                    "media_type": "voice",
                    "file_id": "voice-id",
                    "file_name": "voice.ogg",
                    "mime_type": "audio/ogg",
                },
            ),
        ],
    )
    def test_get_media_from_message_supported_types(self, message, expected):
        assert self.handler.get_media_from_message(message) == expected

    def test_get_media_from_message_defaults_for_missing_fields(self):
        media = self.handler.get_media_from_message({"document": {"file_id": "doc-id"}})

        assert media == {
            "media_type": "document",
            "file_id": "doc-id",
            "file_name": "document",
            "mime_type": "application/octet-stream",
        }

    def test_get_media_from_message_returns_none_for_non_media(self):
        assert self.handler.get_media_from_message({"text": "hello"}) is None
