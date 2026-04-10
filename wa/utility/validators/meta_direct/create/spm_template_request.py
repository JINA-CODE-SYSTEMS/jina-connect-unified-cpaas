"""
Single Product Message (SPM) Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Single Product Message template creation requests.

SPM templates display a single product from a connected catalog with
product details (image, title, price, description) pulled automatically.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.footer import FooterComponent

# ============================================================================
# Product Models
# ============================================================================


class ProductAction(BaseModel):
    """Action configuration for SPM template - references a single product"""
    thumbnail_product_retailer_id: str = Field(
        ...,
        min_length=1,
        description="Product retailer ID from the catalog to display",
    )


class SPMComponent(BaseModel):
    """Single Product Message component"""
    type: Literal["product", "PRODUCT"] = "product"
    action: ProductAction = Field(
        ...,
        description="Product action with the product retailer ID",
    )


# Union type for SPM template components
SPMTemplateComponent = Union[BodyComponent, FooterComponent, SPMComponent]


class SPMTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API Single Product Message template creation request.

    SPM templates display a single product from a connected catalog.
    Product details (image, title, price, description) are pulled automatically
    from the catalog.

    Requirements:
    - Body component is required
    - Product component is required with product_retailer_id
    - Footer is optional
    - Product must exist in the connected catalog

    Example usage:
        >>> data = {
        ...     "name": "featured_product",
        ...     "language": "en",
        ...     "category": "marketing",
        ...     "components": [
        ...         {"type": "body", "text": "Check out this product, {{1}}!",
        ...          "example": {"body_text": [["John"]]}},
        ...         {"type": "footer", "text": "Tap to view details"},
        ...         {"type": "product", "action": {
        ...             "thumbnail_product_retailer_id": "SKU001"
        ...         }}
        ...     ]
        ... }
        >>> template = SPMTemplateRequestValidator(**data)
    """

    category: Literal["MARKETING", "marketing"] = Field(
        ...,
        description="Template category (must be 'marketing' for SPM templates)",
    )
    components: List[SPMTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (body, footer, product)",
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
                if comp_type == "body":
                    if "example" in comp and isinstance(comp["example"], dict):
                        comp["example"] = BodyTextExample(**comp["example"])
                    parsed.append(BodyComponent(**comp))
                elif comp_type == "footer":
                    parsed.append(FooterComponent(**comp))
                elif comp_type == "product":
                    # Parse action
                    if "action" in comp and isinstance(comp["action"], dict):
                        comp["action"] = ProductAction(**comp["action"])
                    parsed.append(SPMComponent(**comp))
                else:
                    raise ValueError(
                        f"Unknown component type for SPM template: {comp_type}. "
                        f"Allowed types: 'body', 'footer', 'product'."
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
            raise ValueError("Body component is required for SPM templates")

        # Product is required
        if "product" not in component_types:
            raise ValueError("Product component is required for SPM templates")

        # Check for duplicates
        if component_types.count("body") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("footer") > 1:
            raise ValueError("Only one footer component is allowed")
        if component_types.count("product") > 1:
            raise ValueError("Only one product component is allowed")

        # Validate component order: body -> footer -> product
        expected_order = ["body", "footer", "product"]
        current_order = [t for t in expected_order if t in component_types]
        actual_order = [
            t.lower() for t in component_types if t.lower() in expected_order
        ]

        if current_order != actual_order:
            raise ValueError(
                f"Components must be in order: body -> footer -> product. "
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
                    "name": "featured_product_promo",
                    "language": "en",
                    "category": "marketing",
                    "components": [
                        {
                            "type": "body",
                            "text": "Hi {{1}}! Check out our featured product of the day!",
                            "example": {"body_text": [["John"]]},
                        },
                        {"type": "footer", "text": "Tap to view and purchase"},
                        {
                            "type": "product",
                            "action": {"thumbnail_product_retailer_id": "SKU001"},
                        },
                    ],
                }
            ]
        }
    }


# ============================================================================
# Utility Functions
# ============================================================================


def validate_spm_template(data: dict) -> SPMTemplateRequestValidator:
    """
    Validate a Single Product Message template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        SPMTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return SPMTemplateRequestValidator(**data)


def parse_and_validate_spm_template(json_str: str) -> SPMTemplateRequestValidator:
    """
    Parse JSON string and validate as SPM template.

    Args:
        json_str: JSON string containing template data

    Returns:
        SPMTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_spm_template(data)
