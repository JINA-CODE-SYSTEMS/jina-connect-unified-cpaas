"""
Text Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
text message sending requests during the 24-hour session window.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TextContent(BaseModel):
    """Text content for the message"""
    body: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="The text of the message (max 4096 characters)",
    )
    preview_url: Optional[bool] = Field(
        False,
        description="Whether to show URL preview if message contains a URL",
    )


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""
    message_id: str = Field(
        ...,
        description="The message ID to reply to",
    )


class TextMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API text message send request.

    Text messages can be sent during the 24-hour session window after
    a user-initiated conversation.

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "text",
        ...     "text": {
        ...         "body": "Hello! How can I help you today?",
        ...         "preview_url": False
        ...     }
        ... }
        >>> request = TextMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Messaging product (must be 'whatsapp')"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Recipient type (must be 'individual')"
    )
    to: str = Field(..., description="Recipient phone number")
    type: Literal["text"] = Field(
        "text", description="Message type (must be 'text')"
    )
    text: TextContent = Field(..., description="Text message content")
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
            "text": self.text.model_dump(exclude_none=True),
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
                    "type": "text",
                    "text": {
                        "body": "Hello! How can I help you today?",
                        "preview_url": False,
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "text",
                    "text": {
                        "body": "Check out our website: https://example.com",
                        "preview_url": True,
                    },
                    "context": {"message_id": "wamid.abcd1234"},
                },
            ]
        }
    }


def validate_text_message_send(data: dict) -> TextMessageSendRequestValidator:
    """Validate a text message send request dictionary."""
    return TextMessageSendRequestValidator(**data)
