"""Base RCS provider and shared data classes."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RCSSendResult:
    """Uniform result from all RCS send operations."""

    success: bool
    provider: str
    message_id: Optional[str] = None
    status: str = "SENT"
    cost: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    is_rcs_capable: bool = True  # False triggers SMS fallback


@dataclass
class RCSInboundMessage:
    """Normalized inbound message from any RCS provider."""

    sender_phone: str  # E.164
    message_id: str
    message_type: str  # "text", "suggestion_response", "location", "file"
    text: Optional[str] = None
    postback_data: Optional[str] = None
    suggestion_text: Optional[str] = None
    location: Optional[Dict] = None
    file_info: Optional[Dict] = None
    raw_payload: Optional[Dict] = None


@dataclass
class RCSEventReport:
    """Normalized delivery/read event from any RCS provider."""

    sender_phone: str
    message_id: str  # The agent message ID this event refers to
    event_type: str  # DELIVERED, READ, IS_TYPING
    event_id: Optional[str] = None
    raw_payload: Optional[Dict] = None


@dataclass
class RCSCapability:
    """Result of a capability check."""

    phone: str
    is_rcs_enabled: bool
    features: List[str] = field(default_factory=list)
    raw_response: Optional[Dict] = None


class BaseRCSProvider(ABC):
    """Abstract base for all RCS providers."""

    PROVIDER_NAME: str = "base"

    def __init__(self, rcs_app):
        self.rcs_app = rcs_app
        raw = rcs_app.provider_credentials or "{}"
        if isinstance(raw, str):
            try:
                self.credentials = json.loads(raw)
            except (ValueError, TypeError):
                self.credentials = {}
        else:
            self.credentials = raw or {}

    @abstractmethod
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
        """Send an AgentContentMessage to a user."""

    @abstractmethod
    def send_event(
        self,
        to_phone: str,
        event_type: str,
        *,
        message_id: Optional[str] = None,
    ) -> RCSSendResult:
        """Send an agent event (IS_TYPING, READ) to a user."""

    @abstractmethod
    def revoke_message(self, to_phone: str, message_id: str) -> RCSSendResult:
        """Revoke an undelivered message."""

    @abstractmethod
    def check_capability(self, phone: str) -> RCSCapability:
        """Check if a phone number supports RCS."""

    @abstractmethod
    def batch_check_capability(self, phones: List[str]) -> Dict[str, RCSCapability]:
        """Batch check RCS capability for multiple phones."""

    @abstractmethod
    def upload_file(self, file_url: str, thumbnail_url: Optional[str] = None) -> Dict[str, str]:
        """Upload a file to the RCS platform."""

    @abstractmethod
    def parse_inbound_webhook(self, payload: Dict) -> RCSInboundMessage:
        """Parse provider-specific inbound webhook into normalized form."""

    @abstractmethod
    def parse_event_webhook(self, payload: Dict) -> RCSEventReport:
        """Parse provider-specific event webhook into normalized form."""

    @abstractmethod
    def validate_webhook_signature(self, request) -> bool:
        """Verify that the webhook came from the RCS provider."""
