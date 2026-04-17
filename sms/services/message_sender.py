"""High-level SMS sender implementing BaseChannelAdapter."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.utils import timezone

from sms.providers import get_sms_provider
from sms.services.rate_limiter import check_rate_limit
from wa.adapters.channel_base import BaseChannelAdapter

logger = logging.getLogger(__name__)


class SMSMessageSender(BaseChannelAdapter):
    def __init__(self, sms_app):
        self.sms_app = sms_app
        self.provider = get_sms_provider(sms_app)

    def get_channel_name(self) -> str:
        return "SMS"

    def send_text(self, chat_id: str, text: str, **kwargs: Any) -> Dict[str, Any]:
        if not check_rate_limit(str(self.sms_app.pk)):
            return {"success": False, "message_id": "", "error": "Rate limit exceeded for SMS app"}

        if not self.sms_app.increment_daily_counter():
            return {"success": False, "message_id": "", "error": "Daily SMS limit reached"}

        result = self.provider.send_sms(
            to=chat_id,
            body=text,
            sender_id=kwargs.get("sender_id") or self.sms_app.sender_id,
            dlt_template_id=kwargs.get("dlt_template_id"),
        )

        # Provider failover (#104): on send failure, try fallback_app if configured
        used_app = self.sms_app
        if not result.success and self.sms_app.fallback_app_id:
            fallback = self.sms_app.fallback_app
            if fallback and fallback.is_active:
                logger.warning(
                    "[SMSMessageSender] Primary send failed for app %s, trying fallback %s",
                    self.sms_app.pk,
                    fallback.pk,
                )
                fallback_provider = get_sms_provider(fallback)
                result = fallback_provider.send_sms(
                    to=chat_id,
                    body=text,
                    sender_id=kwargs.get("sender_id") or fallback.sender_id,
                    dlt_template_id=kwargs.get("dlt_template_id"),
                )
                used_app = fallback

        outbound = self._persist_outbound(
            chat_id=chat_id,
            text=text,
            result=result,
            provider_used=used_app.provider,
            **kwargs,
        )
        return {
            "success": result.success,
            "message_id": result.message_id or "",
            "outbound_id": str(outbound.pk) if outbound else None,
            "segments": result.segment_count,
            "error": result.error_message,
        }

    def send_media(
        self,
        chat_id: str,
        media_type: str,
        media_url: str,
        caption: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        text = (caption or "").strip()
        if media_url:
            text = f"{text}\n{media_url}".strip()
        return self.send_text(chat_id=chat_id, text=text, **kwargs)

    def send_keyboard(self, chat_id: str, text: str, keyboard: list, **kwargs: Any) -> Dict[str, Any]:
        numbered = [text, ""]
        for idx, item in enumerate(keyboard or [], start=1):
            label = item.get("text") or item.get("label") or item.get("title") or "Option"
            numbered.append(f"{idx}. {label}")
        return self.send_text(chat_id=chat_id, text="\n".join(numbered).strip(), **kwargs)

    def _persist_outbound(self, *, chat_id: str, text: str, result, provider_used: str = "", **kwargs):
        from sms.models import SMSOutboundMessage

        contact = kwargs.get("contact")
        outbound = SMSOutboundMessage.objects.create(
            tenant=self.sms_app.tenant,
            sms_app=self.sms_app,
            contact=contact,
            to_number=chat_id,
            from_number=self.sms_app.sender_id,
            message_text=text,
            segment_count=result.segment_count,
            provider_message_id=result.message_id or "",
            status="SENT" if result.success else "FAILED",
            cost=result.cost or self.sms_app.price_per_sms,
            request_payload={"to": chat_id, "body": text},
            response_payload=result.raw_response or {},
            error_code=result.error_code or "",
            error_message=result.error_message or "",
            sent_at=timezone.now() if result.success else None,
            failed_at=timezone.now() if not result.success else None,
            broadcast_message=kwargs.get("broadcast_message"),
            provider_used=provider_used or self.sms_app.provider,
        )

        if contact and kwargs.get("create_inbox_entry", True):
            try:
                from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices
                from team_inbox.utils.inbox_message_factory import create_inbox_message

                inbox_message = create_inbox_message(
                    tenant=self.sms_app.tenant,
                    contact=contact,
                    platform=MessagePlatformChoices.SMS,
                    direction=MessageDirectionChoices.OUTGOING,
                    author=kwargs.get("author", AuthorChoices.USER),
                    content={"type": "text", "body": {"text": text}},
                    external_message_id=str(outbound.id),
                    tenant_user=kwargs.get("tenant_user"),
                    is_read=True,
                )
                outbound.inbox_message = inbox_message
                outbound.save(update_fields=["inbox_message"])
            except Exception:
                logger.exception("Failed to create SMS outbound inbox message")

        return outbound
