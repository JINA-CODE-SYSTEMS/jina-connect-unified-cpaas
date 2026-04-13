"""
Low-level Telegram Bot API HTTP client.

Usage:
    client = TelegramBotClient(token="123456:ABC-DEF...")
    me = client.get_me()
    client.send_message(chat_id=12345, text="Hello!")

Security:
    - Bot token is NEVER logged; use ``masked_token`` for diagnostics.
    - Retries on 429 (rate-limited) and 5xx with exponential backoff.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.telegram.org/bot{token}/{method}"


class TelegramAPIError(Exception):
    """Raised when the Telegram Bot API returns a non-ok response."""

    def __init__(self, status_code: int, description: str, parameters: dict | None = None):
        self.status_code = status_code
        self.description = description
        self.parameters = parameters or {}
        super().__init__(f"Telegram API {status_code}: {description}")


class TelegramBotClient:
    """Thin, synchronous wrapper around the Telegram Bot API."""

    def __init__(self, token: str):
        self._token = token
        self._masked = f"***{token[-4:]}" if len(token) > 4 else "***"
        self._max_retries = getattr(settings, "TELEGRAM_MAX_RETRIES", 3)
        self._timeout = getattr(settings, "TELEGRAM_REQUEST_TIMEOUT", 30)

    def __repr__(self):
        return f"<TelegramBotClient token={self._masked}>"

    # ── Core transport ────────────────────────────────────────────────────

    def _request(self, method: str, data: dict | None = None, files: dict | None = None) -> dict:
        """
        Call a Telegram Bot API method with retries on transient errors.

        Returns the ``result`` field of the Telegram response.
        """
        url = _BASE_URL.format(token=self._token, method=method)
        last_exc = None

        for attempt in range(1, self._max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    json=data if not files else None,
                    data=data if files else None,
                    files=files,
                    timeout=self._timeout,
                )

                # Guard against non-JSON responses (e.g. HTML error pages from 5xx)
                try:
                    body = resp.json()
                except (ValueError, requests.exceptions.JSONDecodeError):
                    if 500 <= resp.status_code < 600 and attempt < self._max_retries:
                        backoff = 2**attempt
                        logger.warning(
                            "[TelegramBotClient] Non-JSON %s response on %s, retrying in %ss (token=%s)",
                            resp.status_code,
                            method,
                            backoff,
                            self._masked,
                        )
                        time.sleep(backoff)
                        continue
                    raise TelegramAPIError(
                        resp.status_code,
                        f"Non-JSON response (HTTP {resp.status_code})",
                    )

                if body.get("ok"):
                    return body["result"]

                error_code = body.get("error_code", resp.status_code)
                description = body.get("description", "Unknown error")
                parameters = body.get("parameters", {})

                # Rate limited — honour retry_after
                if error_code == 429:
                    retry_after = parameters.get("retry_after", 2**attempt)
                    logger.warning(
                        "[TelegramBotClient] 429 rate-limited on %s, retry_after=%s (attempt %s/%s, token=%s)",
                        method,
                        retry_after,
                        attempt,
                        self._max_retries,
                        self._masked,
                    )
                    if attempt < self._max_retries:
                        time.sleep(min(retry_after, 30))
                        continue

                # 5xx — transient server error
                if 500 <= error_code < 600 and attempt < self._max_retries:
                    backoff = 2**attempt
                    logger.warning(
                        "[TelegramBotClient] %s on %s, retrying in %ss (attempt %s/%s, token=%s)",
                        error_code,
                        method,
                        backoff,
                        attempt,
                        self._max_retries,
                        self._masked,
                    )
                    time.sleep(backoff)
                    continue

                raise TelegramAPIError(error_code, description, parameters)

            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    backoff = 2**attempt
                    logger.warning(
                        "[TelegramBotClient] Network error on %s: %s, retrying in %ss (attempt %s/%s, token=%s)",
                        method,
                        exc,
                        backoff,
                        attempt,
                        self._max_retries,
                        self._masked,
                    )
                    time.sleep(backoff)
                    continue
                raise TelegramAPIError(0, f"Network error: {exc}") from exc

        raise TelegramAPIError(0, f"Max retries exceeded: {last_exc}")

    # ── Bot info ──────────────────────────────────────────────────────────

    def get_me(self) -> dict:
        """Call ``getMe`` — returns basic bot info."""
        return self._request("getMe")

    # ── Webhook management ────────────────────────────────────────────────

    def set_webhook(self, url: str, secret_token: str, **kwargs) -> bool:
        """Register a webhook URL with Telegram."""
        data: Dict[str, Any] = {"url": url, "secret_token": secret_token}
        if "allowed_updates" in kwargs:
            data["allowed_updates"] = kwargs["allowed_updates"]
        if "max_connections" in kwargs:
            data["max_connections"] = kwargs["max_connections"]
        return self._request("setWebhook", data)

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        """Remove the current webhook."""
        return self._request("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    # ── Sending messages ──────────────────────────────────────────────────

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[dict] = None,
        **kwargs,
    ) -> dict:
        """Send a text message (``sendMessage``)."""
        data: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = reply_markup
        data.update(kwargs)
        return self._request("sendMessage", data)

    def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        caption: Optional[str] = None,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[dict] = None,
        **kwargs,
    ) -> dict:
        """Send a photo (``sendPhoto``). ``photo`` can be a URL or file_id."""
        data: Dict[str, Any] = {"chat_id": chat_id, "photo": photo}
        if caption:
            data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = reply_markup
        data.update(kwargs)
        return self._request("sendPhoto", data)

    def send_document(
        self,
        chat_id: int | str,
        document: str,
        caption: Optional[str] = None,
        parse_mode: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """Send a document (``sendDocument``). ``document`` can be a URL or file_id."""
        data: Dict[str, Any] = {"chat_id": chat_id, "document": document}
        if caption:
            data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode
        data.update(kwargs)
        return self._request("sendDocument", data)

    def send_video(
        self,
        chat_id: int | str,
        video: str,
        caption: Optional[str] = None,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[dict] = None,
        **kwargs,
    ) -> dict:
        """Send a video (``sendVideo``)."""
        data: Dict[str, Any] = {"chat_id": chat_id, "video": video}
        if caption:
            data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = reply_markup
        data.update(kwargs)
        return self._request("sendVideo", data)

    def send_audio(
        self,
        chat_id: int | str,
        audio: str,
        caption: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """Send an audio file (``sendAudio``)."""
        data: Dict[str, Any] = {"chat_id": chat_id, "audio": audio}
        if caption:
            data["caption"] = caption
        data.update(kwargs)
        return self._request("sendAudio", data)

    def send_voice(
        self,
        chat_id: int | str,
        voice: str,
        caption: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """Send a voice message (``sendVoice``)."""
        data: Dict[str, Any] = {"chat_id": chat_id, "voice": voice}
        if caption:
            data["caption"] = caption
        data.update(kwargs)
        return self._request("sendVoice", data)

    def send_location(
        self,
        chat_id: int | str,
        latitude: float,
        longitude: float,
        **kwargs,
    ) -> dict:
        """Send a location (``sendLocation``)."""
        data: Dict[str, Any] = {
            "chat_id": chat_id,
            "latitude": latitude,
            "longitude": longitude,
        }
        data.update(kwargs)
        return self._request("sendLocation", data)

    def send_contact(
        self,
        chat_id: int | str,
        phone_number: str,
        first_name: str,
        last_name: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """Send a contact card (``sendContact``)."""
        data: Dict[str, Any] = {
            "chat_id": chat_id,
            "phone_number": phone_number,
            "first_name": first_name,
        }
        if last_name:
            data["last_name"] = last_name
        data.update(kwargs)
        return self._request("sendContact", data)

    # ── Callback queries ──────────────────────────────────────────────────

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
        **kwargs,
    ) -> bool:
        """Acknowledge a callback query (``answerCallbackQuery``)."""
        data: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            data["text"] = text
        data["show_alert"] = show_alert
        data.update(kwargs)
        return self._request("answerCallbackQuery", data)

    # ── Message editing ───────────────────────────────────────────────────

    def edit_message_reply_markup(
        self,
        chat_id: int | str,
        message_id: int,
        reply_markup: Optional[dict] = None,
    ) -> dict:
        """Edit the reply markup of a message (``editMessageReplyMarkup``)."""
        data: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self._request("editMessageReplyMarkup", data)

    # ── Files ─────────────────────────────────────────────────────────────

    def get_file(self, file_id: str) -> dict:
        """Get file info for downloading (``getFile``)."""
        return self._request("getFile", {"file_id": file_id})

    def get_file_url(self, file_path: str) -> str:
        """Build the direct download URL from a ``file_path`` returned by ``getFile``."""
        return f"https://api.telegram.org/file/bot{self._token}/{file_path}"

    # ── Bot commands ──────────────────────────────────────────────────────

    def set_my_commands(self, commands: list[dict]) -> bool:
        """Set the bot's command menu (``setMyCommands``)."""
        return self._request("setMyCommands", {"commands": commands})
