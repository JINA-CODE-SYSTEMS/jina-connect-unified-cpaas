"""
Utility Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
utility template creation requests.

Based on META's utility template structure:
- name: Template name
- language: Template language code
- category: "utility"
- parameter_format: Parameter format type (NAMED or POSITIONAL)
- components: Array of header, body, footer, and button components
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample,
                                                    BodyTextNamedParam)
from wa.utility.data_model.meta_direct.buttons import (CopyCodeButton,
                                                       FlowButton,
                                                       PhoneNumberButton,
                                                       QuickReplyButton,
                                                       TemplateButton,
                                                       URLButton)
from wa.utility.data_model.meta_direct.buttons_component import \
    ButtonsComponent
from wa.utility.data_model.meta_direct.enums import (HeaderFormat,
                                                     ParameterFormat,
                                                     TemplateCategory,
                                                     TemplateType)
from wa.utility.data_model.meta_direct.footer import FooterComponent
from wa.utility.data_model.meta_direct.header import (HeaderComponent,
                                                      HeaderHandleExample,
                                                      HeaderTextExample)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator

# Union type for all utility template components
UtilityTemplateComponent = Union[
    HeaderComponent, BodyComponent, FooterComponent, ButtonsComponent
]


class UtilityTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API utility template creation request.
    
    Inherits from BaseTemplateValidator which provides:
    - name, language validation
    - template_type (internal, excluded from META API)
    - parameter_format, message_send_ttl_seconds

    Validates the complete template structure including:
    - Template name (lowercase alphanumeric and underscores only)
    - Language code (e.g., 'en', 'en_US')
    - Category (must be "utility")
    - Parameter format (NAMED or POSITIONAL)
    - Components (header, body, footer, buttons)

    Utility templates are typically used for:
    - Order updates
    - Appointment reminders
    - Account updates
    - Payment updates
    - Shipping notifications

    Example usage:
        >>> data = {
        ...     "name": "order_update",
        ...     "language": "en",
        ...     "category": "utility",
        ...     "template_type": "TEXT",  # Internal - not sent to META
        ...     "parameter_format": "NAMED",
        ...     "components": [
        ...         {"type": "header", "format": "TEXT", "text": "Order Update"},
        ...         {"type": "body", "text": "Your order #{{order_id}} is {{status}}.",
        ...          "example": {"body_text_named_params": [
        ...              {"param_name": "order_id", "example": "12345"},
        ...              {"param_name": "status", "example": "shipped"}
        ...          ]}},
        ...         {"type": "footer", "text": "Thank you for your order!"},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "url", "text": "Track Order", "url": "https://example.com/track/{{1}}"}
        ...         ]}
        ...     ]
        ... }
        >>> template = UtilityTemplateRequestValidator(**data)
    """

    # Override category to restrict to utility only
    category: Literal["utility", "UTILITY"] = Field(
        ..., description="Template category (must be 'utility')"
    )
    
    # Override components with utility-specific component types
    components: List[UtilityTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (header, body, footer, buttons)",
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
                                if btn_type == "url":
                                    parsed_buttons.append(URLButton(**btn))
                                elif btn_type == "phone_number":
                                    parsed_buttons.append(PhoneNumberButton(**btn))
                                elif btn_type == "quick_reply":
                                    parsed_buttons.append(QuickReplyButton(**btn))
                                elif btn_type == "copy_code":
                                    parsed_buttons.append(CopyCodeButton(**btn))
                                elif btn_type == "flow":
                                    parsed_buttons.append(FlowButton(**btn))
                                else:
                                    raise ValueError(f"Unknown button type: {btn_type}")
                            else:
                                raise ValueError(
                                    f"Button must be a dictionary, got {type(btn)}"
                                )
                        comp["buttons"] = parsed_buttons
                    parsed.append(ButtonsComponent(**comp))
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_template_structure(self):
        """Validate the overall template structure"""
        component_types = [comp.type for comp in self.components]

        # Body is required for utility templates
        if "body" not in component_types:
            raise ValueError("Body component is required for utility templates")

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
        actual_order = [t for t in component_types if t in expected_order]

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

        if self.parameter_format:
            payload["parameter_format"] = self.parameter_format.value

        for comp in self.components:
            payload["components"].append(comp.model_dump(exclude_none=True))

        return payload

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "order_status_update",
                    "language": "en",
                    "category": "utility",
                    "parameter_format": "NAMED",
                    "components": [
                        {"type": "header", "format": "TEXT", "text": "Order Update"},
                        {
                            "type": "body",
                            "text": "Hello {{customer_name}}, your order #{{order_id}} is now {{status}}. Expected delivery: {{delivery_date}}.",
                            "example": {
                                "body_text_named_params": [
                                    {"param_name": "customer_name", "example": "John"},
                                    {"param_name": "order_id", "example": "ORD-12345"},
                                    {"param_name": "status", "example": "shipped"},
                                    {
                                        "param_name": "delivery_date",
                                        "example": "Feb 5, 2026",
                                    },
                                ]
                            },
                        },
                        {"type": "footer", "text": "Thank you for shopping with us!"},
                        {
                            "type": "buttons",
                            "buttons": [
                                {
                                    "type": "url",
                                    "text": "Track Order",
                                    "url": "https://example.com/track/{{1}}",
                                    "example": ["https://example.com/track/ORD-12345"],
                                },
                                {
                                    "type": "phone_number",
                                    "text": "Contact Support",
                                    "phone_number": "+919876543210",
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


def validate_utility_template(data: dict) -> UtilityTemplateRequestValidator:
    """
    Validate a utility template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        UtilityTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return UtilityTemplateRequestValidator(**data)


def parse_and_validate_utility_template(
    json_str: str,
) -> UtilityTemplateRequestValidator:
    """
    Parse JSON string and validate as utility template.

    Args:
        json_str: JSON string containing template data

    Returns:
        UtilityTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_utility_template(data)
