"""
Button Models for META Direct API Template Validation
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


class URLButton(BaseModel):
    """URL button component"""
    type: Literal["url"] = "url"
    text: str = Field(..., min_length=1, max_length=25, description="Button label text")
    url: str = Field(..., description="URL to open when button is clicked")
    example: Optional[List[str]] = Field(
        None, description="Example URL values for dynamic parameters"
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("URL button text cannot be empty")
        return v.strip()

    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        if not v:
            raise ValueError("URL is required for URL button")
        # Basic URL validation
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class PhoneNumberButton(BaseModel):
    """Phone number button component"""
    type: Literal["phone_number"] = "phone_number"
    text: str = Field(..., min_length=1, max_length=25, description="Button label text")
    phone_number: str = Field(..., description="Phone number in international format")

    @field_validator("text")
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Phone button text cannot be empty")
        return v.strip()

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v):
        if not v:
            raise ValueError("Phone number is required")
        # Basic phone validation - should start with + and contain digits
        cleaned = re.sub(r"[\s\-\(\)]", "", v)
        if not re.match(r"^\+?[0-9]{10,15}$", cleaned):
            raise ValueError(
                "Invalid phone number format. Use international format (e.g., +919876543210)"
            )
        return v


class QuickReplyButton(BaseModel):
    """Quick reply button component"""
    type: Literal["quick_reply"] = "quick_reply"
    text: str = Field(..., min_length=1, max_length=25, description="Button label text")

    @field_validator("text")
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Quick reply button text cannot be empty")
        return v.strip()


class CopyCodeButton(BaseModel):
    """Copy code button component (for OTP/codes)"""
    type: Literal["copy_code"] = "copy_code"
    example: str = Field(..., description="Example code to copy")


class FlowButton(BaseModel):
    """Flow button component"""
    type: Literal["flow"] = "flow"
    text: str = Field(..., min_length=1, max_length=25, description="Button label text")
    flow_id: str = Field(..., description="Flow ID to trigger")
    flow_action: Optional[str] = Field("navigate", description="Flow action type")
    navigate_screen: Optional[str] = Field(None, description="Screen to navigate to")

class CallPermissionButton(BaseModel):
    """Call Permission button component"""
    type: Literal["call_permission"] = "call_permission_request"


class CatalogButton(BaseModel):
    """Catalog button component for product catalog templates"""
    type: Literal["CATALOG"] = "CATALOG"
    text: str = Field(
        ..., min_length=1, max_length=25, description="Button label text (e.g., 'View catalog')"
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Catalog button text cannot be empty")
        return v.strip()


class OrderDetailsButton(BaseModel):
    """Order Details button for checkout/payment templates.

    Meta API requires this as the sole button in the BUTTONS component.
    - UTILITY category: text must be "Review and Pay"
    - MARKETING category: text must be "Buy now"
    """
    type: Literal["ORDER_DETAILS", "order_details"] = "ORDER_DETAILS"
    text: str = Field(
        ..., min_length=1, max_length=25,
        description="Button label text ('Review and Pay' for UTILITY, 'Buy now' for MARKETING)"
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Order details button text cannot be empty")
        v = v.strip()
        allowed = ("Review and Pay", "Buy now")
        if v not in allowed:
            raise ValueError(
                f"Order details button text must be one of {allowed}, got '{v}'"
            )
        return v


# Union type for all button types
TemplateButton = Union[
    URLButton,
    PhoneNumberButton,
    QuickReplyButton,
    CopyCodeButton,
    FlowButton,
    CallPermissionButton,
    CatalogButton,
    OrderDetailsButton,
]
