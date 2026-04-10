"""
Sticker Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
sticker message sending requests during the 24-hour session window.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class StickerContent(BaseModel):
    """Sticker content for the message"""
    id: Optional[str] = Field(
        None,
        description="Media ID from uploaded sticker (use either id or link)",
    )
    link: Optional[str] = Field(
        None,
        description="URL of the sticker (use either id or link)",
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


class StickerMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API sticker message send request.

    Supported formats: WebP (static and animated)
    Static stickers: 512x512 pixels, max 100KB
    Animated stickers: 512x512 pixels, max 500KB

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "sticker",
        ...     "sticker": {
        ...         "link": "https://example.com/sticker.webp"
        ...     }
        ... }
        >>> request = StickerMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Messaging product (must be 'whatsapp')"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Recipient type (must be 'individual')"
    )
    to: str = Field(..., description="Recipient phone number")
    type: Literal["sticker"] = Field(
        "sticker", description="Message type (must be 'sticker')"
    )
    sticker: StickerContent = Field(..., description="Sticker message content")
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
            "sticker": self.sticker.model_dump(exclude_none=True),
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
                    "type": "sticker",
                    "sticker": {"link": "https://example.com/thumbs_up.webp"},
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "sticker",
                    "sticker": {"id": "sticker_media_id_123456"},
                },
            ]
        }
    }


def validate_sticker_message_send(data: dict) -> StickerMessageSendRequestValidator:
    """Validate a sticker message send request dictionary."""
    return StickerMessageSendRequestValidator(**data)
