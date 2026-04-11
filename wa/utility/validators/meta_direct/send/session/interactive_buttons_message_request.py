"""
Interactive Buttons Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive button message sending requests during the 24-hour session window.
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

# ============================================================================
# Header Types
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


class VideoHeader(BaseModel):
    """Video header for interactive message"""

    type: Literal["video"] = "video"
    video: dict = Field(
        ...,
        description="Video object with 'link' or 'id'",
    )


class DocumentHeader(BaseModel):
    """Document header for interactive message"""

    type: Literal["document"] = "document"
    document: dict = Field(
        ...,
        description="Document object with 'link' or 'id' and optional 'filename'",
    )


InteractiveHeader = Union[TextHeader, ImageHeader, VideoHeader, DocumentHeader]


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
# Reply Buttons
# ============================================================================


class ReplyButton(BaseModel):
    """Reply button for interactive message"""

    type: Literal["reply"] = "reply"
    reply: dict = Field(
        ...,
        description="Reply object with 'id' (max 256 chars) and 'title' (max 20 chars)",
    )

    @field_validator("reply")
    @classmethod
    def validate_reply(cls, v):
        if "id" not in v:
            raise ValueError("Reply button must have 'id'")
        if "title" not in v:
            raise ValueError("Reply button must have 'title'")
        if len(v["id"]) > 256:
            raise ValueError("Reply button id must be max 256 characters")
        if len(v["title"]) > 20:
            raise ValueError("Reply button title must be max 20 characters")
        return v


class ButtonsAction(BaseModel):
    """Action for button interactive message"""

    buttons: List[ReplyButton] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="List of reply buttons (max 3)",
    )


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveButtonsContent(BaseModel):
    """Interactive buttons content"""

    type: Literal["button"] = "button"
    header: Optional[InteractiveHeader] = Field(None, description="Optional header (text, image, video, or document)")
    body: InteractiveBody = Field(..., description="Message body (required)")
    footer: Optional[InteractiveFooter] = Field(None, description="Optional footer")
    action: ButtonsAction = Field(..., description="Button action")

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


class InteractiveButtonsMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API interactive buttons message send request.

    Interactive button messages display up to 3 reply buttons that users can tap.

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "interactive",
        ...     "interactive": {
        ...         "type": "button",
        ...         "body": {"text": "Choose an option:"},
        ...         "action": {
        ...             "buttons": [
        ...                 {"type": "reply", "reply": {"id": "opt1", "title": "Option 1"}},
        ...                 {"type": "reply", "reply": {"id": "opt2", "title": "Option 2"}}
        ...             ]
        ...         }
        ...     }
        ... }
        >>> request = InteractiveButtonsMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field("whatsapp", description="Messaging product (must be 'whatsapp')")
    recipient_type: Literal["individual"] = Field("individual", description="Recipient type (must be 'individual')")
    to: str = Field(..., description="Recipient phone number")
    type: Literal["interactive"] = Field("interactive", description="Message type (must be 'interactive')")
    interactive: InteractiveButtonsContent = Field(..., description="Interactive buttons content")
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
                        "type": "button",
                        "header": {"type": "text", "text": "Order Confirmation"},
                        "body": {"text": "Would you like to confirm your order?"},
                        "footer": {"text": "Reply within 24 hours"},
                        "action": {
                            "buttons": [
                                {"type": "reply", "reply": {"id": "confirm", "title": "Confirm"}},
                                {"type": "reply", "reply": {"id": "cancel", "title": "Cancel"}},
                                {"type": "reply", "reply": {"id": "modify", "title": "Modify Order"}},
                            ]
                        },
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "interactive",
                    "interactive": {
                        "type": "button",
                        "header": {
                            "type": "image",
                            "image": {"link": "https://example.com/product.jpg"},
                        },
                        "body": {"text": "Check out this product! Would you like to know more?"},
                        "action": {
                            "buttons": [
                                {"type": "reply", "reply": {"id": "yes", "title": "Yes, tell me more"}},
                                {"type": "reply", "reply": {"id": "no", "title": "No, thanks"}},
                            ]
                        },
                    },
                },
            ]
        }
    }


def validate_interactive_buttons_message_send(
    data: dict,
) -> InteractiveButtonsMessageSendRequestValidator:
    """Validate an interactive buttons message send request dictionary."""
    return InteractiveButtonsMessageSendRequestValidator(**data)
