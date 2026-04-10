"""
Interactive Product Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive product message sending requests (Single Product Message - SPM).
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

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
# Product Action
# ============================================================================


class ProductAction(BaseModel):
    """Action for single product message"""
    catalog_id: str = Field(
        ...,
        description="Facebook catalog ID",
    )
    product_retailer_id: str = Field(
        ...,
        description="Product retailer ID from the catalog",
    )


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveProductContent(BaseModel):
    """Interactive single product content"""
    type: Literal["product"] = "product"
    body: Optional[InteractiveBody] = Field(
        None, description="Optional body text"
    )
    footer: Optional[InteractiveFooter] = Field(
        None, description="Optional footer"
    )
    action: ProductAction = Field(
        ..., description="Product action with catalog and product IDs"
    )


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(..., description="The message ID to reply to")


class InteractiveProductMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API interactive single product message send request.

    Single Product Messages (SPM) display a single product from your catalog.
    Users can view product details and add to cart.

    Requirements:
    - Catalog must be connected to WhatsApp Business Account
    - Product must be approved and in stock

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "interactive",
        ...     "interactive": {
        ...         "type": "product",
        ...         "body": {"text": "Check out this product!"},
        ...         "action": {
        ...             "catalog_id": "123456789",
        ...             "product_retailer_id": "SKU-001"
        ...         }
        ...     }
        ... }
        >>> request = InteractiveProductMessageSendRequestValidator(**data)
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
    interactive: InteractiveProductContent = Field(
        ..., description="Interactive single product content"
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
                        "type": "product",
                        "body": {"text": "Here's the product you were asking about!"},
                        "footer": {"text": "Tap to view details"},
                        "action": {
                            "catalog_id": "123456789012345",
                            "product_retailer_id": "PROD-SKU-001",
                        },
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "interactive",
                    "interactive": {
                        "type": "product",
                        "action": {
                            "catalog_id": "123456789012345",
                            "product_retailer_id": "wireless-headphones-black",
                        },
                    },
                },
            ]
        }
    }


def validate_interactive_product_message_send(
    data: dict,
) -> InteractiveProductMessageSendRequestValidator:
    """Validate an interactive single product message send request dictionary."""
    return InteractiveProductMessageSendRequestValidator(**data)
