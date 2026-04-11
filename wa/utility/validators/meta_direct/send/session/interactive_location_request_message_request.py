"""
Interactive Location Request Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive location request message sending requests.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ============================================================================
# Body
# ============================================================================


class InteractiveBody(BaseModel):
    """Body for interactive message"""

    text: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Body text (max 1024 characters)",
    )


# ============================================================================
# Location Request Action
# ============================================================================


class LocationRequestAction(BaseModel):
    """Action for location request interactive message"""

    name: Literal["send_location"] = "send_location"


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveLocationRequestContent(BaseModel):
    """Interactive location request content"""

    type: Literal["location_request_message"] = "location_request_message"
    body: InteractiveBody = Field(..., description="Message body (required)")
    action: LocationRequestAction = Field(..., description="Location request action")


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""

    message_id: str = Field(..., description="The message ID to reply to")


class InteractiveLocationRequestMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API interactive location request message send request.

    Location request messages prompt users to share their location.
    When the user taps the button, they can choose to share their current location.

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "interactive",
        ...     "interactive": {
        ...         "type": "location_request_message",
        ...         "body": {"text": "Please share your location for delivery"},
        ...         "action": {"name": "send_location"}
        ...     }
        ... }
        >>> request = InteractiveLocationRequestMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field("whatsapp", description="Messaging product (must be 'whatsapp')")
    recipient_type: Literal["individual"] = Field("individual", description="Recipient type (must be 'individual')")
    to: str = Field(..., description="Recipient phone number")
    type: Literal["interactive"] = Field("interactive", description="Message type (must be 'interactive')")
    interactive: InteractiveLocationRequestContent = Field(..., description="Interactive location request content")
    context: Optional[ContextInfo] = Field(None, description="Context for replying to a specific message")

    @field_validator("to")
    @classmethod
    def validate_phone_number(cls, v):
        if not v or not v.strip():
            raise ValueError("Recipient phone number cannot be empty")
        cleaned = re.sub(r"[^\d+]", "", v)
        if not re.match(r"^\+?[0-9]{10,15}$", cleaned):
            raise ValueError("Invalid phone number format. Must be 10-15 digits, optionally starting with +")
        return cleaned

    def to_meta_payload(self) -> dict:
        """Convert validated request to META API payload format"""
        payload = {
            "messaging_product": self.messaging_product,
            "recipient_type": self.recipient_type,
            "to": self.to,
            "type": self.type,
            "interactive": self.interactive.model_dump(exclude_none=True),
        }
        if self.context:
            payload["context"] = self.context.model_dump(exclude_none=True)
        return payload

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "interactive",
                    "interactive": {
                        "type": "location_request_message",
                        "body": {"text": "To deliver your order, please share your current location."},
                        "action": {"name": "send_location"},
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "interactive",
                    "interactive": {
                        "type": "location_request_message",
                        "body": {"text": "Share your location so we can find stores near you."},
                        "action": {"name": "send_location"},
                    },
                },
            ]
        }
    }


def validate_interactive_location_request_message_send(
    data: dict,
) -> InteractiveLocationRequestMessageSendRequestValidator:
    """Validate an interactive location request message send request dictionary."""
    return InteractiveLocationRequestMessageSendRequestValidator(**data)
