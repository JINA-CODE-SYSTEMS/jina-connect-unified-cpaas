"""
Multi-Product Message (MPM) Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Multi-Product Message template creation requests.

MPM templates allow businesses to showcase multiple products from their
catalog in a single message.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.footer import FooterComponent
from wa.utility.data_model.meta_direct.header import (HeaderComponent,
                                                      HeaderHandleExample,
                                                      HeaderTextExample)

# ============================================================================
# Product Section Models
# ============================================================================


class ProductItem(BaseModel):
    """A product item in the MPM template"""
    product_retailer_id: str = Field(
        ...,
        min_length=1,
        description="Product retailer ID from the catalog",
    )


class ProductSection(BaseModel):
    """A section containing products in the MPM template"""
    title: Optional[str] = Field(
        None,
        max_length=24,
        description="Section title (max 24 characters, optional)",
    )
    product_items: List[ProductItem] = Field(
        ...,
        min_length=1,
        max_length=30,
        description="List of products in this section (max 30 per section)",
    )


class MPMAction(BaseModel):
    """Action configuration for MPM template"""
    thumbnail_product_retailer_id: Optional[str] = Field(
        None,
        description="Product ID to use as thumbnail",
    )
    sections: List[ProductSection] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Product sections (max 10 sections)",
    )

    @model_validator(mode="after")
    def validate_total_products(self):
        """Validate total products across all sections"""
        total_products = sum(len(section.product_items) for section in self.sections)
        if total_products > 30:
            raise ValueError(
                f"Total products across all sections cannot exceed 30. Got {total_products}"
            )
        return self


class MPMComponent(BaseModel):
    """Multi-Product Message component"""
    type: Literal["product_list", "PRODUCT_LIST"] = "product_list"
    action: MPMAction = Field(
        ...,
        description="MPM action with product sections",
    )


# Union type for MPM template components
MPMTemplateComponent = Union[HeaderComponent, BodyComponent, FooterComponent, MPMComponent]


class MPMTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API Multi-Product Message template creation request.

    MPM templates allow showcasing multiple products from a catalog.
    Products are organized into sections with optional titles.

    Requirements:
    - Maximum 30 products total across all sections
    - Maximum 10 sections
    - Each section can have up to 30 products
    - Body is required
    - Products must be from the connected catalog

    Example usage:
        >>> data = {
        ...     "name": "product_showcase",
        ...     "language": "en",
        ...     "category": "marketing",
        ...     "components": [
        ...         {"type": "header", "format": "text", "text": "Our Products"},
        ...         {"type": "body", "text": "Check out these items!"},
        ...         {"type": "footer", "text": "Tap to view"},
        ...         {"type": "product_list", "action": {
        ...             "thumbnail_product_retailer_id": "SKU123",
        ...             "sections": [
        ...                 {"title": "Featured", "product_items": [
        ...                     {"product_retailer_id": "SKU123"},
        ...                     {"product_retailer_id": "SKU456"}
        ...                 ]}
        ...             ]
        ...         }}
        ...     ]
        ... }
        >>> template = MPMTemplateRequestValidator(**data)
    """

    category: Literal["MARKETING", "marketing"] = Field(
        ...,
        description="Template category (must be 'marketing' for MPM templates)",
    )
    components: List[MPMTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (header, body, footer, product_list)",
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

            comp_type = comp.get("type", "").lower()

            try:
                if comp_type == "header":
                    if "format" in comp and isinstance(comp["format"], str):
                        comp["format"] = comp["format"].upper()
                    if "example" in comp and isinstance(comp["example"], dict):
                        if "header_handle" in comp["example"]:
                            comp["example"] = HeaderHandleExample(**comp["example"])
                        elif "header_text" in comp["example"]:
                            comp["example"] = HeaderTextExample(**comp["example"])
                    parsed.append(HeaderComponent(**comp))
                elif comp_type == "body":
                    if "example" in comp and isinstance(comp["example"], dict):
                        comp["example"] = BodyTextExample(**comp["example"])
                    parsed.append(BodyComponent(**comp))
                elif comp_type == "footer":
                    parsed.append(FooterComponent(**comp))
                elif comp_type == "product_list":
                    # Parse action
                    if "action" in comp and isinstance(comp["action"], dict):
                        action_data = comp["action"]
                        # Parse sections
                        if "sections" in action_data:
                            parsed_sections = []
                            for section in action_data["sections"]:
                                if isinstance(section, BaseModel):
                                    parsed_sections.append(section)
                                elif isinstance(section, dict):
                                    # Parse product items
                                    if "product_items" in section:
                                        parsed_items = []
                                        for item in section["product_items"]:
                                            if isinstance(item, BaseModel):
                                                parsed_items.append(item)
                                            elif isinstance(item, dict):
                                                parsed_items.append(ProductItem(**item))
                                            else:
                                                raise ValueError(
                                                    f"Product item must be a dictionary, got {type(item)}"
                                                )
                                        section["product_items"] = parsed_items
                                    parsed_sections.append(ProductSection(**section))
                                else:
                                    raise ValueError(
                                        f"Section must be a dictionary, got {type(section)}"
                                    )
                            action_data["sections"] = parsed_sections
                        comp["action"] = MPMAction(**action_data)
                    parsed.append(MPMComponent(**comp))
                else:
                    raise ValueError(
                        f"Unknown component type for MPM template: {comp_type}. "
                        f"Allowed types: 'header', 'body', 'footer', 'product_list'."
                    )
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_template_structure(self):
        """Validate the overall template structure"""
        component_types = [comp.type.lower() for comp in self.components]

        # Body is required
        if "body" not in component_types:
            raise ValueError("Body component is required for MPM templates")

        # product_list is required
        if "product_list" not in component_types:
            raise ValueError("product_list component is required for MPM templates")

        # Check for duplicates
        if component_types.count("header") > 1:
            raise ValueError("Only one header component is allowed")
        if component_types.count("body") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("footer") > 1:
            raise ValueError("Only one footer component is allowed")
        if component_types.count("product_list") > 1:
            raise ValueError("Only one product_list component is allowed")

        # Validate component order: header -> body -> footer -> product_list
        expected_order = ["header", "body", "footer", "product_list"]
        current_order = [t for t in expected_order if t in component_types]
        actual_order = [
            t.lower() for t in component_types if t.lower() in expected_order
        ]

        if current_order != actual_order:
            raise ValueError(
                f"Components must be in order: header -> body -> footer -> product_list. "
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
                    "name": "featured_products",
                    "language": "en",
                    "category": "marketing",
                    "components": [
                        {"type": "header", "format": "text", "text": "🛍️ Our Top Picks"},
                        {
                            "type": "body",
                            "text": "Check out our featured products this week!",
                        },
                        {"type": "footer", "text": "Tap a product to learn more"},
                        {
                            "type": "product_list",
                            "action": {
                                "thumbnail_product_retailer_id": "SKU001",
                                "sections": [
                                    {
                                        "title": "Best Sellers",
                                        "product_items": [
                                            {"product_retailer_id": "SKU001"},
                                            {"product_retailer_id": "SKU002"},
                                            {"product_retailer_id": "SKU003"},
                                        ],
                                    },
                                    {
                                        "title": "New Arrivals",
                                        "product_items": [
                                            {"product_retailer_id": "SKU004"},
                                            {"product_retailer_id": "SKU005"},
                                        ],
                                    },
                                ],
                            },
                        },
                    ],
                }
            ]
        }
    }


# ============================================================================
# Utility Functions
# ============================================================================


def validate_mpm_template(data: dict) -> MPMTemplateRequestValidator:
    """
    Validate a Multi-Product Message template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        MPMTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return MPMTemplateRequestValidator(**data)


def parse_and_validate_mpm_template(json_str: str) -> MPMTemplateRequestValidator:
    """
    Parse JSON string and validate as MPM template.

    Args:
        json_str: JSON string containing template data

    Returns:
        MPMTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_mpm_template(data)
