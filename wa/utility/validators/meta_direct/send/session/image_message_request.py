"""
Image Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
image message sending requests during the 24-hour session window.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ImageContent(BaseModel):
    """Image content for the message"""

    id: Optional[str] = Field(
        None,
        description="Media ID from uploaded media (use either id or link)",
    )
    link: Optional[str] = Field(
        None,
        description="URL of the image (use either id or link)",
    )
    caption: Optional[str] = Field(
        None,
        max_length=1024,
        description="Image caption (max 1024 characters)",
    )

    @model_validator(mode="after")
    def validate_id_or_link(self):
        if not self.id and not self.link:
            raise ValueError("Either 'id' or 'link' must be provided")
        if self.id and self.link:
            raise ValueError("Only one of 'id' or 'link' should be provided, not both")
        return self

    @field_validator("link")
    @classmethod
    def validate_link(cls, v):
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("Link must be a valid HTTP/HTTPS URL")
        return v


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""

    message_id: str = Field(..., description="The message ID to reply to")


class ImageMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API image message send request.

    Supported formats: JPEG, PNG
    Max size: 5MB

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "image",
        ...     "image": {
        ...         "link": "https://example.com/image.jpg",
        ...         "caption": "Check out this image!"
        ...     }
        ... }
        >>> request = ImageMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field("whatsapp", description="Messaging product (must be 'whatsapp')")
    recipient_type: Literal["individual"] = Field("individual", description="Recipient type (must be 'individual')")
    to: str = Field(..., description="Recipient phone number")
    type: Literal["image"] = Field("image", description="Message type (must be 'image')")
    image: ImageContent = Field(..., description="Image message content")
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
            "image": self.image.model_dump(exclude_none=True),
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
                    "type": "image",
                    "image": {
                        "link": "https://example.com/product.jpg",
                        "caption": "Here's the product you requested!",
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "image",
                    "image": {"id": "media_id_123456"},
                },
            ]
        }
    }


def validate_image_message_send(data: dict) -> ImageMessageSendRequestValidator:
    """Validate an image message send request dictionary."""
    return ImageMessageSendRequestValidator(**data)
