"""
Reaction Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
reaction message sending requests during the 24-hour session window.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ReactionContent(BaseModel):
    """Reaction content for the message"""

    message_id: str = Field(
        ...,
        description="The message ID to react to",
    )
    emoji: str = Field(
        ...,
        description="Emoji to react with (use empty string to remove reaction)",
    )

    @field_validator("emoji")
    @classmethod
    def validate_emoji(cls, v):
        # Empty string is valid (removes reaction)
        # Otherwise should be a valid emoji character
        if v and len(v) > 10:  # Emojis can be multi-codepoint
            raise ValueError("Invalid emoji - too long")
        return v


class ReactionMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API reaction message send request.

    Reaction messages allow reacting to a specific message with an emoji.
    To remove a reaction, send an empty emoji string.

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "reaction",
        ...     "reaction": {
        ...         "message_id": "wamid.abcd1234",
        ...         "emoji": "👍"
        ...     }
        ... }
        >>> request = ReactionMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field("whatsapp", description="Messaging product (must be 'whatsapp')")
    recipient_type: Literal["individual"] = Field("individual", description="Recipient type (must be 'individual')")
    to: str = Field(..., description="Recipient phone number")
    type: Literal["reaction"] = Field("reaction", description="Message type (must be 'reaction')")
    reaction: ReactionContent = Field(..., description="Reaction content")

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
        return {
            "messaging_product": self.messaging_product,
            "recipient_type": self.recipient_type,
            "to": self.to,
            "type": self.type,
            "reaction": self.reaction.model_dump(exclude_none=True),
        }

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "reaction",
                    "reaction": {
                        "message_id": "wamid.HBgLMTIzNDU2Nzg5MBUCABIYFjNFQjBDNkY3",
                        "emoji": "👍",
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "reaction",
                    "reaction": {
                        "message_id": "wamid.HBgLMTIzNDU2Nzg5MBUCABIYFjNFQjBDNkY3",
                        "emoji": "",  # Remove reaction
                    },
                },
            ]
        }
    }


def validate_reaction_message_send(data: dict) -> ReactionMessageSendRequestValidator:
    """Validate a reaction message send request dictionary."""
    return ReactionMessageSendRequestValidator(**data)
