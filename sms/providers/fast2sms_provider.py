from __future__ import annotations

import requests

from sms.providers.base import BaseSMSProvider, SMSDeliveryReport, SMSInboundMessage, SMSSendResult


class Fast2SMSProvider(BaseSMSProvider):
    PROVIDER_NAME = "fast2sms"
    API_URL = "https://www.fast2sms.com/dev/bulkV2"

    def send_sms(self, to: str, body: str, *, sender_id=None, dlt_template_id=None, **kwargs) -> SMSSendResult:
        api_key = self.credentials.get("api_key")
        if not api_key:
            return SMSSendResult(False, self.PROVIDER_NAME, error_message="Fast2SMS api_key missing")

        payload = {
            "route": self.credentials.get("route", "dlt"),
            "sender_id": sender_id or self.sms_app.sender_id,
            "message": body,
            "numbers": to,
        }

        try:
            response = requests.post(
                self.API_URL,
                json=payload,
                headers={"authorization": api_key, "Content-Type": "application/json"},
                timeout=30,
            )
            data = response.json()
        except Exception as exc:
            return SMSSendResult(False, self.PROVIDER_NAME, error_message=str(exc))

        ok = bool(data.get("return")) and response.status_code < 400
        return SMSSendResult(
            success=ok,
            provider=self.PROVIDER_NAME,
            message_id=str(data.get("request_id", "")) if ok else None,
            error_message=None if ok else str(data),
            raw_response=data,
        )

    def parse_inbound_webhook(self, payload: dict) -> SMSInboundMessage:
        # Fast2SMS is usually outbound-only. Keep a generic parser for compatibility.
        return SMSInboundMessage(
            from_number=str(payload.get("from", "")),
            to_number=str(payload.get("to", "")),
            body=str(payload.get("message", "")),
            provider_message_id=str(payload.get("message_id", "")),
            raw_payload=payload,
        )

    def parse_dlr_webhook(self, payload: dict) -> SMSDeliveryReport:
        status = str(payload.get("status", "")).upper()
        status_map = {
            "DELIVERED": "DELIVERED",
            "SENT": "SENT",
            "FAILED": "FAILED",
            "UNDELIVERED": "UNDELIVERED",
        }
        return SMSDeliveryReport(
            provider_message_id=str(payload.get("request_id", "")),
            status=status_map.get(status, "PENDING"),
            raw_payload=payload,
        )

    def validate_webhook_signature(self, request) -> bool:
        secret = self.credentials.get("webhook_secret")
        if not secret:
            return False
        return request.headers.get("X-Webhook-Secret", "") == secret
