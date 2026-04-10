"""
Footer Component for META Direct API Template Validation
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class FooterComponent(BaseModel):
    """Footer component - optional"""
    type: Literal["footer"] = "footer"
    text: str = Field(..., min_length=1, max_length=60, description="Footer text")

    @field_validator("text")
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Footer text cannot be empty")
        return v.strip()
