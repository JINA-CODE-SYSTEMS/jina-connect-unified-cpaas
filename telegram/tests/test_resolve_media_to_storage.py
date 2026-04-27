"""Test for #128: incoming Telegram media is downloaded and persisted."""

from unittest.mock import patch

import pytest


@pytest.mark.django_db
class TestResolveMediaToStorage:
    def test_replaces_file_id_with_storage_url(self, bot_app):
        """#128: file_id is downloaded via TelegramMediaHandler and replaced with a URL."""
        from telegram.tasks import _resolve_media_to_storage

        content_in = {
            "type": "image",
            "body": {"text": "caption"},
            "media": {"file_id": "tg-file-123", "mime_type": "image/jpeg"},
        }

        with (
            patch("telegram.services.bot_client.TelegramBotClient"),
            patch("telegram.services.media_handler.TelegramMediaHandler") as mock_handler_cls,
            patch(
                "django.core.files.storage.default_storage.save", return_value="telegram/1/tg-file-123.jpg"
            ) as mock_save,
            patch("django.core.files.storage.default_storage.url", return_value="https://gcs.example/url") as mock_url,
        ):
            mock_handler = mock_handler_cls.return_value
            mock_handler.download_file.return_value = (b"binary", "photos/file_1.jpg")

            content_out = _resolve_media_to_storage(bot_app, content_in, bot_app.tenant)

        assert content_out["media"]["url"] == "https://gcs.example/url"
        assert "file_id" not in content_out["media"]
        assert content_out["media"]["mime_type"] == "image/jpeg"
        # Original dict is not mutated
        assert content_in["media"]["file_id"] == "tg-file-123"
        mock_save.assert_called_once()
        mock_url.assert_called_once()

    def test_no_op_when_file_id_missing(self, bot_app):
        """If content has no media.file_id the function returns input unchanged."""
        from telegram.tasks import _resolve_media_to_storage

        content_in = {"type": "text", "body": {"text": "hi"}}
        assert _resolve_media_to_storage(bot_app, content_in, bot_app.tenant) is content_in

    def test_base_url_preferred_over_sites_framework(self, bot_app):
        """#147 regression: BASE_URL from settings is used before Sites framework.

        When default_storage.url() returns a relative path, the absolute URL must
        be built from settings.BASE_URL — Site.objects must never be queried.
        """
        from telegram.tasks import _resolve_media_to_storage

        content_in = {
            "type": "image",
            "body": {"text": ""},
            "media": {"file_id": "test-file-id", "mime_type": "image/jpeg"},
        }

        with (
            patch("telegram.services.bot_client.TelegramBotClient"),
            patch("telegram.services.media_handler.TelegramMediaHandler") as mock_handler_cls,
            patch("django.core.files.storage.default_storage.save", return_value="telegram/1/test-file-id.jpg"),
            patch("django.core.files.storage.default_storage.url", return_value="/media/telegram/1/test-file-id.jpg"),
            patch("django.conf.settings.BASE_URL", "https://api.myserver.com", create=True),
            patch("django.contrib.sites.models.Site.objects") as mock_site,
        ):
            mock_handler_cls.return_value.download_file.return_value = (b"bytes", "photos/f.jpg")

            content_out = _resolve_media_to_storage(bot_app, content_in, bot_app.tenant)

        assert content_out["media"]["url"] == "https://api.myserver.com/media/telegram/1/test-file-id.jpg"
        mock_site.get.assert_not_called()
