"""
High-level Telegram message sender with logging, model persistence, and inbox
integration.

Implements the BaseChannelAdapter interface so it can be returned by the
channel registry.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.utils import timezone

from telegram.constants import TELEGRAM_ERROR_MAP
from wa.adapters.channel_base import BaseChannelAdapter

logger = logging.getLogger(__name__)


class TelegramMessageSender(BaseChannelAdapter):
    """
    High-level message sending with logging, persistence, and rate limiting.

    Registered in the channel registry as the TELEGRAM adapter factory output.
    """

    def __init__(self, bot_app):
        from telegram.services.bot_client import TelegramBotClient

        self.bot_app = bot_app
        self.client = TelegramBotClient(token=bot_app.bot_token)

    # ── BaseChannelAdapter interface ──────────────────────────────────────

    def get_channel_name(self) -> str:
        return "TELEGRAM"

    def _check_rate_limit(self) -> Dict[str, Any] | None:
        """Return an error dict if rate-limited, else None."""
        from telegram.services.rate_limiter import check_rate_limit

        if not check_rate_limit(str(self.bot_app.pk)):
            return {"success": False, "message_id": "", "error": "Rate limit exceeded for this bot."}
        return None

    def send_text(self, chat_id: str, text: str, **kwargs: Any) -> Dict[str, Any]:
        """Send a plain text message and persist the outbound record."""
        if err := self._check_rate_limit():
            return err

        reply_markup = kwargs.get("reply_markup")
        parse_mode = kwargs.get("parse_mode")

        try:
            result = self.client.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            outbound = self._persist_outbound(
                chat_id=int(chat_id),
                message_type="TEXT",
                request_payload={"text": text},
                result=result,
                contact=kwargs.get("contact"),
            )
            return {
                "success": True,
                "message_id": str(result.get("message_id", "")),
                "outbound_id": str(outbound.pk) if outbound else None,
            }
        except Exception as exc:
            return self._handle_send_error(exc, int(chat_id), "TEXT", kwargs.get("contact"))

    def send_media(
        self,
        chat_id: str,
        media_type: str,
        media_url: str,
        caption: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Send a media message (photo, document, video, audio, voice)."""
        if err := self._check_rate_limit():
            return err

        # Validate URL scheme
        from urllib.parse import urlparse

        parsed = urlparse(media_url)
        if parsed.scheme not in ("https", "http"):
            return {"success": False, "message_id": "", "error": f"Invalid media URL scheme: {parsed.scheme!r}"}

        method_map = {
            "image": self.client.send_photo,
            "photo": self.client.send_photo,
            "document": self.client.send_document,
            "video": self.client.send_video,
            "audio": self.client.send_audio,
            "voice": self.client.send_voice,
        }
        send_fn = method_map.get(media_type.lower())
        if not send_fn:
            return {"success": False, "message_id": "", "error": f"Unsupported media type: {media_type}"}

        # Build positional arg name from media type
        media_key = {
            "image": "photo",
            "photo": "photo",
            "document": "document",
            "video": "video",
            "audio": "audio",
            "voice": "voice",
        }[media_type.lower()]

        try:
            send_kwargs = {"chat_id": int(chat_id), media_key: media_url}
            if caption:
                send_kwargs["caption"] = caption
            result = send_fn(**send_kwargs)
            outbound = self._persist_outbound(
                chat_id=int(chat_id),
                message_type=media_type.upper(),
                request_payload={media_key: media_url, "caption": caption},
                result=result,
                contact=kwargs.get("contact"),
            )
            return {
                "success": True,
                "message_id": str(result.get("message_id", "")),
                "outbound_id": str(outbound.pk) if outbound else None,
            }
        except Exception as exc:
            return self._handle_send_error(exc, int(chat_id), media_type.upper(), kwargs.get("contact"))

    def send_keyboard(
        self,
        chat_id: str,
        text: str,
        keyboard: list,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Send a text message with an inline keyboard."""
        if err := self._check_rate_limit():
            return err

        from telegram.services.keyboard_builder import build_inline_keyboard

        reply_markup = build_inline_keyboard(keyboard)
        try:
            result = self.client.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=reply_markup,
                parse_mode=kwargs.get("parse_mode"),
            )
            outbound = self._persist_outbound(
                chat_id=int(chat_id),
                message_type="TEXT",
                request_payload={"text": text, "reply_markup": reply_markup},
                result=result,
                contact=kwargs.get("contact"),
            )
            return {
                "success": True,
                "message_id": str(result.get("message_id", "")),
                "outbound_id": str(outbound.pk) if outbound else None,
            }
        except Exception as exc:
            return self._handle_send_error(exc, int(chat_id), "TEXT", kwargs.get("contact"))

    # ── Convenience methods ───────────────────────────────────────────────

    def send_location(self, chat_id: str | int, latitude: float, longitude: float, contact=None) -> Dict[str, Any]:
        if err := self._check_rate_limit():
            return err
        try:
            result = self.client.send_location(chat_id=int(chat_id), latitude=latitude, longitude=longitude)
            outbound = self._persist_outbound(
                chat_id=int(chat_id),
                message_type="LOCATION",
                request_payload={"latitude": latitude, "longitude": longitude},
                result=result,
                contact=contact,
            )
            return {
                "success": True,
                "message_id": str(result.get("message_id", "")),
                "outbound_id": str(outbound.pk) if outbound else None,
            }
        except Exception as exc:
            return self._handle_send_error(exc, int(chat_id), "LOCATION", contact)

    def send_contact_card(
        self, chat_id: str | int, phone_number: str, first_name: str, last_name: str = None, contact=None
    ) -> Dict[str, Any]:
        if err := self._check_rate_limit():
            return err
        try:
            result = self.client.send_contact(
                chat_id=int(chat_id),
                phone_number=phone_number,
                first_name=first_name,
                last_name=last_name,
            )
            outbound = self._persist_outbound(
                chat_id=int(chat_id),
                message_type="CONTACT",
                request_payload={"phone_number": phone_number, "first_name": first_name},
                result=result,
                contact=contact,
            )
            return {
                "success": True,
                "message_id": str(result.get("message_id", "")),
                "outbound_id": str(outbound.pk) if outbound else None,
            }
        except Exception as exc:
            return self._handle_send_error(exc, int(chat_id), "CONTACT", contact)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _persist_outbound(self, *, chat_id, message_type, request_payload, result, contact=None):
        """Create a TelegramOutboundMessage record."""
        from telegram.models import TelegramOutboundMessage

        try:
            return TelegramOutboundMessage.objects.create(
                tenant=self.bot_app.tenant,
                bot_app=self.bot_app,
                contact=contact,
                chat_id=chat_id,
                message_type=message_type,
                request_payload=request_payload,
                provider_message_id=result.get("message_id"),
                status="SENT",
                sent_at=timezone.now(),
            )
        except Exception:
            logger.exception(
                "[TelegramMessageSender] Failed to persist outbound record for chat_id=%s",
                chat_id,
            )
            return None

    def _handle_send_error(self, exc, chat_id, message_type, contact=None):
        """Log error and persist a FAILED outbound record."""
        from telegram.services.bot_client import TelegramAPIError

        status = "FAILED"
        error_msg = str(exc)

        if isinstance(exc, TelegramAPIError):
            status = TELEGRAM_ERROR_MAP.get(exc.status_code, "FAILED")
            error_msg = exc.description

        logger.exception(
            "[TelegramMessageSender] %s sending %s to chat_id=%s (token=%s): %s",
            status,
            message_type,
            chat_id,
            self.bot_app.masked_token,
            error_msg,
        )

        # Persist failed outbound
        from telegram.models import TelegramOutboundMessage

        try:
            TelegramOutboundMessage.objects.create(
                tenant=self.bot_app.tenant,
                bot_app=self.bot_app,
                contact=contact,
                chat_id=chat_id,
                message_type=message_type,
                status=status,
                failed_at=timezone.now(),
                error_message=error_msg[:2000],
            )
        except Exception:
            logger.exception("[TelegramMessageSender] Failed to persist error outbound record")

        return {"success": False, "message_id": "", "error": error_msg}
