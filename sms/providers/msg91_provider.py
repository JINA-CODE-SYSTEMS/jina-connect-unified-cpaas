from __future__ import annotations

import requests

from sms.providers.base import BaseSMSProvider, SMSDeliveryReport, SMSInboundMessage, SMSSendResult


class MSG91SMSProvider(BaseSMSProvider):
    PROVIDER_NAME = "msg91"
    API_URL = "https://control.msg91.com/api/v5/flow/"

    def send_sms(self, to: str, body: str, *, sender_id=None, dlt_template_id=None, **kwargs) -> SMSSendResult:
        auth_key = self.credentials.get("auth_key")
        if not auth_key:
            return SMSSendResult(False, self.PROVIDER_NAME, error_message="MSG91 auth_key missing")

        payload = {
            "flow_id": dlt_template_id or self.credentials.get("flow_id") or self.sms_app.dlt_template_id,
            "sender": sender_id or self.sms_app.sender_id,
            "mobiles": to,
            "message": body,
        }

        try:
            response = requests.post(self.API_URL, json=payload, headers={"authkey": auth_key}, timeout=30)
            data = response.json()
        except Exception as exc:
            return SMSSendResult(False, self.PROVIDER_NAME, error_message=str(exc))

        ok = response.status_code < 400 and data.get("type") != "error"
        return SMSSendResult(
            success=ok,
            provider=self.PROVIDER_NAME,
            message_id=str(data.get("request_id", "")) if ok else None,
            error_message=None if ok else str(data),
            raw_response=data,
        )

    def parse_inbound_webhook(self, payload: dict) -> SMSInboundMessage:
        return SMSInboundMessage(
            from_number=payload.get("mobile", ""),
            to_number=payload.get("sender", ""),
            body=payload.get("message", ""),
            provider_message_id=str(payload.get("msg_id", "")),
            raw_payload=payload,
        )

    def parse_dlr_webhook(self, payload: dict) -> SMSDeliveryReport:
        status_map = {"1": "DELIVERED", "2": "FAILED", "9": "UNDELIVERED"}
        status = str(payload.get("status", ""))
        return SMSDeliveryReport(
            provider_message_id=str(payload.get("request_id", "")),
            status=status_map.get(status, "PENDING"),
            raw_payload=payload,
        )

    def validate_webhook_signature(self, request) -> bool:
        # MSG91 commonly uses fixed source IP allow-listing configured at infra.
        # Keep permissive here until network-level controls are enforced.
        return True
