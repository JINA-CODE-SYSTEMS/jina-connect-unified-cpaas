"""
Video Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
video message sending requests during the 24-hour session window.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class VideoContent(BaseModel):
    """Video content for the message"""
    id: Optional[str] = Field(
        None,
        description="Media ID from uploaded media (use either id or link)",
    )
    link: Optional[str] = Field(
        None,
        description="URL of the video (use either id or link)",
    )
    caption: Optional[str] = Field(
        None,
        max_length=1024,
        description="Video caption (max 1024 characters)",
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


class VideoMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API video message send request.

    Supported formats: MP4, 3GPP
    Max size: 16MB

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "video",
        ...     "video": {
        ...         "link": "https://example.com/video.mp4",
        ...         "caption": "Check out this video!"
        ...     }
        ... }
        >>> request = VideoMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Messaging product (must be 'whatsapp')"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Recipient type (must be 'individual')"
    )
    to: str = Field(..., description="Recipient phone number")
    type: Literal["video"] = Field(
        "video", description="Message type (must be 'video')"
    )
    video: VideoContent = Field(..., description="Video message content")
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
            "video": self.video.model_dump(exclude_none=True),
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
                    "type": "video",
                    "video": {
                        "link": "https://example.com/demo.mp4",
                        "caption": "Product demo video",
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "video",
                    "video": {"id": "media_id_123456"},
                },
            ]
        }
    }


def validate_video_message_send(data: dict) -> VideoMessageSendRequestValidator:
    """Validate a video message send request dictionary."""
    return VideoMessageSendRequestValidator(**data)
