"""
Buttons Component for META Direct API Template Validation
"""

from typing import List, Literal

from pydantic import BaseModel, Field, field_validator

from .buttons import (CopyCodeButton, FlowButton, PhoneNumberButton,
                      QuickReplyButton, TemplateButton, URLButton)


class ButtonsComponent(BaseModel):
    """Buttons component - optional"""
    type: Literal["buttons"] = "buttons"
    buttons: List[TemplateButton] = Field(
        ..., min_length=1, max_length=10, description="List of buttons"
    )

    @field_validator("buttons")
    @classmethod
    def validate_buttons(cls, v):
        if not v:
            raise ValueError("At least one button is required in buttons component")
        if len(v) > 10:
            raise ValueError("Maximum 10 buttons allowed per template")

        # Count button types for validation
        url_count = sum(
            1
            for b in v
            if isinstance(b, URLButton)
            or (isinstance(b, dict) and b.get("type") == "url")
        )
        phone_count = sum(
            1
            for b in v
            if isinstance(b, PhoneNumberButton)
            or (isinstance(b, dict) and b.get("type") == "phone_number")
        )
        quick_reply_count = sum(
            1
            for b in v
            if isinstance(b, QuickReplyButton)
            or (isinstance(b, dict) and b.get("type") == "quick_reply")
        )

        # META limits: max 2 URL buttons, max 1 phone button
        if url_count > 2:
            raise ValueError("Maximum 2 URL buttons allowed")
        if phone_count > 1:
            raise ValueError("Maximum 1 phone number button allowed")
        if quick_reply_count > 3:
            raise ValueError("Maximum 3 quick reply buttons allowed")

        return v
