from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Dict

import requests

from sms.constants import TWILIO_STATUS_MAP
from sms.providers.base import BaseSMSProvider, SMSDeliveryReport, SMSInboundMessage, SMSSendResult

logger = logging.getLogger(__name__)


class TwilioSMSProvider(BaseSMSProvider):
    PROVIDER_NAME = "twilio"
    API_BASE = "https://api.twilio.com/2010-04-01/Accounts"

    def send_sms(self, to: str, body: str, *, sender_id=None, dlt_template_id=None, **kwargs) -> SMSSendResult:
        account_sid = self.credentials.get("account_sid")
        auth_token = self.credentials.get("auth_token")
        if not account_sid or not auth_token:
            return SMSSendResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="Twilio credentials are missing account_sid or auth_token",
            )

        url = f"{self.API_BASE}/{account_sid}/Messages.json"
        data: Dict[str, str] = {
            "To": to,
            "Body": body,
            "StatusCallback": self.sms_app.dlr_webhook_url,
        }

        messaging_service_sid = self.credentials.get("messaging_service_sid")
        if messaging_service_sid:
            data["MessagingServiceSid"] = messaging_service_sid
        else:
            data["From"] = sender_id or self.sms_app.sender_id

        try:
            response = requests.post(
                url,
                data=data,
                auth=(account_sid, auth_token),
                timeout=30,
            )
            response_json = response.json()
        except Exception as exc:
            logger.exception("Twilio send failed")
            return SMSSendResult(success=False, provider=self.PROVIDER_NAME, error_message=str(exc))

        if response.status_code >= 400:
            return SMSSendResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_code=str(response_json.get("code", "")),
                error_message=response_json.get("message", "Twilio request failed"),
                raw_response=response_json,
            )

        return SMSSendResult(
            success=True,
            provider=self.PROVIDER_NAME,
            message_id=response_json.get("sid"),
            segment_count=int(response_json.get("num_segments", 1) or 1),
            cost=None,
            raw_response=response_json,
        )

    def parse_inbound_webhook(self, payload: dict) -> SMSInboundMessage:
        return SMSInboundMessage(
            from_number=payload.get("From", ""),
            to_number=payload.get("To", ""),
            body=payload.get("Body", ""),
            provider_message_id=payload.get("MessageSid", ""),
            raw_payload=payload,
        )

    def parse_dlr_webhook(self, payload: dict) -> SMSDeliveryReport:
        twilio_status = (payload.get("MessageStatus") or "").lower()
        return SMSDeliveryReport(
            provider_message_id=payload.get("MessageSid", ""),
            status=TWILIO_STATUS_MAP.get(twilio_status, "PENDING"),
            error_code=payload.get("ErrorCode"),
            error_message=payload.get("ErrorMessage"),
            raw_payload=payload,
        )

    def validate_webhook_signature(self, request) -> bool:
        """Validate Twilio webhook signature using account auth token."""
        signature = request.headers.get("X-Twilio-Signature", "")
        auth_token = self.credentials.get("auth_token", "")
        if not signature or not auth_token:
            return False

        url = request.build_absolute_uri()
        params = request.POST.dict() if hasattr(request, "POST") else {}

        expected = self._build_twilio_signature(url, params, auth_token)
        return hmac.compare_digest(signature, expected)

    @staticmethod
    def _build_twilio_signature(url: str, params: dict, token: str) -> str:
        s = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
        digest = hmac.new(token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1).digest()
        return base64.b64encode(digest).decode("utf-8")
