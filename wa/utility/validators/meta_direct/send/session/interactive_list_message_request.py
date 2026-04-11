"""
Interactive List Message Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
interactive list message sending requests during the 24-hour session window.
"""

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ============================================================================
# Header Types
# ============================================================================


class TextHeader(BaseModel):
    """Text header for list message (only text headers allowed)"""

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
# List Items and Sections
# ============================================================================


class ListRow(BaseModel):
    """A row item in a list section"""

    id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Unique row ID (max 200 characters)",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=24,
        description="Row title (max 24 characters)",
    )
    description: Optional[str] = Field(
        None,
        max_length=72,
        description="Row description (max 72 characters)",
    )


class ListSection(BaseModel):
    """A section in the list"""

    title: Optional[str] = Field(
        None,
        max_length=24,
        description="Section title (max 24 characters, required if multiple sections)",
    )
    rows: List[ListRow] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="List rows (max 10 per section)",
    )


class ListAction(BaseModel):
    """Action for list interactive message"""

    button: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Button text to open list (max 20 characters)",
    )
    sections: List[ListSection] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="List sections (max 10 sections)",
    )

    @model_validator(mode="after")
    def validate_sections(self):
        # If multiple sections, each must have a title
        if len(self.sections) > 1:
            for i, section in enumerate(self.sections):
                if not section.title:
                    raise ValueError(f"Section {i + 1} must have a title when multiple sections are present")
        # Total rows across all sections cannot exceed 10
        total_rows = sum(len(section.rows) for section in self.sections)
        if total_rows > 10:
            raise ValueError(f"Total rows across all sections cannot exceed 10, got {total_rows}")
        return self


# ============================================================================
# Interactive Content
# ============================================================================


class InteractiveListContent(BaseModel):
    """Interactive list content"""

    type: Literal["list"] = "list"
    header: Optional[TextHeader] = Field(None, description="Optional text header")
    body: InteractiveBody = Field(..., description="Message body (required)")
    footer: Optional[InteractiveFooter] = Field(None, description="Optional footer")
    action: ListAction = Field(..., description="List action")


class ContextInfo(BaseModel):
    """Context for replying to a specific message"""

    message_id: str = Field(..., description="The message ID to reply to")


class InteractiveListMessageSendRequestValidator(BaseModel):
    """
    Validator for META Direct API interactive list message send request.

    Interactive list messages display a menu with sections and selectable rows.
    - Max 10 sections
    - Max 10 rows total across all sections
    - Section titles required when multiple sections

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "interactive",
        ...     "interactive": {
        ...         "type": "list",
        ...         "header": {"type": "text", "text": "Our Menu"},
        ...         "body": {"text": "Please select an item:"},
        ...         "action": {
        ...             "button": "View Menu",
        ...             "sections": [{
        ...                 "title": "Main Dishes",
        ...                 "rows": [
        ...                     {"id": "pizza", "title": "Pizza", "description": "$12.99"},
        ...                     {"id": "burger", "title": "Burger", "description": "$9.99"}
        ...                 ]
        ...             }]
        ...         }
        ...     }
        ... }
        >>> request = InteractiveListMessageSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field("whatsapp", description="Messaging product (must be 'whatsapp')")
    recipient_type: Literal["individual"] = Field("individual", description="Recipient type (must be 'individual')")
    to: str = Field(..., description="Recipient phone number")
    type: Literal["interactive"] = Field("interactive", description="Message type (must be 'interactive')")
    interactive: InteractiveListContent = Field(..., description="Interactive list content")
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
                        "type": "list",
                        "header": {"type": "text", "text": "Order Menu"},
                        "body": {"text": "Please choose what you'd like to order:"},
                        "footer": {"text": "Tap to select"},
                        "action": {
                            "button": "View Menu",
                            "sections": [
                                {
                                    "title": "Pizzas",
                                    "rows": [
                                        {"id": "margherita", "title": "Margherita", "description": "$10.99"},
                                        {"id": "pepperoni", "title": "Pepperoni", "description": "$12.99"},
                                    ],
                                },
                                {
                                    "title": "Drinks",
                                    "rows": [
                                        {"id": "cola", "title": "Cola", "description": "$2.99"},
                                        {"id": "water", "title": "Water", "description": "$1.99"},
                                    ],
                                },
                            ],
                        },
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "interactive",
                    "interactive": {
                        "type": "list",
                        "body": {"text": "How can we help you today?"},
                        "action": {
                            "button": "Select Option",
                            "sections": [
                                {
                                    "rows": [
                                        {"id": "support", "title": "Customer Support"},
                                        {"id": "sales", "title": "Sales Inquiry"},
                                        {"id": "track", "title": "Track Order"},
                                    ]
                                }
                            ],
                        },
                    },
                },
            ]
        }
    }


def validate_interactive_list_message_send(
    data: dict,
) -> InteractiveListMessageSendRequestValidator:
    """Validate an interactive list message send request dictionary."""
    return InteractiveListMessageSendRequestValidator(**data)
