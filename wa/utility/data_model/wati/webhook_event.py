"""
WATI Webhook Event Data Model

Pydantic model to parse webhook events from WATI.

WATI supports various webhook event types:
- Message received
- Message status updates (sent, delivered, read, failed)
- Template status updates
- Session message events

Reference: https://docs.wati.io/reference/message-received
"""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class WATIWebhookEventType(str, Enum):
    """WATI webhook event types."""

    MESSAGE_RECEIVED = "message_received"
    MESSAGE_STATUS = "message_status"
    TEMPLATE_STATUS = "template_status"
    SESSION_STATUS = "session_status"
    UNKNOWN = "unknown"


class WATIWebhookEvent(BaseModel):
    """
    Model to parse and normalize WATI webhook events.

    WATI sends webhook notifications for various events.
    This model provides a unified interface to determine the event type
    and extract relevant data.
    """

    # Event identification
    event_type: WATIWebhookEventType = Field(default=WATIWebhookEventType.UNKNOWN, description="Type of webhook event")

    # Common fields
    timestamp: Optional[str] = Field(None, description="Event timestamp")
    waba_id: Optional[str] = Field(None, description="WhatsApp Business Account ID")
    phone_number_id: Optional[str] = Field(None, description="Business phone number ID")

    # Message fields
    message_id: Optional[str] = Field(None, description="Message WAMID")
    wa_id: Optional[str] = Field(None, description="Customer WhatsApp ID")
    sender_name: Optional[str] = Field(None, description="Customer profile name")
    message_type: Optional[str] = Field(None, description="Message type")
    message_text: Optional[str] = Field(None, description="Message text content")

    # Status fields
    status: Optional[str] = Field(None, description="Status value")
    status_message_id: Optional[str] = Field(None, description="Message ID for status")

    # Template status fields
    template_name: Optional[str] = Field(None, description="Template name")
    template_status: Optional[str] = Field(None, description="Template approval status")
    template_rejection_reason: Optional[str] = Field(None, description="Reason for template rejection")

    # Raw data
    raw_payload: Optional[Dict[str, Any]] = Field(None, description="Raw webhook payload for debugging")

    @classmethod
    def from_webhook_payload(cls, payload: Dict[str, Any]) -> "WATIWebhookEvent":
        """
        Factory method to create WATIWebhookEvent from raw webhook data.

        Determines the event type and extracts relevant fields.

        Args:
            payload: Raw webhook payload from WATI.

        Returns:
            WATIWebhookEvent instance.
        """
        try:
            event_type = cls._determine_event_type(payload)

            common = {
                "event_type": event_type,
                "timestamp": payload.get("timestamp") or payload.get("created"),
                "waba_id": payload.get("wabaId"),
                "phone_number_id": payload.get("phoneNumberId"),
                "raw_payload": payload,
            }

            if event_type == WATIWebhookEventType.MESSAGE_RECEIVED:
                return cls(
                    **common,
                    message_id=payload.get("id") or payload.get("messageId"),
                    wa_id=payload.get("waId") or payload.get("senderPhoneNumber"),
                    sender_name=payload.get("senderName") or payload.get("pushName"),
                    message_type=payload.get("type") or payload.get("messageType"),
                    message_text=payload.get("text") or payload.get("data"),
                )
            elif event_type == WATIWebhookEventType.MESSAGE_STATUS:
                return cls(
                    **common,
                    status=payload.get("status") or payload.get("eventType"),
                    status_message_id=payload.get("statusMessageId") or payload.get("id"),
                    wa_id=payload.get("waId") or payload.get("recipientPhoneNumber"),
                )
            elif event_type == WATIWebhookEventType.TEMPLATE_STATUS:
                return cls(
                    **common,
                    template_name=payload.get("templateName") or payload.get("elementName"),
                    template_status=payload.get("templateStatus") or payload.get("event"),
                    template_rejection_reason=payload.get("rejectionReason") or payload.get("reason"),
                )
            else:
                return cls(**common)

        except (KeyError, TypeError, ValueError):
            return cls(
                event_type=WATIWebhookEventType.UNKNOWN,
                raw_payload=payload,
            )

    @staticmethod
    def _determine_event_type(payload: Dict[str, Any]) -> WATIWebhookEventType:
        """
        Determine the webhook event type from the payload.

        Uses heuristics based on the fields present in the payload.
        """
        # Check for template status events
        if payload.get("templateName") or payload.get("templateStatus"):
            return WATIWebhookEventType.TEMPLATE_STATUS

        # Check for message status events
        if payload.get("status") and not payload.get("type"):
            return WATIWebhookEventType.MESSAGE_STATUS

        if payload.get("eventType") in ("sent", "delivered", "read", "failed"):
            return WATIWebhookEventType.MESSAGE_STATUS

        # Check for incoming message events
        if payload.get("type") or payload.get("messageType"):
            return WATIWebhookEventType.MESSAGE_RECEIVED

        if payload.get("waId") and (payload.get("text") or payload.get("data")):
            return WATIWebhookEventType.MESSAGE_RECEIVED

        return WATIWebhookEventType.UNKNOWN
