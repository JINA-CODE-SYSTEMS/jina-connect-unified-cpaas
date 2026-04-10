"""
Coupon Code Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
coupon code template creation requests.

Based on META's coupon code template structure:
- name: Template name
- language: Template language code
- category: "marketing" (coupon code templates are marketing category)
- components: Array of header (optional), body, and button components with copy_code
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample,
                                                    BodyTextNamedParam)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.buttons import (CopyCodeButton,
                                                       PhoneNumberButton,
                                                       QuickReplyButton,
                                                       TemplateButton,
                                                       URLButton)
from wa.utility.data_model.meta_direct.buttons_component import \
    ButtonsComponent
from wa.utility.data_model.meta_direct.enums import (HeaderFormat,
                                                     ParameterFormat,
                                                     TemplateCategory)
from wa.utility.data_model.meta_direct.footer import FooterComponent
from wa.utility.data_model.meta_direct.header import (HeaderComponent,
                                                      HeaderHandleExample,
                                                      HeaderTextExample)

# ============================================================================
# Coupon Code-specific Button Component
# ============================================================================


class CouponCodeButtonsComponent(BaseModel):
    """Buttons component specifically for coupon code templates - must contain copy_code button"""
    type: Literal["buttons"] = "buttons"
    buttons: List[TemplateButton] = Field(
        ..., min_length=1, max_length=10, description="List of buttons"
    )

    @field_validator("buttons")
    @classmethod
    def validate_buttons(cls, v):
        if not v:
            raise ValueError("At least one button is required in buttons component")

        # Check that at least one copy_code button exists
        copy_code_buttons = [
            b
            for b in v
            if isinstance(b, CopyCodeButton)
            or (isinstance(b, dict) and b.get("type") == "copy_code")
        ]
        if not copy_code_buttons:
            raise ValueError(
                "Coupon code templates must have at least one copy_code type button"
            )

        # Count button types for validation
        copy_code_count = len(copy_code_buttons)
        if copy_code_count > 1:
            raise ValueError("Only one copy_code button is allowed per template")

        # Quick reply buttons are allowed alongside copy_code
        quick_reply_count = sum(
            1
            for b in v
            if isinstance(b, QuickReplyButton)
            or (isinstance(b, dict) and b.get("type") == "quick_reply")
        )
        if quick_reply_count > 3:
            raise ValueError("Maximum 3 quick reply buttons allowed")

        return v


# Union type for coupon code template components
CouponCodeTemplateComponent = Union[
    HeaderComponent, BodyComponent, FooterComponent, CouponCodeButtonsComponent
]


class CouponCodeTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API coupon code template creation request.

    Coupon code templates are used to share discount codes with customers.
    They must include a copy_code button that allows users to copy the code.

    Validates the complete template structure including:
    - Template name (lowercase alphanumeric and underscores only)
    - Language code (e.g., 'en', 'en_US')
    - Category (must be "marketing" for coupon code templates)
    - Components (header optional, body required, copy_code button required)

    Example usage:
        >>> data = {
        ...     "name": "discount_coupon",
        ...     "language": "en",
        ...     "category": "marketing",
        ...     "components": [
        ...         {"type": "header", "format": "text", "text": "Special Offer!"},
        ...         {"type": "body", "text": "Use code to get {{1}}% off!",
        ...          "example": {"body_text": [["20"]]}},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "quick_reply", "text": "Shop Now"},
        ...             {"type": "copy_code", "example": "SAVE20"}
        ...         ]}
        ...     ]
        ... }
        >>> template = CouponCodeTemplateRequestValidator(**data)
    """

    category: Literal["MARKETING", "marketing"] = Field(
        ...,
        description="Template category (must be 'marketing' for coupon code templates)",
    )
    components: List[CouponCodeTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (header, body, buttons with copy_code)",
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
                elif comp_type_lower == "body":
                    # Parse example if present
                    if "example" in comp and isinstance(comp["example"], dict):
                        comp["example"] = BodyTextExample(**comp["example"])
                    parsed.append(BodyComponent(**comp))
                elif comp_type_lower == "footer":
                    parsed.append(FooterComponent(**comp))
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
                                elif btn_type == "quick_reply":
                                    parsed_buttons.append(QuickReplyButton(**btn))
                                elif btn_type == "url":
                                    parsed_buttons.append(URLButton(**btn))
                                elif btn_type == "phone_number":
                                    parsed_buttons.append(PhoneNumberButton(**btn))
                                else:
                                    raise ValueError(
                                        f"Unknown or unsupported button type for coupon code template: {btn_type}. "
                                        f"Only 'copy_code', 'quick_reply', 'url', and 'phone_number' buttons are allowed."
                                    )
                            else:
                                raise ValueError(
                                    f"Button must be a dictionary, got {type(btn)}"
                                )
                        comp["buttons"] = parsed_buttons
                    parsed.append(CouponCodeButtonsComponent(**comp))
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_template_structure(self):
        """Validate the overall template structure"""
        component_types = [comp.type.lower() for comp in self.components]

        # Body is required for coupon code templates
        if "body" not in component_types:
            raise ValueError("Body component is required for coupon code templates")

        # Buttons with copy_code is required
        if "buttons" not in component_types:
            raise ValueError(
                "Buttons component with copy_code button is required for coupon code templates"
            )

        # Check for duplicate components
        if component_types.count("header") > 1:
            raise ValueError("Only one header component is allowed")
        if component_types.count("body") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("footer") > 1:
            raise ValueError("Only one footer component is allowed")
        if component_types.count("buttons") > 1:
            raise ValueError("Only one buttons component is allowed")

        # Validate component order: header -> body -> footer -> buttons
        expected_order = ["header", "body", "footer", "buttons"]
        current_order = [t for t in expected_order if t in component_types]
        actual_order = [t.lower() for t in component_types if t.lower() in expected_order]

        if current_order != actual_order:
            raise ValueError(
                f"Components must be in order: header -> body -> footer -> buttons. "
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
                    "name": "discount_code_promo",
                    "language": "en",
                    "category": "marketing",
                    "components": [
                        {"type": "header", "format": "text", "text": "Special Offer!"},
                        {
                            "type": "body",
                            "text": "Hi! Use this exclusive code to get {{1}}% off your next purchase. Valid until {{2}}.",
                            "example": {"body_text": [["20", "December 31"]]},
                        },
                        {
                            "type": "buttons",
                            "buttons": [
                                {"type": "quick_reply", "text": "Shop Now"},
                                {"type": "copy_code", "example": "SAVE20"},
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


def validate_coupon_code_template(data: dict) -> CouponCodeTemplateRequestValidator:
    """
    Validate a coupon code template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        CouponCodeTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return CouponCodeTemplateRequestValidator(**data)


def parse_and_validate_coupon_code_template(
    json_str: str,
) -> CouponCodeTemplateRequestValidator:
    """
    Parse JSON string and validate as coupon code template.

    Args:
        json_str: JSON string containing template data

    Returns:
        CouponCodeTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_coupon_code_template(data)
