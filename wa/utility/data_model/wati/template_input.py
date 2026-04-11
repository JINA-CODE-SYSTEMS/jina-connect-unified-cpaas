"""
WATI Template Input Data Model

Pydantic models for WATI template creation payloads.

WATI's template creation API (POST /api/v1/whatsApp/templates) expects
a JSON body with fields like:
    - type, category, subCategory, buttonsType
    - buttons, footer, elementName, language
    - header, body, customParams, creationMethod

Reference: https://docs.wati.io/reference/post_api-v1-whatsapp-templates
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# =========================================================================
# Enums
# =========================================================================


class WATITemplateCategory(str, Enum):
    """WATI template categories (maps to META's categories)."""

    MARKETING = "MARKETING"
    UTILITY = "UTILITY"
    AUTHENTICATION = "AUTHENTICATION"


class WATITemplateSubCategory(str, Enum):
    """WATI template sub-categories."""

    STANDARD = "STANDARD"
    CAROUSEL = "CAROUSEL"
    CATALOG = "CATALOG"
    CHECKOUT_BUTTON = "CHECKOUT_BUTTON"
    ORDER_STATUS = "ORDER_STATUS"
    LIMITED_TIME_OFFER = "LIMITED_TIME_OFFER"
    COUPON_CODE = "COUPON_CODE"
    MPM = "MPM"
    SPM = "SPM"
    PRODUCT_CARD_CAROUSEL = "PRODUCT_CARD_CAROUSEL"


class WATIButtonsType(str, Enum):
    """WATI template button type configurations."""

    NONE = "NONE"
    QUICK_REPLY = "quick_reply"
    CALL_TO_ACTION = "call_to_action"
    QUICK_REPLY_AND_CALL_TO_ACTION = "quick_reply_and_call_to_action"
    ORDER_DETAILS = "order_details"
    CHECKOUT = "checkout"


class WATICreationMethod(int, Enum):
    """Template creation method."""

    HUMAN = 0
    AI = 1
    HUMAN_AND_AI = 2


class WATIHeaderFormat(str, Enum):
    """Header format types for WATI templates."""

    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    NONE = "NONE"


# =========================================================================
# Component Models
# =========================================================================


class WATITemplateHeader(BaseModel):
    """
    Header configuration for a WATI template.

    Supports text headers (with optional variables) and media headers
    (image, video, document).
    """

    format: WATIHeaderFormat = Field(..., description="Header format type")
    text: Optional[str] = Field(None, description="Header text (required for TEXT format, may contain {{variables}})")
    media_url: Optional[str] = Field(None, description="Media URL for IMAGE/VIDEO/DOCUMENT headers")
    example: Optional[str] = Field(None, description="Example value for header variables or media handle")


class WATITemplateButton(BaseModel):
    """
    Button configuration for a WATI template.

    Supports different button types as used in WATI's API.
    """

    type: str = Field(..., description="Button type (e.g., 'url', 'phone_number', 'quick_reply')")
    text: str = Field(..., min_length=1, max_length=25, description="Button label text")
    url: Optional[str] = Field(None, description="URL for URL-type buttons")
    phone_number: Optional[str] = Field(None, description="Phone number for phone_number-type buttons")
    example: Optional[str] = Field(None, description="Example value for dynamic URL parameters")

    @field_validator("text")
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Button text cannot be empty")
        return v.strip()


class WATITemplateCustomParam(BaseModel):
    """
    Custom parameter definition for a WATI template.

    Used to define variables in the template body (e.g., {{name}}, {{order_id}}).
    """

    name: str = Field(..., description="Parameter name")
    value: Optional[str] = Field(None, description="Default or example value for the parameter")


# =========================================================================
# Main Template Input Model
# =========================================================================


class WATITemplateInput(BaseModel):
    """
    Complete template creation payload for WATI API.

    This model maps to the JSON body expected by:
    POST https://{WATI_API_ENDPOINT}/api/v1/whatsApp/templates

    Usage:
        template = WATITemplateInput(
            elementName="welcome_msg",
            language="en",
            category=WATITemplateCategory.MARKETING,
            body="Hello {{1}}, welcome to our store!",
        )
        payload = template.to_wati_payload()
    """

    # Core fields
    type: str = Field(default="template", description="Template type, typically 'template'")
    category: WATITemplateCategory = Field(..., description="Template category (MARKETING, UTILITY, AUTHENTICATION)")
    subCategory: Optional[WATITemplateSubCategory] = Field(
        default=WATITemplateSubCategory.STANDARD, description="Template sub-category"
    )
    elementName: str = Field(
        ..., min_length=1, max_length=512, description="Internal template name (alphanumeric and underscores)"
    )
    language: str = Field(..., min_length=2, max_length=10, description="Language code (e.g., 'en', 'es', 'fr')")

    # Content
    body: str = Field(
        ..., min_length=1, max_length=1024, description="Body text of the template, can include variables like {{name}}"
    )
    footer: Optional[str] = Field(None, max_length=60, description="Footer text of the template")
    header: Optional[WATITemplateHeader] = Field(None, description="Header configuration")

    # Buttons
    buttonsType: WATIButtonsType = Field(default=WATIButtonsType.NONE, description="Type of buttons configuration")
    buttons: Optional[List[WATITemplateButton]] = Field(None, description="List of button configurations")

    # Parameters
    customParams: Optional[List[WATITemplateCustomParam]] = Field(
        None, description="Custom parameter definitions for template variables"
    )

    # Metadata
    creationMethod: WATICreationMethod = Field(
        default=WATICreationMethod.HUMAN, description="Template creation method (0=HUMAN, 1=AI, 2=HUMAN_AND_AI)"
    )

    # Raw payload for debugging
    raw_payload: Optional[Dict[str, Any]] = Field(None, exclude=True, description="Raw payload for debugging")

    @field_validator("elementName")
    @classmethod
    def validate_element_name(cls, v):
        """Template names must be lowercase alphanumeric with underscores."""
        import re

        if not v or not v.strip():
            raise ValueError("Template name cannot be empty")
        v = v.strip().lower()
        if not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError("Template name must contain only lowercase letters, numbers, and underscores")
        return v

    def to_wati_payload(self) -> dict:
        """
        Convert to WATI API payload dict.

        Returns:
            dict: JSON-serializable dict suitable for WATI template creation API.
        """
        payload = self.model_dump(
            exclude_none=True,
            exclude={"raw_payload"},
        )
        # Convert header to dict format if present
        if self.header:
            payload["header"] = self.header.model_dump(exclude_none=True)
        return payload

    @classmethod
    def from_webhook_payload(cls, payload: Dict[str, Any]) -> "WATITemplateInput":
        """
        Factory method to create WATITemplateInput from a WATI webhook or API response.

        Args:
            payload: Raw template data from WATI.

        Returns:
            WATITemplateInput instance.
        """
        try:
            return cls(raw_payload=payload, **payload)
        except Exception:
            # If parsing fails, try with minimal required fields
            return cls(
                elementName=payload.get("elementName", "unknown"),
                language=payload.get("language", "en"),
                category=payload.get("category", WATITemplateCategory.UTILITY),
                body=payload.get("body", ""),
                raw_payload=payload,
            )
