"""
Interactive Flow Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive flow message sending requests.
"""

import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

# ============================================================================
# Header Types
# ============================================================================


class TextHeader(BaseModel):
    """Text header for flow message"""
    type: Literal["text"] = "text"
    text: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Header text (max 60 characters)",
    )


class ImageHeader(BaseModel):
    """Image header for flow message"""
    type: Literal["image"] = "image"
    image: dict = Field(
        ...,
        description="Image object with 'link' or 'id'",
    )


FlowHeader = Union[TextHeader, ImageHeader]


# ============================================================================
# Body and Footer
# ============================================================================


class InteractiveBody(BaseModel):
    """Body for interactive message"""
    text: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Body text (max 1024 characters)",
    )


class InteractiveFooter(BaseModel):
    """Footer for interactive message"""
    text: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Footer text (max 60 characters)",
    )


# ============================================================================
# Flow Action
# ============================================================================


class FlowActionPayload(BaseModel):
    """Payload for flow action"""
    screen: str = Field(
        ...,
        description="The first screen to display (for navigate mode)",
    )
    data: Optional[Dict[str, Any]] = Field(
        None,
        description="Data to pass to the flow",
    )


class FlowAction(BaseModel):
    """Action for flow interactive message"""
    name: Literal["flow"] = "flow"
    parameters: dict = Field(
        ...,
        description="Flow parameters including flow_id, flow_cta, mode, etc.",
    )

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, v):
        required_fields = ["flow_id", "flow_cta"]
        for field in required_fields:
            if field not in v:
                raise ValueError(f"Flow parameters must include '{field}'")
        
        # flow_cta max length is 20
        if len(v.get("flow_cta", "")) > 20:
            raise ValueError("flow_cta must be max 20 characters")
        
        # Validate mode if present
        valid_modes = ["navigate", "draft"]
        if "mode" in v and v["mode"] not in valid_modes:
            raise ValueError(f"Invalid mode. Must be one of: {valid_modes}")
        
        return v


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveFlowContent(BaseModel):
    """Interactive flow content"""
    type: Literal["flow"] = "flow"
    header: Optional[FlowHeader] = Field(
        None, description="Optional header (text or image)"
    )
    body: InteractiveBody = Field(..., description="Message body (required)")
    footer: Optional[InteractiveFooter] = Field(None, description="Optional footer")
    action: FlowAction = Field(..., description="Flow action")

    @field_validator("header", mode="before")
    @classmethod
    def parse_header(cls, v):
        if v is None:
            return None
        if isinstance(v, BaseModel):
            return v
        if isinstance(v, dict):
            header_type = v.get("type")
            if header_type == "text":
                return TextHeader(**v)
            elif header_type == "image":
                return ImageHeader(**v)
            else:
                raise ValueError(f"Unknown header type: {header_type}")
        return v


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(..., description="The message ID to reply to")


class InteractiveFlowMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API interactive flow message send request.

    Flow messages launch WhatsApp Flows - interactive, multi-step forms
    and experiences within WhatsApp.

    Requirements:
    - Flow must be created and published in WhatsApp Manager
    - flow_id must be a valid published flow ID

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "interactive",
        ...     "interactive": {
        ...         "type": "flow",
        ...         "body": {"text": "Book your appointment"},
        ...         "action": {
        ...             "name": "flow",
        ...             "parameters": {
        ...                 "flow_id": "1234567890",
        ...                 "flow_cta": "Book Now",
        ...                 "mode": "navigate",
        ...                 "flow_action": "navigate",
        ...                 "flow_action_payload": {
        ...                     "screen": "BOOKING_SCREEN"
        ...                 }
        ...             }
        ...         }
        ...     }
        ... }
        >>> request = InteractiveFlowMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Messaging product (must be 'whatsapp')"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Recipient type (must be 'individual')"
    )
    to: str = Field(..., description="Recipient phone number")
    type: Literal["interactive"] = Field(
        "interactive", description="Message type (must be 'interactive')"
    )
    interactive: InteractiveFlowContent = Field(
        ..., description="Interactive flow content"
    )
    context: Optional[ContextInfo] = Field(
        None, description="Context for replying to a specific message"
    )

    @field_validator("to")
    @classmethod
    def validate_phone_number(cls, v):
        if not v or not v.strip():
            raise ValueError("Recipient phone number cannot be empty")
        cleaned = re.sub(r"[^\d+]", "", v)
        if not re.match(r"^\+?[0-9]{10,15}$", cleaned):
            raise ValueError(
                "Invalid phone number format. Must be 10-15 digits, optionally starting with +"
            )
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
                        "type": "flow",
                        "header": {"type": "text", "text": "Book Appointment"},
                        "body": {"text": "Schedule your appointment with us in just a few steps."},
                        "footer": {"text": "Powered by WhatsApp Flows"},
                        "action": {
                            "name": "flow",
                            "parameters": {
                                "flow_id": "1234567890123456",
                                "flow_cta": "Book Now",
                                "mode": "navigate",
                                "flow_action": "navigate",
                                "flow_action_payload": {
                                    "screen": "APPOINTMENT_SCREEN",
                                    "data": {"service_type": "consultation"},
                                },
                            },
                        },
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "interactive",
                    "interactive": {
                        "type": "flow",
                        "body": {"text": "Complete your registration to get started."},
                        "action": {
                            "name": "flow",
                            "parameters": {
                                "flow_id": "9876543210123456",
                                "flow_cta": "Register",
                                "mode": "navigate",
                                "flow_action": "navigate",
                                "flow_action_payload": {"screen": "REGISTRATION"},
                            },
                        },
                    },
                },
            ]
        }
    }


def validate_interactive_flow_message_send(
    data: dict,
) -> InteractiveFlowMessageSendRequestValidator:
    """Validate an interactive flow message send request dictionary."""
    return InteractiveFlowMessageSendRequestValidator(**data)
