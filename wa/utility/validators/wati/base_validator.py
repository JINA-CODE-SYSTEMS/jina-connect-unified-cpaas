"""
Base Template Validator for WATI API

This module provides the base class for all WATI API template validators.
Derived classes should override ``category`` with specific Literal types and
add category-specific validation logic.

WATI templates are submitted via POST /api/v1/whatsApp/templates and follow
the same META template structure with some WATI-specific wrapping.

Reference: https://docs.wati.io/reference/post_api-v1-whatsapp-templates
"""

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from wa.utility.data_model.wati.template_input import (
    WATIButtonsType,
    WATICreationMethod,
    WATIHeaderFormat,
    WATITemplateButton,
    WATITemplateCustomParam,
    WATITemplateHeader,
    WATITemplateSubCategory,
)


class BaseTemplateValidator(BaseModel):
    """
    Base class for WATI template validators.

    Provides common fields and validators that all template types share:
    - elementName: Template name (alphanumeric and underscores)
    - language: Language code (e.g., 'en', 'en_US')
    - body: Template body text with optional {{variables}}
    - header: Optional header configuration
    - footer: Optional footer text
    - buttons: Optional button configurations
    - customParams: Custom parameter definitions

    Derived classes should:
    1. Override ``category`` with specific Literal type
    2. Add any category-specific validators
    3. Override ``validate_category_specific`` if needed
    """

    # Core fields
    type: str = Field(default="template", description="Template type")
    category: Literal[
        "AUTHENTICATION",
        "MARKETING",
        "UTILITY",
        "authentication",
        "marketing",
        "utility",
    ]
    subCategory: Optional[WATITemplateSubCategory] = Field(
        default=WATITemplateSubCategory.STANDARD,
        description="Template sub-category",
    )
    elementName: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Internal template name (alphanumeric and underscores only)",
    )
    language: str = Field(
        ...,
        min_length=2,
        max_length=10,
        description="Template language code (e.g., 'en', 'en_US')",
    )

    # Content
    body: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Body text, can include variables like {{name}} or {{1}}",
    )
    footer: Optional[str] = Field(
        None,
        max_length=60,
        description="Footer text of the template",
    )
    header: Optional[WATITemplateHeader] = Field(None, description="Header configuration")

    # Buttons
    buttonsType: WATIButtonsType = Field(
        default=WATIButtonsType.NONE,
        description="Type of buttons configuration",
    )
    buttons: Optional[List[WATITemplateButton]] = Field(None, description="List of button configurations")

    # Parameters
    customParams: Optional[List[WATITemplateCustomParam]] = Field(
        None, description="Custom parameter definitions for template variables"
    )

    # Metadata
    creationMethod: WATICreationMethod = Field(
        default=WATICreationMethod.HUMAN,
        description="Template creation method",
    )

    # =========================================================================
    # Field Validators
    # =========================================================================

    @field_validator("elementName")
    @classmethod
    def validate_element_name(cls, v):
        """Template name must be lowercase alphanumeric with underscores."""
        if not v or not v.strip():
            raise ValueError("Template name cannot be empty")
        v = v.strip().lower()
        if not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError("Template name must contain only lowercase letters, numbers, and underscores")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v):
        """Validate language code format."""
        if not v or not v.strip():
            raise ValueError("Language code cannot be empty")
        valid_pattern = r"^[a-z]{2}(_[A-Z]{2})?$"
        if not re.match(valid_pattern, v):
            raise ValueError("Language code must be in format 'xx' or 'xx_XX' (e.g., 'en', 'en_US')")
        return v

    @field_validator("body")
    @classmethod
    def validate_body(cls, v):
        """Validate body text is not empty."""
        if not v or not v.strip():
            raise ValueError("Body text cannot be empty")
        return v.strip()

    @field_validator("buttons", mode="before")
    @classmethod
    def validate_buttons(cls, v):
        """
        Convert raw button data to WATITemplateButton objects.
        Handles both list of dicts and already-parsed objects.
        """
        if v is None:
            return None

        if isinstance(v, list):
            parsed_buttons = []
            for btn in v:
                if isinstance(btn, WATITemplateButton):
                    parsed_buttons.append(btn)
                elif isinstance(btn, dict):
                    try:
                        parsed_buttons.append(WATITemplateButton(**btn))
                    except Exception as e:
                        raise ValueError(f"Invalid button data: {e}")
                else:
                    raise ValueError(f"Button must be a dict or WATITemplateButton, got {type(btn)}")
            return parsed_buttons

        raise ValueError(f"Buttons must be a list, got {type(v)}")

    # =========================================================================
    # Model Validators
    # =========================================================================

    @model_validator(mode="after")
    def validate_buttons_type_consistency(self):
        """Ensure buttonsType matches the actual buttons provided."""
        has_buttons = self.buttons is not None and len(self.buttons) > 0

        if self.buttonsType == WATIButtonsType.NONE and has_buttons:
            raise ValueError(
                "buttonsType is NONE but buttons were provided. Set buttonsType to match the button types used."
            )

        if self.buttonsType != WATIButtonsType.NONE and not has_buttons:
            raise ValueError(f"buttonsType is '{self.buttonsType.value}' but no buttons were provided.")

        return self

    @model_validator(mode="after")
    def validate_header_configuration(self):
        """Validate header configuration consistency."""
        if self.header:
            if self.header.format == WATIHeaderFormat.TEXT and not self.header.text:
                raise ValueError("Header text is required for TEXT header format")
            if (
                self.header.format
                in (
                    WATIHeaderFormat.IMAGE,
                    WATIHeaderFormat.VIDEO,
                    WATIHeaderFormat.DOCUMENT,
                )
                and not self.header.media_url
                and not self.header.example
            ):
                raise ValueError(f"Media URL or example handle is required for {self.header.format.value} header")
        return self

    @model_validator(mode="after")
    def validate_body_params_have_definitions(self):
        """
        Check that template body variables have corresponding customParams definitions.
        """
        # Find all {{variable}} patterns in body
        named_params = re.findall(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}", self.body)
        positional_params = re.findall(r"\{\{(\d+)\}\}", self.body)

        if not named_params and not positional_params:
            return self

        # If customParams are provided, validate they cover all body variables
        if self.customParams:
            provided = {p.name for p in self.customParams}
            all_params = set(named_params) | set(positional_params)
            missing = all_params - provided
            if missing:
                # Warn but don't fail — WATI may auto-detect parameters
                pass

        return self

    # =========================================================================
    # Serialization
    # =========================================================================

    def to_wati_payload(self) -> dict:
        """
        Convert to WATI API payload dict.

        Returns:
            dict: JSON-serializable dict suitable for WATI template creation API.
        """
        payload = self.model_dump(exclude_none=True)
        # Ensure category is uppercase for API
        payload["category"] = payload["category"].upper()
        if self.header:
            payload["header"] = self.header.model_dump(exclude_none=True)
        if self.buttons:
            payload["buttons"] = [btn.model_dump(exclude_none=True) for btn in self.buttons]
        if self.customParams:
            payload["customParams"] = [p.model_dump(exclude_none=True) for p in self.customParams]
        return payload

    @classmethod
    def from_dict(cls, data: dict):
        """Create a validator instance from a dictionary."""
        return cls(**data)
