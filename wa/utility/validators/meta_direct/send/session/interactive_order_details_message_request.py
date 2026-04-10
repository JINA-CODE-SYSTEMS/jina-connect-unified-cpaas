"""
Interactive Order Details Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive order_details message sending requests during the 24-hour session window.

An order_details message displays an order with items, pricing, and a
"Review and Pay" button that triggers WhatsApp India Payments.

Reference:
  https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-order-details-messages
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from wa.utility.data_model.shared.order_models import (
    OrderAmount,
    OrderDiscount,
    OrderItem,
    OrderShipping,
    OrderTax,
    PaymentGatewayConfig,
    PaymentSettings,
)

# ============================================================================
# Header Types (order_details supports text and image only)
# ============================================================================


class TextHeader(BaseModel):
    """Text header for interactive message"""
    type: Literal["text"] = "text"
    text: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Header text (max 60 characters)",
    )


class ImageHeader(BaseModel):
    """Image header for interactive message"""
    type: Literal["image"] = "image"
    image: dict = Field(
        ...,
        description="Image object with 'link' or 'id'",
    )


OrderDetailsHeader = Union[TextHeader, ImageHeader]


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
# Context (for message replies)
# ============================================================================


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(
        ...,
        description="The message_id of the message being replied to",
    )


# ============================================================================
# Order Contents
# ============================================================================


class OrderSession(BaseModel):
    """
    Order contents within an order_details request.

    ``status`` is always ``"pending"`` for new orders sent via review_and_pay.
    """
    status: Literal["pending"] = Field(
        "pending", description="Order status, always 'pending' for new orders"
    )
    catalog_id: Optional[str] = Field(
        None, description="Facebook catalog ID (optional)"
    )
    items: List[OrderItem] = Field(
        ...,
        min_length=1,
        max_length=999,
        description="List of order items (1–999)",
    )
    subtotal: OrderAmount = Field(
        ..., description="Subtotal = sum of (item.amount.value × item.quantity)"
    )
    tax: OrderTax = Field(
        ..., description="Tax amount"
    )
    shipping: Optional[OrderShipping] = Field(
        None, description="Shipping cost (optional)"
    )
    discount: Optional[OrderDiscount] = Field(
        None, description="Discount amount (optional)"
    )
    expiration: Optional[dict] = Field(
        None,
        description='Payment expiration: {"timestamp": "EPOCH", "description": "text"}',
    )

    @model_validator(mode="after")
    def validate_subtotal_matches_items(self):
        """Subtotal must equal sum of (item.amount.value × item.quantity)."""
        expected = sum(item.amount.value * item.quantity for item in self.items)
        if self.subtotal.value != expected:
            raise ValueError(
                f"subtotal.value ({self.subtotal.value}) does not match "
                f"sum of item.amount.value × item.quantity ({expected})"
            )
        return self


# ============================================================================
# Action Parameters
# ============================================================================


class OrderDetailsParameters(BaseModel):
    """
    Parameters for the ``review_and_pay`` action.

    ``total_amount`` must equal ``subtotal + tax + shipping − discount``.
    ``currency`` is currently limited to ``"INR"`` (WhatsApp India Payments).
    """
    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=35,
        description="Unique order reference ID (max 35 chars)",
    )
    type: Literal["digital-goods", "physical-goods"] = Field(
        ..., description="Goods type"
    )
    currency: Literal["INR"] = Field(
        "INR", description="Currency code (only INR supported)"
    )
    total_amount: OrderAmount = Field(
        ..., description="Order total = subtotal + tax + shipping − discount"
    )
    payment_settings: List[PaymentSettings] = Field(
        ...,
        min_length=1,
        description="Payment gateway configurations",
    )
    order: OrderSession = Field(
        ..., description="Order contents (items, subtotal, tax, etc.)"
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

    @model_validator(mode="after")
    def validate_total_amount_matches(self):
        """total_amount must equal subtotal + tax + shipping − discount."""
        order = self.order
        expected = order.subtotal.value + order.tax.value
        if order.shipping:
            expected += order.shipping.value
        if order.discount:
            expected -= order.discount.value
        if self.total_amount.value != expected:
            raise ValueError(
                f"total_amount.value ({self.total_amount.value}) does not match "
                f"subtotal ({order.subtotal.value}) + tax ({order.tax.value})"
                f" + shipping ({order.shipping.value if order.shipping else 0})"
                f" − discount ({order.discount.value if order.discount else 0})"
                f" = {expected}"
            )
        return self


# ============================================================================
# Action
# ============================================================================


class OrderDetailsAction(BaseModel):
    """Action wrapper for order_details messages."""
    name: Literal["review_and_pay"] = Field(
        "review_and_pay", description="Action name for order details"
    )
    parameters: OrderDetailsParameters = Field(
        ..., description="Order details parameters"
    )


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveOrderDetailsContent(BaseModel):
    """
    Content for interactive order_details messages.

    order_details supports text and image headers only (no video/document).
    """
    type: Literal["order_details"] = Field(
        "order_details", description="Interactive type: order_details"
    )
    body: InteractiveBody = Field(
        ..., description="Message body (required, max 1024 chars)"
    )
    action: OrderDetailsAction = Field(
        ..., description="Order details action with review_and_pay parameters"
    )
    header: Optional[OrderDetailsHeader] = Field(
        None, description="Optional header (text or image only)"
    )
    footer: Optional[InteractiveFooter] = Field(
        None, description="Optional footer"
    )

    @field_validator("header", mode="before")
    @classmethod
    def parse_header(cls, v):
        """Parse header dict into the correct typed model."""
        if v is None:
            return None
        if isinstance(v, dict):
            header_type = v.get("type", "text")
            if header_type == "text":
                return TextHeader(**v)
            elif header_type == "image":
                return ImageHeader(**v)
            else:
                raise ValueError(
                    f"order_details header type must be 'text' or 'image', got '{header_type}'"
                )
        return v


# ============================================================================
# Top-Level Request Validator
# ============================================================================


class InteractiveOrderDetailsMessageSendRequestValidator(BaseModel):
    """
    Validates a full META Cloud API order_details interactive message request.

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
    interactive: InteractiveOrderDetailsContent = Field(
        ..., description="Interactive order details content"
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
                        "type": "order_details",
                        "body": {"text": "Your order is ready for payment"},
                        "footer": {"text": "Thank you for shopping"},
                        "action": {
                            "name": "review_and_pay",
                            "parameters": {
                                "reference_id": "order-12345",
                                "type": "digital-goods",
                                "currency": "INR",
                                "total_amount": {"value": 60000, "offset": 100},
                                "payment_settings": [
                                    {
                                        "type": "payment_gateway",
                                        "payment_gateway": {
                                            "type": "razorpay",
                                            "configuration_name": "my-razorpay-config",
                                        },
                                    }
                                ],
                                "order": {
                                    "status": "pending",
                                    "items": [
                                        {
                                            "name": "Wireless Earbuds",
                                            "amount": {"value": 25000, "offset": 100},
                                            "quantity": 2,
                                            "retailer_id": "SKU-001",
                                        }
                                    ],
                                    "subtotal": {"value": 50000, "offset": 100},
                                    "tax": {"value": 10000, "offset": 100},
                                },
                            },
                        },
                    },
                }
            ]
        }
    }


def validate_interactive_order_details_message_send(
    data: dict,
) -> InteractiveOrderDetailsMessageSendRequestValidator:
    """Validate an interactive order_details message send request dictionary."""
    return InteractiveOrderDetailsMessageSendRequestValidator(**data)
