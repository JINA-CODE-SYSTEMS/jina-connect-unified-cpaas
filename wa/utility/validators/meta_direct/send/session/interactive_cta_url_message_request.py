"""
Interactive CTA URL Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive CTA (Call-To-Action) URL button message sending requests.
"""

import re
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

# ============================================================================
# Header Types
# ============================================================================


class TextHeader(BaseModel):
    """Text header for CTA URL message"""
    type: Literal["text"] = "text"
    text: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Header text (max 60 characters)",
    )


class ImageHeader(BaseModel):
    """Image header for CTA URL message"""
    type: Literal["image"] = "image"
    image: dict = Field(
        ...,
        description="Image object with 'link' or 'id'",
    )


class VideoHeader(BaseModel):
    """Video header for CTA URL message"""
    type: Literal["video"] = "video"
    video: dict = Field(
        ...,
        description="Video object with 'link' or 'id'",
    )


class DocumentHeader(BaseModel):
    """Document header for CTA URL message"""
    type: Literal["document"] = "document"
    document: dict = Field(
        ...,
        description="Document object with 'link' or 'id'",
    )


CTAHeader = Union[TextHeader, ImageHeader, VideoHeader, DocumentHeader]


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
# CTA URL Action
# ============================================================================


class CTAURLAction(BaseModel):
    """Action for CTA URL interactive message"""
    name: Literal["cta_url"] = "cta_url"
    parameters: dict = Field(
        ...,
        description="CTA URL parameters with 'display_text' and 'url'",
    )

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, v):
        if "display_text" not in v:
            raise ValueError("CTA URL parameters must include 'display_text'")
        if "url" not in v:
            raise ValueError("CTA URL parameters must include 'url'")
        
        # display_text max length is 20
        if len(v.get("display_text", "")) > 20:
            raise ValueError("display_text must be max 20 characters")
        
        # Validate URL format
        url = v.get("url", "")
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must be a valid HTTP/HTTPS URL")
        
        return v


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveCTAURLContent(BaseModel):
    """Interactive CTA URL content"""
    type: Literal["cta_url"] = "cta_url"
    header: Optional[CTAHeader] = Field(
        None, description="Optional header (text, image, video, or document)"
    )
    body: InteractiveBody = Field(..., description="Message body (required)")
    footer: Optional[InteractiveFooter] = Field(None, description="Optional footer")
    action: CTAURLAction = Field(..., description="CTA URL action")

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
            elif header_type == "video":
                return VideoHeader(**v)
            elif header_type == "document":
                return DocumentHeader(**v)
            else:
                raise ValueError(f"Unknown header type: {header_type}")
        return v


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(..., description="The message ID to reply to")


class InteractiveCTAURLMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API interactive CTA URL message send request.

    CTA URL messages display a button that opens a URL when tapped.
    Unlike template URL buttons, these can be sent during the 24-hour session window.

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "interactive",
        ...     "interactive": {
        ...         "type": "cta_url",
        ...         "body": {"text": "Visit our website for more info"},
        ...         "action": {
        ...             "name": "cta_url",
        ...             "parameters": {
        ...                 "display_text": "Visit Website",
        ...                 "url": "https://example.com"
        ...             }
        ...         }
        ...     }
        ... }
        >>> request = InteractiveCTAURLMessageSendRequestValidator(**data)
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
    interactive: InteractiveCTAURLContent = Field(
        ..., description="Interactive CTA URL content"
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
                        "type": "cta_url",
                        "header": {"type": "text", "text": "Special Offer!"},
                        "body": {"text": "Click below to check out our exclusive deals."},
                        "footer": {"text": "Limited time offer"},
                        "action": {
                            "name": "cta_url",
                            "parameters": {
                                "display_text": "Shop Now",
                                "url": "https://example.com/deals",
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
                        "type": "cta_url",
                        "body": {"text": "Track your order status in real-time."},
                        "action": {
                            "name": "cta_url",
                            "parameters": {
                                "display_text": "Track Order",
                                "url": "https://example.com/track/ORD-12345",
                            },
                        },
                    },
                },
            ]
        }
    }


def validate_interactive_cta_url_message_send(
    data: dict,
) -> InteractiveCTAURLMessageSendRequestValidator:
    """Validate an interactive CTA URL message send request dictionary."""
    return InteractiveCTAURLMessageSendRequestValidator(**data)
