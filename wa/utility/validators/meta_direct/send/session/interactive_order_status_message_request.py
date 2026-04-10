"""
Interactive Order Status Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive order_status message sending requests during the 24-hour session window.

An order_status message updates the status of a previously sent order
(identified by reference_id from the original order_details message).

Reference:
  https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-order-status-messages
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ============================================================================
# Body (reused shape, local definition for validator independence)
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
# Context (for message replies)
# ============================================================================


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(
        ...,
        description="The message_id of the message being replied to",
    )


# ============================================================================
# Order Status
# ============================================================================


class OrderStatusOrder(BaseModel):
    """
    Order status payload for order_status messages.

    ``status`` must be one of the META-defined lifecycle states.
    Note: "pending" is NOT valid here — it's only used in order_details.
    """
    status: Literal[
        "processing", "shipped", "completed",
        "canceled", "partially_shipped",
    ] = Field(..., description="New order status")
    description: Optional[str] = Field(
        None,
        max_length=120,
        description="Optional description, e.g., 'Tracking #: TRK12345'",
    )


class OrderStatusParameters(BaseModel):
    """Parameters for the ``review_order`` action."""
    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=35,
        description="Same reference_id from the original order_details message",
    )
    order: OrderStatusOrder = Field(
        ..., description="New order status"
    )

    @field_validator("reference_id")
    @classmethod
    def validate_reference_id_format(cls, v):
        """Reference ID: alphanumeric, underscores, dashes, dots only."""
        if not re.match(r"^[a-zA-Z0-9_.\-]+$", v):
            raise ValueError(
                "reference_id must contain only alphanumeric characters, "
                "underscores, dashes, and dots"
            )
        return v


class OrderStatusAction(BaseModel):
    """Action wrapper for order_status messages."""
    name: Literal["review_order"] = Field(
        "review_order", description="Action name for order status updates"
    )
    parameters: OrderStatusParameters = Field(
        ..., description="Order status parameters"
    )


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveOrderStatusContent(BaseModel):
    """
    Content for interactive order_status messages.

    order_status is simpler than order_details — no header, footer, items,
    or amounts. Just body text and the status update action.
    """
    type: Literal["order_status"] = Field(
        "order_status", description="Interactive type: order_status"
    )
    body: InteractiveBody = Field(
        ..., description="Message body (required)"
    )
    action: OrderStatusAction = Field(
        ..., description="Order status action with review_order parameters"
    )


# ============================================================================
# Top-Level Request Validator
# ============================================================================


class InteractiveOrderStatusMessageSendRequestValidator(BaseModel):
    """
    Validates a full META Cloud API order_status interactive message request.

    Envelope: messaging_product, recipient_type, to, type, interactive
    """
    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Must be 'whatsapp'"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Must be 'individual'"
    )
    to: str = Field(
        ..., description="Recipient phone number"
    )
    type: Literal["interactive"] = Field(
        "interactive", description="Message type: interactive"
    )
    interactive: InteractiveOrderStatusContent = Field(
        ..., description="Interactive order status content"
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
        """Convert validated request to META API payload format."""
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
                        "type": "order_status",
                        "body": {"text": "Your order has been shipped!"},
                        "action": {
                            "name": "review_order",
                            "parameters": {
                                "reference_id": "order-12345",
                                "order": {
                                    "status": "shipped",
                                    "description": "Tracking #: TRK12345",
                                },
                            },
                        },
                    },
                }
            ]
        }
    }


def validate_interactive_order_status_message_send(
    data: dict,
) -> InteractiveOrderStatusMessageSendRequestValidator:
    """Validate an interactive order_status message send request dictionary."""
    return InteractiveOrderStatusMessageSendRequestValidator(**data)
