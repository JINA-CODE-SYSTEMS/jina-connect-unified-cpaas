"""
Audio Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
audio message sending requests during the 24-hour session window.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AudioContent(BaseModel):
    """Audio content for the message"""
    id: Optional[str] = Field(
        None,
        description="Media ID from uploaded media (use either id or link)",
    )
    link: Optional[str] = Field(
        None,
        description="URL of the audio file (use either id or link)",
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


class AudioMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API audio message send request.

    Supported formats: AAC, MP4, MPEG, AMR, OGG (OPUS codecs only)
    Max size: 16MB

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "audio",
        ...     "audio": {
        ...         "link": "https://example.com/audio.mp3"
        ...     }
        ... }
        >>> request = AudioMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Messaging product (must be 'whatsapp')"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Recipient type (must be 'individual')"
    )
    to: str = Field(..., description="Recipient phone number")
    type: Literal["audio"] = Field(
        "audio", description="Message type (must be 'audio')"
    )
    audio: AudioContent = Field(..., description="Audio message content")
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
            "audio": self.audio.model_dump(exclude_none=True),
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
                    "type": "audio",
                    "audio": {"link": "https://example.com/voice_note.ogg"},
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "audio",
                    "audio": {"id": "media_id_123456"},
                },
            ]
        }
    }


def validate_audio_message_send(data: dict) -> AudioMessageSendRequestValidator:
    """Validate an audio message send request dictionary."""
    return AudioMessageSendRequestValidator(**data)
