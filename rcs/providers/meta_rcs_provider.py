"""Meta RCS provider implementation (WhatsApp-style API)."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, List, Optional

import requests

from rcs.providers.base import (
    BaseRCSProvider,
    RCSCapability,
    RCSEventReport,
    RCSInboundMessage,
    RCSSendResult,
)

logger = logging.getLogger(__name__)


class MetaRCSProvider(BaseRCSProvider):
    PROVIDER_NAME = "META_RCS"
    API_BASE = "https://graph.facebook.com/v21.0"

    def _get_phone_number_id(self) -> str:
        return self.credentials.get("phone_number_id", "")

    def _get_headers(self) -> dict:
        token = self.credentials.get("access_token", "")
        return {"Authorization": f"Bearer {token}"}

    def send_message(
        self,
        to_phone: str,
        content_message: Dict[str, Any],
        *,
        message_id: Optional[str] = None,
        traffic_type: str = "TRANSACTION",
        ttl: Optional[str] = None,
        **kwargs,
    ) -> RCSSendResult:
        phone_id = self._get_phone_number_id()
        url = f"{self.API_BASE}/{phone_id}/messages"

        body = self._convert_to_meta_format(to_phone, content_message, traffic_type)

        try:
            resp = requests.post(url, json=body, headers=self._get_headers(), timeout=30)
        except requests.RequestException as exc:
            logger.exception("Meta RCS send failed: %s", exc)
            return RCSSendResult(success=False, provider=self.PROVIDER_NAME, error_message=str(exc))

        if resp.status_code >= 400:
            data = resp.json() if resp.text else {}
            error = data.get("error", {})
            # Error code 131047 = recipient not RCS-capable
            is_capable = error.get("code") != 131047
            return RCSSendResult(
                success=False,
                provider=self.PROVIDER_NAME,
                is_rcs_capable=is_capable,
                error_code=str(error.get("code", resp.status_code)),
                error_message=error.get("message", resp.text),
                raw_response=data,
            )

        data = resp.json()
        msg_id = data.get("messages", [{}])[0].get("id", "")
        return RCSSendResult(success=True, provider=self.PROVIDER_NAME, message_id=msg_id, raw_response=data)

    def _convert_to_meta_format(self, to_phone, content_message, traffic_type):
        """Convert unified RCS content format to Meta's WhatsApp-style API format."""
        body: Dict[str, Any] = {
            "messaging_product": "rcs",
            "to": to_phone,
        }

        if "text" in content_message and "richCard" not in content_message:
            body["type"] = "text"
            body["text"] = {"body": content_message["text"]}
        elif "richCard" in content_message:
            body["type"] = "interactive"
            body["interactive"] = self._rich_card_to_interactive(content_message["richCard"])
        else:
            body["type"] = "text"
            body["text"] = {"body": str(content_message)}

        return body

    def _rich_card_to_interactive(self, rich_card):
        """Convert Google-style richCard to Meta interactive format."""
        interactive: Dict[str, Any] = {"type": "button"}

        if "standaloneCard" in rich_card:
            card = rich_card["standaloneCard"].get("cardContent", {})
            if card.get("title"):
                interactive["header"] = {"type": "text", "text": card["title"]}
            if card.get("description"):
                interactive["body"] = {"text": card["description"]}
            if card.get("media"):
                media_url = card["media"].get("contentInfo", {}).get("fileUrl", "")
                if media_url:
                    interactive["header"] = {"type": "image", "image": {"link": media_url}}
            if card.get("suggestions"):
                interactive["action"] = {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": s.get("reply", {}).get("postbackData", ""),
                                "title": s.get("reply", {}).get("text", ""),
                            },
                        }
                        for s in card["suggestions"]
                        if "reply" in s
                    ][:3]  # Meta limit: 3 buttons per interactive
                }

        return interactive

    def send_event(
        self,
        to_phone: str,
        event_type: str,
        *,
        message_id: Optional[str] = None,
    ) -> RCSSendResult:
        # Meta does not expose a separate event endpoint for typing/read
        return RCSSendResult(
            success=False,
            provider=self.PROVIDER_NAME,
            error_message="Meta RCS does not support agent events",
        )

    def revoke_message(self, to_phone: str, message_id: str) -> RCSSendResult:
        return RCSSendResult(
            success=False,
            provider=self.PROVIDER_NAME,
            error_message="Meta RCS does not support message revocation",
        )

    def check_capability(self, phone: str) -> RCSCapability:
        # Meta handles RCS discovery internally — no separate API
        return RCSCapability(phone=phone, is_rcs_enabled=True)

    def batch_check_capability(self, phones: List[str]) -> Dict[str, RCSCapability]:
        return {p: RCSCapability(phone=p, is_rcs_enabled=True) for p in phones}

    def upload_file(self, file_url: str, thumbnail_url: Optional[str] = None) -> Dict[str, str]:
        phone_id = self._get_phone_number_id()
        url = f"{self.API_BASE}/{phone_id}/media"

        try:
            resp = requests.post(
                url,
                json={"messaging_product": "rcs", "url": file_url, "type": "image/jpeg"},
                headers=self._get_headers(),
                timeout=30,
            )
            if resp.status_code >= 400:
                return {}
            data = resp.json()
            return {"fileName": data.get("id", "")}
        except requests.RequestException:
            logger.exception("Meta RCS file upload failed")
            return {}

    def parse_inbound_webhook(self, payload: Dict) -> RCSInboundMessage:
        entry = (payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value", {})
        msg = (value.get("messages") or [{}])[0]

        msg_type = msg.get("type", "text")
        text = None
        postback = None
        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
        elif msg_type == "interactive":
            ir = msg.get("interactive", {})
            if "button_reply" in ir:
                postback = ir["button_reply"].get("id", "")
                text = ir["button_reply"].get("title", "")
                msg_type = "suggestion_response"

        return RCSInboundMessage(
            sender_phone=msg.get("from", ""),
            message_id=msg.get("id", ""),
            message_type=msg_type,
            text=text,
            postback_data=postback,
            raw_payload=payload,
        )

    def parse_event_webhook(self, payload: Dict) -> RCSEventReport:
        entry = (payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value", {})
        status = (value.get("statuses") or [{}])[0]

        status_map = {"delivered": "DELIVERED", "read": "READ", "sent": "SENT"}
        return RCSEventReport(
            sender_phone=status.get("recipient_id", ""),
            message_id=status.get("id", ""),
            event_type=status_map.get(status.get("status", ""), "UNKNOWN"),
            raw_payload=payload,
        )

    def validate_webhook_signature(self, request) -> bool:
        """Validate Meta webhook using X-Hub-Signature-256 (same as WhatsApp)."""
        app_secret = self.credentials.get("app_secret", "")
        if not app_secret:
            return False
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not signature.startswith("sha256="):
            return False
        expected = (
            "sha256="
            + hmac.new(
                app_secret.encode("utf-8"),
                request.body,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(signature, expected)
