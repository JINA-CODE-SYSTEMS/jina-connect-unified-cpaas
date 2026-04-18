"""
Telegram media handler — download files from Telegram servers.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class TelegramMediaHandler:
    """Handle media download for Telegram messages."""

    def __init__(self, client):
        """
        Args:
            client: A TelegramBotClient instance.
        """
        self.client = client

    # Telegram allows files up to 2 GB but the Bot API caps downloads at 20 MB (#128).
    # We mirror that limit here to protect worker memory.
    MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

    def download_file(self, file_id: str) -> tuple[bytes, str]:
        """
        Download a file from Telegram servers.

        Raises ``ValueError`` if the reported file size exceeds
        :pyattr:`MAX_DOWNLOAD_BYTES`.

        Returns:
            Tuple of (file_content_bytes, file_path_on_telegram).
        """
        file_info = self.client.get_file(file_id)
        file_size = file_info.get("file_size")
        if file_size and file_size > self.MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"Telegram file_id={file_id} is {file_size} bytes, exceeds limit {self.MAX_DOWNLOAD_BYTES}"
            )

        file_path = file_info.get("file_path", "")
        url = self.client.get_file_url(file_path)

        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content, file_path

    def get_media_from_message(self, message: dict) -> Optional[dict]:
        """
        Extract media info from a Telegram message update.

        Returns a dict with ``file_id``, ``file_name``, ``mime_type``, and
        ``media_type`` — or None if the message has no media.
        """
        if "photo" in message:
            photos = message["photo"]
            largest = photos[-1] if photos else {}
            return {
                "media_type": "photo",
                "file_id": largest.get("file_id", ""),
                "file_name": "photo.jpg",
                "mime_type": "image/jpeg",
            }

        if "document" in message:
            doc = message["document"]
            return {
                "media_type": "document",
                "file_id": doc.get("file_id", ""),
                "file_name": doc.get("file_name", "document"),
                "mime_type": doc.get("mime_type", "application/octet-stream"),
            }

        if "video" in message:
            vid = message["video"]
            return {
                "media_type": "video",
                "file_id": vid.get("file_id", ""),
                "file_name": vid.get("file_name", "video.mp4"),
                "mime_type": vid.get("mime_type", "video/mp4"),
            }

        if "audio" in message:
            aud = message["audio"]
            return {
                "media_type": "audio",
                "file_id": aud.get("file_id", ""),
                "file_name": aud.get("file_name", "audio.mp3"),
                "mime_type": aud.get("mime_type", "audio/mpeg"),
            }

        if "voice" in message:
            voice = message["voice"]
            return {
                "media_type": "voice",
                "file_id": voice.get("file_id", ""),
                "file_name": "voice.ogg",
                "mime_type": voice.get("mime_type", "audio/ogg"),
            }

        return None
