from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SMSSendResult:
    success: bool
    provider: str
    message_id: Optional[str] = None
    segment_count: int = 1
    cost: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class SMSInboundMessage:
    from_number: str
    to_number: str
    body: str
    provider_message_id: str
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass
class SMSDeliveryReport:
    provider_message_id: str
    status: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None


class BaseSMSProvider(ABC):
    PROVIDER_NAME: str = "base"

    def __init__(self, sms_app):
        self.sms_app = sms_app
        raw = sms_app.provider_credentials or "{}"
        if isinstance(raw, str):
            try:
                self.credentials = json.loads(raw)
            except (ValueError, TypeError):
                self.credentials = {}
        else:
            # Support dict values from tests (SimpleNamespace) and legacy code
            self.credentials = raw or {}

    @abstractmethod
    def send_sms(
        self,
        to: str,
        body: str,
        *,
        sender_id: Optional[str] = None,
        dlt_template_id: Optional[str] = None,
        **kwargs,
    ) -> SMSSendResult:
        pass

    @abstractmethod
    def parse_inbound_webhook(self, payload: dict) -> SMSInboundMessage:
        pass

    @abstractmethod
    def parse_dlr_webhook(self, payload: dict) -> SMSDeliveryReport:
        pass

    @abstractmethod
    def validate_webhook_signature(self, request) -> bool:
        pass
