"""
Base Template Validator for META Direct API

This module provides the base class for all META Direct API template validators.
Derived classes should override category and components with specific types.
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from wa.utility.data_model.meta_direct.body import BodyComponent
from wa.utility.data_model.meta_direct.buttons_component import ButtonsComponent
from wa.utility.data_model.meta_direct.enums import ParameterFormat, TemplateType
from wa.utility.data_model.meta_direct.footer import FooterComponent
from wa.utility.data_model.meta_direct.header import HeaderComponent


class BaseTemplateValidator(BaseModel):
    """
    Base class for META Direct API template validators.

    Provides common fields and validators that all template types share:
    - name: Template name (alphanumeric and underscores)
    - language: Language code (e.g., 'en', 'en_US')
    - parameter_format: NAMED or POSITIONAL
    - template_type: Internal field for storage (NOT sent to META API)

    Derived classes should:
    1. Override `category` with specific Literal type
    2. Override `components` with specific component union type
    3. Add any category-specific validators
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name (alphanumeric and underscores only)",
    )
    language: str = Field(
        ...,
        min_length=2,
        max_length=10,
        description="Template language code (e.g., 'en', 'en_US')",
    )
    parameter_format: Optional[ParameterFormat] = Field(
        default=None, description="Parameter format type (NAMED or POSITIONAL)"
    )
    category: Literal["marketing", "utility", "authentication", "MARKETING", "UTILITY", "AUTHENTICATION"]
    components: List[Union[HeaderComponent, BodyComponent, FooterComponent, ButtonsComponent]] = Field(
        ..., min_length=1, description="Template components"
    )
    message_send_ttl_seconds: Optional[int] = Field(default=43200, description="Message send TTL in seconds")

    # Internal field - NOT sent to META API, used for internal storage/UI
    # This tracks the actual content type (TEXT, IMAGE, VIDEO, etc.)
    # while META only uses category (MARKETING, UTILITY, AUTHENTICATION)
    template_type: Optional[TemplateType] = Field(
        default=TemplateType.TEXT,
        exclude=True,  # Exclude from model_dump() when sending to META API
        description="Internal template type for storage (TEXT, IMAGE, VIDEO, DOCUMENT, CAROUSEL, etc.)",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        """Validate template name - must be lowercase alphanumeric and underscores"""
        if not v or not v.strip():
            raise ValueError("Template name cannot be empty")
        v = v.strip().lower()
        # META requires lowercase alphanumeric and underscores
        if not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError("Template name must contain only lowercase letters, numbers, and underscores")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v):
        """Validate language code format"""
        if not v or not v.strip():
            raise ValueError("Language code cannot be empty")
        # Common language codes validation
        valid_pattern = r"^[a-z]{2}(_[A-Z]{2})?$"
        if not re.match(valid_pattern, v):
            raise ValueError("Language code must be in format 'xx' or 'xx_XX' (e.g., 'en', 'en_US')")
        return v

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)
