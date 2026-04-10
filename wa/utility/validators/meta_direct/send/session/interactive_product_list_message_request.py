"""
Interactive Product List Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive product list message sending requests (Multi-Product Message - MPM).
"""

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ============================================================================
# Header Types
# ============================================================================


class TextHeader(BaseModel):
    """Text header for product list message"""
    type: Literal["text"] = "text"
    text: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Header text (max 60 characters)",
    )


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
# Product List Items and Sections
# ============================================================================


class ProductItem(BaseModel):
    """A product item in a section"""
    product_retailer_id: str = Field(
        ...,
        description="Product retailer ID from the catalog",
    )


class ProductSection(BaseModel):
    """A section in the product list"""
    title: Optional[str] = Field(
        None,
        max_length=24,
        description="Section title (max 24 characters)",
    )
    product_items: List[ProductItem] = Field(
        ...,
        min_length=1,
        description="List of products in this section",
    )


class ProductListAction(BaseModel):
    """Action for product list interactive message"""
    catalog_id: str = Field(
        ...,
        description="Facebook catalog ID",
    )
    sections: List[ProductSection] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Product sections (max 10 sections)",
    )

    @model_validator(mode="after")
    def validate_sections(self):
        # Total products across all sections cannot exceed 30
        total_products = sum(
            len(section.product_items) for section in self.sections
        )
        if total_products > 30:
            raise ValueError(
                f"Total products across all sections cannot exceed 30, got {total_products}"
            )
        # If multiple sections, each should have a title
        if len(self.sections) > 1:
            for i, section in enumerate(self.sections):
                if not section.title:
                    raise ValueError(
                        f"Section {i + 1} should have a title when multiple sections are present"
                    )
        return self


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveProductListContent(BaseModel):
    """Interactive product list content"""
    type: Literal["product_list"] = "product_list"
    header: TextHeader = Field(
        ..., description="Header (required for product list)"
    )
    body: InteractiveBody = Field(..., description="Message body (required)")
    footer: Optional[InteractiveFooter] = Field(None, description="Optional footer")
    action: ProductListAction = Field(..., description="Product list action")


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(..., description="The message ID to reply to")


class InteractiveProductListMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API interactive product list message send request.

    Multi-Product Messages (MPM) display multiple products from your catalog.
    - Max 10 sections
    - Max 30 products total across all sections
    - Header is required

    Requirements:
    - Catalog must be connected to WhatsApp Business Account
    - Products must be approved and in stock

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "interactive",
        ...     "interactive": {
        ...         "type": "product_list",
        ...         "header": {"type": "text", "text": "Our Products"},
        ...         "body": {"text": "Check out our collection!"},
        ...         "action": {
        ...             "catalog_id": "123456789",
        ...             "sections": [{
        ...                 "title": "Featured",
        ...                 "product_items": [
        ...                     {"product_retailer_id": "SKU-001"},
        ...                     {"product_retailer_id": "SKU-002"}
        ...                 ]
        ...             }]
        ...         }
        ...     }
        ... }
        >>> request = InteractiveProductListMessageSendRequestValidator(**data)
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
    interactive: InteractiveProductListContent = Field(
        ..., description="Interactive product list content"
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
                        "type": "product_list",
                        "header": {"type": "text", "text": "Our Best Sellers"},
                        "body": {"text": "Check out our most popular products!"},
                        "footer": {"text": "Tap to view details"},
                        "action": {
                            "catalog_id": "123456789012345",
                            "sections": [
                                {
                                    "title": "Electronics",
                                    "product_items": [
                                        {"product_retailer_id": "headphones-001"},
                                        {"product_retailer_id": "speaker-002"},
                                    ],
                                },
                                {
                                    "title": "Accessories",
                                    "product_items": [
                                        {"product_retailer_id": "case-001"},
                                        {"product_retailer_id": "charger-002"},
                                    ],
                                },
                            ],
                        },
                    },
                },
            ]
        }
    }


def validate_interactive_product_list_message_send(
    data: dict,
) -> InteractiveProductListMessageSendRequestValidator:
    """Validate an interactive product list message send request dictionary."""
    return InteractiveProductListMessageSendRequestValidator(**data)
