"""
Catalog Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
catalog template creation requests.

Based on META's catalog template structure:
- name: Template name
- language: Template language code
- category: "MARKETING" (catalog templates are marketing category)
- components: Array of body, footer, and catalog button components
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample,
                                                    BodyTextNamedParam)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.buttons import (CatalogButton,
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
# Catalog-specific Button Component
# ============================================================================


class CatalogButtonsComponent(BaseModel):
    """Buttons component specifically for catalog templates - must contain CATALOG button"""
    type: Literal["BUTTONS"] = "BUTTONS"
    buttons: List[TemplateButton] = Field(
        ..., min_length=1, max_length=10, description="List of buttons"
    )

    @field_validator("buttons")
    @classmethod
    def validate_buttons(cls, v):
        if not v:
            raise ValueError("At least one button is required in buttons component")

        # Check that at least one CATALOG button exists
        catalog_buttons = [
            b
            for b in v
            if isinstance(b, CatalogButton)
            or (isinstance(b, dict) and b.get("type") == "CATALOG")
        ]
        if not catalog_buttons:
            raise ValueError(
                "Catalog templates must have at least one CATALOG type button"
            )

        # Count button types for validation
        catalog_count = len(catalog_buttons)
        if catalog_count > 1:
            raise ValueError("Only one CATALOG button is allowed per template")

        return v


# Union type for catalog template components
CatalogTemplateComponent = Union[
    HeaderComponent, BodyComponent, FooterComponent, CatalogButtonsComponent
]


class CatalogTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API catalog template creation request.

    Catalog templates are used to showcase products from your WhatsApp catalog.
    They must include a CATALOG button that opens the business catalog.

    Validates the complete template structure including:
    - Template name (lowercase alphanumeric and underscores only)
    - Language code (e.g., 'en', 'en_US')
    - Category (must be "MARKETING" for catalog templates)
    - Components (body required, footer optional, CATALOG button required)

    Example usage:
        >>> data = {
        ...     "name": "product_catalog",
        ...     "language": "en",
        ...     "category": "MARKETING",
        ...     "components": [
        ...         {"type": "BODY", "text": "Check out our latest products!",
        ...          "example": {"body_text": [["Check out our latest products!"]]}},
        ...         {"type": "FOOTER", "text": "Tap below to browse"},
        ...         {"type": "BUTTONS", "buttons": [
        ...             {"type": "CATALOG", "text": "View catalog"}
        ...         ]}
        ...     ]
        ... }
        >>> template = CatalogTemplateRequestValidator(**data)
    """

    category: Literal["MARKETING", "marketing"] = Field(
        ..., description="Template category (must be 'MARKETING' for catalog templates)"
    )
    components: List[CatalogTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (body, footer, buttons with CATALOG)",
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

            # Normalize type to uppercase for comparison
            comp_type_upper = comp_type.upper()

            try:
                if comp_type_upper == "HEADER":
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
                elif comp_type_upper == "BODY":
                    # Parse example if present
                    if "example" in comp and isinstance(comp["example"], dict):
                        comp["example"] = BodyTextExample(**comp["example"])
                    parsed.append(BodyComponent(**comp))
                elif comp_type_upper == "FOOTER":
                    parsed.append(FooterComponent(**comp))
                elif comp_type_upper == "BUTTONS":
                    # Parse buttons
                    if "buttons" in comp:
                        parsed_buttons = []
                        for btn in comp["buttons"]:
                            if isinstance(btn, BaseModel):
                                parsed_buttons.append(btn)
                            elif isinstance(btn, dict):
                                btn_type = btn.get("type")
                                if btn_type == "CATALOG":
                                    parsed_buttons.append(CatalogButton(**btn))
                                elif btn_type == "url":
                                    parsed_buttons.append(URLButton(**btn))
                                elif btn_type == "phone_number":
                                    parsed_buttons.append(PhoneNumberButton(**btn))
                                elif btn_type == "quick_reply":
                                    parsed_buttons.append(QuickReplyButton(**btn))
                                else:
                                    raise ValueError(f"Unknown button type: {btn_type}")
                            else:
                                raise ValueError(
                                    f"Button must be a dictionary, got {type(btn)}"
                                )
                        comp["buttons"] = parsed_buttons
                    parsed.append(CatalogButtonsComponent(**comp))
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_template_structure(self):
        """Validate the overall template structure"""
        component_types = [comp.type.upper() for comp in self.components]

        # Body is required for catalog templates
        if "BODY" not in component_types:
            raise ValueError("Body component is required for catalog templates")

        # Buttons with CATALOG is required
        if "BUTTONS" not in component_types:
            raise ValueError(
                "Buttons component with CATALOG button is required for catalog templates"
            )

        # Check for duplicate components
        if component_types.count("HEADER") > 1:
            raise ValueError("Only one header component is allowed")
        if component_types.count("BODY") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("FOOTER") > 1:
            raise ValueError("Only one footer component is allowed")
        if component_types.count("BUTTONS") > 1:
            raise ValueError("Only one buttons component is allowed")

        # Validate component order: header -> body -> footer -> buttons
        expected_order = ["HEADER", "BODY", "FOOTER", "BUTTONS"]
        current_order = [t for t in expected_order if t in component_types]
        actual_order = [t.upper() for t in component_types if t.upper() in expected_order]

        if current_order != actual_order:
            raise ValueError(
                f"Components must be in order: HEADER -> BODY -> FOOTER -> BUTTONS. "
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
                    "name": "product_showcase",
                    "language": "en",
                    "category": "MARKETING",
                    "components": [
                        {
                            "type": "BODY",
                            "text": "Check out our amazing products! We have {{1}} items on sale.",
                            "example": {"body_text": [["50"]]},
                        },
                        {"type": "FOOTER", "text": "Tap below to browse our catalog"},
                        {
                            "type": "BUTTONS",
                            "buttons": [{"type": "CATALOG", "text": "View catalog"}],
                        },
                    ],
                }
            ]
        }
    }


# ============================================================================
# Utility Functions
# ============================================================================


def validate_catalog_template(data: dict) -> CatalogTemplateRequestValidator:
    """
    Validate a catalog template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        CatalogTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return CatalogTemplateRequestValidator(**data)


def parse_and_validate_catalog_template(
    json_str: str,
) -> CatalogTemplateRequestValidator:
    """
    Parse JSON string and validate as catalog template.

    Args:
        json_str: JSON string containing template data

    Returns:
        CatalogTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_catalog_template(data)
