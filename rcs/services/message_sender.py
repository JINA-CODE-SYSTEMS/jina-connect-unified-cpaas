"""High-level RCS sender implementing BaseChannelAdapter with SMS fallback."""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, Optional

from django.db.models import F
from django.utils import timezone

from rcs.providers import get_rcs_provider
from rcs.services.capability_checker import RCSCapabilityChecker
from rcs.services.rate_limiter import check_rate_limit
from rcs.services.rich_card_builder import RichCardBuilder
from rcs.services.suggestion_builder import SuggestionBuilder
from wa.adapters.channel_base import BaseChannelAdapter

logger = logging.getLogger(__name__)


class RCSMessageSender(BaseChannelAdapter):
    """Implements BaseChannelAdapter for RCS channel with SMS fallback."""

    def __init__(self, rcs_app):
        self.rcs_app = rcs_app
        self.provider = get_rcs_provider(rcs_app)
        self.capability_checker = RCSCapabilityChecker(self.provider)

    def get_channel_name(self) -> str:
        return "RCS"

    def send_text(self, chat_id: str, text: str, **kwargs: Any) -> Dict[str, Any]:
        """Send plain text RCS message with optional suggestions."""
        content_message: Dict[str, Any] = {"text": text[:3072]}
        suggestions = kwargs.get("suggestions")
        if suggestions:
            content_message["suggestions"] = suggestions[:11]
        return self._send_with_fallback(chat_id, content_message, "TEXT", **kwargs)

    def send_media(
        self,
        chat_id: str,
        media_type: str,
        media_url: str,
        caption: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Send media as a rich card with optional caption."""
        content_message = RichCardBuilder.standalone_card(
            title=caption,
            media_url=media_url,
            media_height=kwargs.get("media_height", "MEDIUM"),
            thumbnail_url=kwargs.get("thumbnail_url"),
            suggestions=kwargs.get("suggestions"),
        )
        return self._send_with_fallback(chat_id, content_message, "RICH_CARD", **kwargs)

    def send_keyboard(self, chat_id: str, text: str, keyboard: list, **kwargs: Any) -> Dict[str, Any]:
        """Send text with suggested replies/actions."""
        suggestions = SuggestionBuilder.from_channel_agnostic_keyboard(keyboard)
        content_message: Dict[str, Any] = {"text": text[:3072], "suggestions": suggestions}
        return self._send_with_fallback(chat_id, content_message, "TEXT", **kwargs)

    def send_rich_card(
        self,
        chat_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        media_url: Optional[str] = None,
        suggestions: Optional[list] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """RCS-specific: send a standalone rich card."""
        content_message = RichCardBuilder.standalone_card(
            title=title,
            description=description,
            media_url=media_url,
            suggestions=suggestions,
            media_height=kwargs.get("media_height", "MEDIUM"),
            thumbnail_url=kwargs.get("thumbnail_url"),
        )
        return self._send_with_fallback(chat_id, content_message, "RICH_CARD", **kwargs)

    def send_carousel(self, chat_id: str, cards: list, card_width: str = "MEDIUM", **kwargs: Any) -> Dict[str, Any]:
        """RCS-specific: send a carousel of rich cards."""
        content_message = RichCardBuilder.carousel(cards, card_width)
        chip_suggestions = kwargs.get("suggestions")
        if chip_suggestions:
            content_message["suggestions"] = chip_suggestions[:11]
        return self._send_with_fallback(chat_id, content_message, "CAROUSEL", **kwargs)

    # ── Core send logic ──────────────────────────────────────────────────

    def _send_with_fallback(self, chat_id, content_message, message_type, **kwargs):
        """Send via RCS; fallback to SMS if user not capable."""
        phone = chat_id  # RCS uses E.164 phone as chat_id

        # Check capability before sending
        if kwargs.get("check_capability", True):
            capability = self.capability_checker.get_capability(phone)
            if not capability.is_rcs_enabled:
                return self._sms_fallback(phone, content_message, message_type, **kwargs)

            # iOS-aware rendering adjustment
            device_os = self._detect_device_os(capability)
            if device_os == "ios":
                content_message = self._adjust_for_ios(content_message)

        # Check rate limit
        if not check_rate_limit(str(self.rcs_app.pk)):
            return {"success": False, "error": "Rate limited", "channel": "RCS"}

        # Atomic daily limit gate — must pass BEFORE sending
        if not self.rcs_app.increment_daily_counter():
            return {"success": False, "error": "Daily limit reached", "channel": "RCS"}

        # Send via RCS
        result = self.provider.send_message(
            to_phone=phone,
            content_message=content_message,
            traffic_type=kwargs.get("traffic_type", "TRANSACTION"),
        )

        # If user not RCS-capable (404), decrement counter and fallback to SMS
        if not result.success and not result.is_rcs_capable:
            self.capability_checker.invalidate(self.rcs_app.agent_id, phone)
            self._decrement_daily_counter()
            return self._sms_fallback(phone, content_message, message_type, **kwargs)

        # Persist outbound
        outbound = self._persist_outbound(phone, content_message, message_type, result, **kwargs)

        return {
            "success": result.success,
            "message_id": result.message_id,
            "outbound_id": str(outbound.pk) if outbound else None,
            "channel": "RCS",
            "error": result.error_message,
        }

    # ── SMS Fallback ─────────────────────────────────────────────────────

    def _sms_fallback(self, phone, content_message, message_type, **kwargs):
        """Fall back to SMS channel when user is not RCS-capable."""
        if not self.rcs_app.sms_fallback_enabled or not self.rcs_app.sms_fallback_app:
            return {
                "success": False,
                "error": "User not RCS-capable and no SMS fallback configured",
                "channel": "RCS",
            }

        from jina_connect.channel_registry import get_channel_adapter

        try:
            sms_adapter = get_channel_adapter("SMS", self.rcs_app.tenant)
        except (ValueError, NotImplementedError):
            return {"success": False, "error": "SMS fallback adapter not available", "channel": "RCS"}

        text = self._extract_text_from_content(content_message)
        result = sms_adapter.send_text(phone, text, **kwargs)
        result["channel"] = "SMS_FALLBACK"
        result["original_channel"] = "RCS"
        return result

    @staticmethod
    def _extract_text_from_content(content_message):
        """Extract plain text from RCS content for SMS fallback."""
        if "text" in content_message:
            return content_message["text"]

        parts = []
        rich_card = content_message.get("richCard", {})

        standalone = rich_card.get("standaloneCard", {})
        card_content = standalone.get("cardContent", {})
        if card_content.get("title"):
            parts.append(card_content["title"])
        if card_content.get("description"):
            parts.append(card_content["description"])
        media = card_content.get("media", {}).get("contentInfo", {}).get("fileUrl")
        if media:
            parts.append(f"[Media] {media}")

        carousel = rich_card.get("carouselCard", {})
        card_contents = carousel.get("cardContents", [])
        for idx, card in enumerate(card_contents, 1):
            if card.get("title"):
                parts.append(f"Card {idx}: {card['title']}")
            if card.get("description"):
                parts.append(card["description"])

        return "\n".join(parts) if parts else "Message from RCS"

    # ── Persistence ──────────────────────────────────────────────────────

    def _persist_outbound(self, phone, content_message, message_type, result, **kwargs):
        """Create RCSOutboundMessage + optional inbox timeline entry."""
        from rcs.models import RCSOutboundMessage

        contact = kwargs.get("contact")
        try:
            outbound = RCSOutboundMessage.objects.create(
                tenant=self.rcs_app.tenant,
                rcs_app=self.rcs_app,
                contact=contact,
                to_phone=phone,
                message_type=message_type,
                message_content=content_message,
                suggestions=content_message.get("suggestions", []),
                provider_message_id=result.message_id or "",
                status="SENT" if result.success else "FAILED",
                cost=result.cost or self.rcs_app.price_per_message,
                traffic_type=kwargs.get("traffic_type", "TRANSACTION"),
                request_payload=content_message,
                response_payload=result.raw_response or {},
                error_code=result.error_code or "",
                error_message=result.error_message or "",
                sent_at=timezone.now() if result.success else None,
                failed_at=timezone.now() if not result.success else None,
                broadcast_message=kwargs.get("broadcast_message"),
            )

            if contact and kwargs.get("create_inbox_entry", True):
                try:
                    from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices
                    from team_inbox.utils.inbox_message_factory import create_inbox_message

                    inbox_msg = create_inbox_message(
                        tenant=self.rcs_app.tenant,
                        contact=contact,
                        platform=MessagePlatformChoices.RCS,
                        direction=MessageDirectionChoices.OUTGOING,
                        author=kwargs.get("author", AuthorChoices.USER),
                        content=self._to_inbox_content(content_message, message_type),
                        external_message_id=str(outbound.pk),
                        tenant_user=kwargs.get("tenant_user"),
                        is_read=True,
                    )
                    outbound.inbox_message = inbox_msg
                    outbound.save(update_fields=["inbox_message"])
                except Exception:
                    logger.exception("Failed to create RCS outbound inbox message")

            return outbound
        except Exception:
            logger.exception("Failed to persist RCS outbound message")
            return None

    @staticmethod
    def _to_inbox_content(content_message, message_type):
        """Convert RCS content to team inbox content format."""
        if message_type == "TEXT":
            return {"type": "text", "body": {"text": content_message.get("text", "")}}
        if message_type in ("RICH_CARD", "CAROUSEL"):
            return {"type": "rcs_rich_card", "body": content_message}
        return {"type": "text", "body": {"text": str(content_message)}}

    # ── Daily Counter Management ─────────────────────────────────────────

    def _decrement_daily_counter(self):
        """Decrement daily counter when an RCS send gets a 404 and falls back to SMS."""
        from rcs.models import RCSApp

        RCSApp.objects.filter(
            pk=self.rcs_app.pk,
            messages_sent_today__gt=0,
        ).update(messages_sent_today=F("messages_sent_today") - 1)

    # ── iOS-Aware Rendering ──────────────────────────────────────────────

    @staticmethod
    def _detect_device_os(capability) -> str:
        """Detect device OS from capability features."""
        features = capability.features if capability else []
        android_only = {"ACTION_CREATE_CALENDAR_EVENT", "ACTION_DIAL"}
        feature_set = set(features)
        if feature_set and not feature_set.intersection(android_only):
            return "ios"
        return "android"

    @staticmethod
    def _adjust_for_ios(content_message):
        """Adjust RCS payload for optimal iPhone rendering."""
        msg = copy.deepcopy(content_message)

        rich_card = msg.get("richCard", {})
        standalone = rich_card.get("standaloneCard", {})
        card_content = standalone.get("cardContent", {})
        if "media" in card_content:
            card_content["media"]["height"] = "MEDIUM"

        carousel = rich_card.get("carouselCard", {})
        for card in carousel.get("cardContents", []):
            if "media" in card:
                card["media"]["height"] = "MEDIUM"
            for s in card.get("suggestions", []):
                if "reply" in s:
                    s["reply"]["text"] = s["reply"]["text"][:20]
                if "action" in s:
                    s["action"]["text"] = s["action"]["text"][:20]

        for s in msg.get("suggestions", []):
            if "reply" in s:
                s["reply"]["text"] = s["reply"]["text"][:20]
            if "action" in s:
                s["action"]["text"] = s["action"]["text"][:20]

        return msg
