"""
Limited Time Offer (LTO) Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Limited Time Offer template creation requests.

Based on META's LTO template structure:
- name: Template name
- language: Template language code
- category: "marketing" (LTO templates are marketing category)
- components: Array of header, limited_time_offer, body, and button components
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.buttons import (CopyCodeButton,
                                                       PhoneNumberButton,
                                                       QuickReplyButton,
                                                       TemplateButton,
                                                       URLButton)
from wa.utility.data_model.meta_direct.buttons_component import \
    ButtonsComponent
from wa.utility.data_model.meta_direct.header import (HeaderComponent,
                                                      HeaderHandleExample,
                                                      HeaderTextExample)

# ============================================================================
# Limited Time Offer Component
# ============================================================================


class LimitedTimeOfferDetails(BaseModel):
    """Details for the limited time offer"""
    text: str = Field(
        ...,
        min_length=1,
        max_length=16,
        description="Limited time offer text (max 16 characters)",
    )
    has_expiration: bool = Field(
        ...,
        description="Whether the offer has an expiration countdown timer",
    )


class LimitedTimeOfferComponent(BaseModel):
    """Limited Time Offer component for LTO templates"""
    type: Literal["limited_time_offer", "LIMITED_TIME_OFFER"] = "limited_time_offer"
    limited_time_offer: LimitedTimeOfferDetails = Field(
        ...,
        description="Limited time offer details with text and expiration flag",
    )


# ============================================================================
# LTO-specific Button Component
# ============================================================================


class LTOButtonsComponent(BaseModel):
    """Buttons component specifically for LTO templates - typically copy_code + url"""
    type: Literal["buttons"] = "buttons"
    buttons: List[TemplateButton] = Field(
        ..., min_length=1, max_length=10, description="List of buttons"
    )

    @field_validator("buttons")
    @classmethod
    def validate_buttons(cls, v):
        if not v:
            raise ValueError("At least one button is required in buttons component")

        # Check that at least one copy_code button exists for LTO
        copy_code_buttons = [
            b
            for b in v
            if isinstance(b, CopyCodeButton)
            or (isinstance(b, dict) and b.get("type") == "copy_code")
        ]
        if not copy_code_buttons:
            raise ValueError(
                "LTO templates must have at least one copy_code type button"
            )

        # Count button types for validation
        copy_code_count = len(copy_code_buttons)
        if copy_code_count > 1:
            raise ValueError("Only one copy_code button is allowed per template")

        # URL buttons validation
        url_count = sum(
            1
            for b in v
            if isinstance(b, URLButton)
            or (isinstance(b, dict) and b.get("type") == "url")
        )
        if url_count > 2:
            raise ValueError("Maximum 2 URL buttons allowed")

        return v


# Union type for LTO template components
LTOTemplateComponent = Union[
    HeaderComponent, LimitedTimeOfferComponent, BodyComponent, LTOButtonsComponent
]


class LTOTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API Limited Time Offer (LTO) template creation request.

    LTO templates are used to promote time-sensitive offers with optional
    countdown timers and offer codes.

    Validates the complete template structure including:
    - Template name (lowercase alphanumeric and underscores only)
    - Language code (e.g., 'en', 'en_US')
    - Category (must be "marketing" for LTO templates)
    - Components (header, limited_time_offer, body, buttons with copy_code)

    Example usage:
        >>> data = {
        ...     "name": "flash_sale_offer",
        ...     "language": "en",
        ...     "category": "marketing",
        ...     "components": [
        ...         {"type": "header", "format": "image",
        ...          "example": {"header_handle": ["4::aW1hZ2..."]}},
        ...         {"type": "limited_time_offer",
        ...          "limited_time_offer": {"text": "Limited Time!", "has_expiration": True}},
        ...         {"type": "body", "text": "Get {{1}}% off! Use code now!",
        ...          "example": {"body_text": [["50"]]}},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "copy_code", "example": "FLASH50"},
        ...             {"type": "url", "text": "Shop Now", "url": "https://example.com/sale"}
        ...         ]}
        ...     ]
        ... }
        >>> template = LTOTemplateRequestValidator(**data)
    """

    category: Literal["MARKETING", "marketing"] = Field(
        ...,
        description="Template category (must be 'marketing' for LTO templates)",
    )
    components: List[LTOTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (header, limited_time_offer, body, buttons)",
    )

    @field_validator("components", mode="before")
    @classmethod
    def parse_components(cls, v):
        """Parse component dictionaries into appropriate component types"""
        if not v:
            raise ValueError("At least one component is required")

        parsed = []
        for comp in v:
            if isinstance(comp, BaseModel):
                parsed.append(comp)
                continue

            if not isinstance(comp, dict):
                raise ValueError(f"Component must be a dictionary, got {type(comp)}")

            comp_type = comp.get("type")
            if not comp_type:
                raise ValueError("Component must have a 'type' field")

            # Normalize type to lowercase for comparison
            comp_type_lower = comp_type.lower()

            try:
                if comp_type_lower == "header":
                    # Normalize format to uppercase
                    if "format" in comp and isinstance(comp["format"], str):
                        comp["format"] = comp["format"].upper()
                    # Parse example if present
                    if "example" in comp and isinstance(comp["example"], dict):
                        if "header_handle" in comp["example"]:
                            comp["example"] = HeaderHandleExample(**comp["example"])
                        elif "header_text" in comp["example"]:
                            comp["example"] = HeaderTextExample(**comp["example"])
                    parsed.append(HeaderComponent(**comp))
                elif comp_type_lower == "limited_time_offer":
                    # Parse limited_time_offer details
                    if "limited_time_offer" in comp and isinstance(
                        comp["limited_time_offer"], dict
                    ):
                        comp["limited_time_offer"] = LimitedTimeOfferDetails(
                            **comp["limited_time_offer"]
                        )
                    parsed.append(LimitedTimeOfferComponent(**comp))
                elif comp_type_lower == "body":
                    # Parse example if present
                    if "example" in comp and isinstance(comp["example"], dict):
                        comp["example"] = BodyTextExample(**comp["example"])
                    parsed.append(BodyComponent(**comp))
                elif comp_type_lower == "buttons":
                    # Parse buttons
                    if "buttons" in comp:
                        parsed_buttons = []
                        for btn in comp["buttons"]:
                            if isinstance(btn, BaseModel):
                                parsed_buttons.append(btn)
                            elif isinstance(btn, dict):
                                btn_type = btn.get("type")
                                if btn_type == "copy_code":
                                    parsed_buttons.append(CopyCodeButton(**btn))
                                elif btn_type == "url":
                                    parsed_buttons.append(URLButton(**btn))
                                elif btn_type == "quick_reply":
                                    parsed_buttons.append(QuickReplyButton(**btn))
                                elif btn_type == "phone_number":
                                    parsed_buttons.append(PhoneNumberButton(**btn))
                                else:
                                    raise ValueError(
                                        f"Unknown or unsupported button type for LTO template: {btn_type}. "
                                        f"Allowed types: 'copy_code', 'url', 'quick_reply', 'phone_number'."
                                    )
                            else:
                                raise ValueError(
                                    f"Button must be a dictionary, got {type(btn)}"
                                )
                        comp["buttons"] = parsed_buttons
                    parsed.append(LTOButtonsComponent(**comp))
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_template_structure(self):
        """Validate the overall template structure"""
        component_types = [comp.type.lower() for comp in self.components]

        # limited_time_offer is required for LTO templates
        if "limited_time_offer" not in component_types:
            raise ValueError(
                "limited_time_offer component is required for LTO templates"
            )

        # Body is required for LTO templates
        if "body" not in component_types:
            raise ValueError("Body component is required for LTO templates")

        # Buttons with copy_code is required
        if "buttons" not in component_types:
            raise ValueError(
                "Buttons component with copy_code button is required for LTO templates"
            )

        # Check for duplicate components
        if component_types.count("header") > 1:
            raise ValueError("Only one header component is allowed")
        if component_types.count("limited_time_offer") > 1:
            raise ValueError("Only one limited_time_offer component is allowed")
        if component_types.count("body") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("buttons") > 1:
            raise ValueError("Only one buttons component is allowed")

        # Validate component order: header -> limited_time_offer -> body -> buttons
        expected_order = ["header", "limited_time_offer", "body", "buttons"]
        current_order = [t for t in expected_order if t in component_types]
        actual_order = [
            t.lower() for t in component_types if t.lower() in expected_order
        ]

        if current_order != actual_order:
            raise ValueError(
                f"Components must be in order: header -> limited_time_offer -> body -> buttons. "
                f"Got: {actual_order}"
            )

        return self

    def to_meta_payload(self) -> dict:
        """Convert validated template to META API payload format"""
        payload = {
            "name": self.name,
            "language": self.language,
            "category": self.category.upper(),
            "components": [],
        }

        for comp in self.components:
            payload["components"].append(comp.model_dump(exclude_none=True))

        return payload

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "flash_sale_offer",
                    "language": "en",
                    "category": "marketing",
                    "components": [
                        {
                            "type": "header",
                            "format": "image",
                            "example": {"header_handle": ["4::aW1hZ2UvaGFuZGxl"]},
                        },
                        {
                            "type": "limited_time_offer",
                            "limited_time_offer": {
                                "text": "Limited Time!",
                                "has_expiration": True,
                            },
                        },
                        {
                            "type": "body",
                            "text": "🔥 Flash Sale! Get {{1}}% off everything! Use the code below before time runs out!",
                            "example": {"body_text": [["50"]]},
                        },
                        {
                            "type": "buttons",
                            "buttons": [
                                {"type": "copy_code", "example": "FLASH50"},
                                {
                                    "type": "url",
                                    "text": "Shop Now",
                                    "url": "https://example.com/sale",
                                },
                            ],
                        },
                    ],
                }
            ]
        }
    }


# ============================================================================
# Utility Functions
# ============================================================================


def validate_lto_template(data: dict) -> LTOTemplateRequestValidator:
    """
    Validate a Limited Time Offer template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        LTOTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return LTOTemplateRequestValidator(**data)


def parse_and_validate_lto_template(json_str: str) -> LTOTemplateRequestValidator:
    """
    Parse JSON string and validate as LTO template.

    Args:
        json_str: JSON string containing template data

    Returns:
        LTOTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_lto_template(data)
