"""
Product Card Carousel Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Product Card Carousel template creation requests.

Product Card Carousels display products from a connected catalog, with
product details (image, title, price) pulled automatically from the catalog.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.buttons import (QuickReplyButton,
                                                       URLButton)

# ============================================================================
# Product Card Models
# ============================================================================


class ProductCardButton(BaseModel):
    """Button for product card - quick_reply or url only"""
    type: Literal["quick_reply", "url"] = Field(
        ..., description="Button type (quick_reply or url)"
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=25,
        description="Button text (max 25 characters)",
    )
    # URL button specific fields
    url: Optional[str] = Field(
        None, description="URL for url type button"
    )
    example: Optional[List[str]] = Field(
        None, description="Example values for URL variables"
    )


class ProductCardButtonsComponent(BaseModel):
    """Buttons component for product card - max 2 buttons"""
    type: Literal["buttons", "BUTTONS"] = "buttons"
    buttons: List[ProductCardButton] = Field(
        ...,
        min_length=1,
        max_length=2,
        description="List of buttons (max 2 per card)",
    )


class ProductCard(BaseModel):
    """A single product card in the carousel"""
    product_retailer_id: str = Field(
        ...,
        min_length=1,
        description="Product retailer ID from the catalog",
    )
    components: List[ProductCardButtonsComponent] = Field(
        ...,
        min_length=1,
        max_length=1,
        description="Card components (only buttons allowed)",
    )

    @field_validator("components", mode="before")
    @classmethod
    def parse_components(cls, v):
        """Parse component dictionaries into appropriate component types"""
        if not v:
            raise ValueError("Product card must have a buttons component")

        parsed = []
        for comp in v:
            if isinstance(comp, BaseModel):
                parsed.append(comp)
                continue

            if not isinstance(comp, dict):
                raise ValueError(f"Component must be a dictionary, got {type(comp)}")

            comp_type = comp.get("type", "").lower()

            if comp_type == "buttons":
                # Parse buttons
                if "buttons" in comp:
                    parsed_buttons = []
                    for btn in comp["buttons"]:
                        if isinstance(btn, BaseModel):
                            parsed_buttons.append(btn)
                        elif isinstance(btn, dict):
                            parsed_buttons.append(ProductCardButton(**btn))
                        else:
                            raise ValueError(
                                f"Button must be a dictionary, got {type(btn)}"
                            )
                    comp["buttons"] = parsed_buttons
                parsed.append(ProductCardButtonsComponent(**comp))
            else:
                raise ValueError(
                    f"Unknown component type for product card: {comp_type}. "
                    f"Only 'buttons' is allowed."
                )

        return parsed


class ProductCarouselComponent(BaseModel):
    """Product carousel component containing product cards"""
    type: Literal["product_carousel", "PRODUCT_CAROUSEL"] = "product_carousel"
    cards: List[ProductCard] = Field(
        ...,
        min_length=2,
        max_length=10,
        description="List of product cards (2-10 cards)",
    )


# Union type for product card carousel template components
ProductCardCarouselTemplateComponent = Union[BodyComponent, ProductCarouselComponent]


class ProductCardCarouselTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API Product Card Carousel template creation request.

    Product Card Carousels display products from a connected catalog.
    Product details (image, title, price) are pulled automatically from the catalog.
    Each card can have up to 2 buttons.

    Requirements:
    - 2-10 product cards per carousel
    - Each card references a product_retailer_id from catalog
    - Each card must have buttons component (1-2 buttons)
    - Body component is required
    - All cards must have the same button configuration

    Example usage:
        >>> data = {
        ...     "name": "product_showcase",
        ...     "language": "en",
        ...     "category": "marketing",
        ...     "components": [
        ...         {"type": "body", "text": "Check out our products, {{1}}!",
        ...          "example": {"body_text": [["John"]]}},
        ...         {"type": "product_carousel", "cards": [
        ...             {"product_retailer_id": "SKU001", "components": [
        ...                 {"type": "buttons", "buttons": [
        ...                     {"type": "quick_reply", "text": "Buy Now"},
        ...                     {"type": "url", "text": "Details", "url": "https://..."}
        ...                 ]}
        ...             ]},
        ...             {"product_retailer_id": "SKU002", "components": [...]}
        ...         ]}
        ...     ]
        ... }
        >>> template = ProductCardCarouselTemplateRequestValidator(**data)
    """

    category: Literal["MARKETING", "marketing"] = Field(
        ...,
        description="Template category (must be 'marketing' for product carousels)",
    )
    components: List[ProductCardCarouselTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (body and product_carousel)",
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
                elif comp_type == "product_carousel":
                    # Parse product cards
                    if "cards" in comp:
                        parsed_cards = []
                        for card in comp["cards"]:
                            if isinstance(card, BaseModel):
                                parsed_cards.append(card)
                            elif isinstance(card, dict):
                                parsed_cards.append(ProductCard(**card))
                            else:
                                raise ValueError(
                                    f"Card must be a dictionary, got {type(card)}"
                                )
                        comp["cards"] = parsed_cards
                    parsed.append(ProductCarouselComponent(**comp))
                else:
                    raise ValueError(
                        f"Unknown component type for product card carousel: {comp_type}. "
                        f"Allowed types: 'body', 'product_carousel'."
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
            raise ValueError(
                "Body component is required for product card carousel templates"
            )

        # product_carousel is required
        if "product_carousel" not in component_types:
            raise ValueError(
                "product_carousel component is required for product card carousel templates"
            )

        # Check for duplicates
        if component_types.count("body") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("product_carousel") > 1:
            raise ValueError("Only one product_carousel component is allowed")

        # Validate all cards have consistent button configuration
        carousel_comp = next(
            c for c in self.components if c.type.lower() == "product_carousel"
        )
        if carousel_comp.cards:
            first_card_buttons = None
            for i, card in enumerate(carousel_comp.cards):
                if card.components:
                    buttons_comp = card.components[0]  # Only buttons allowed
                    button_config = [
                        (b.type, b.text) for b in buttons_comp.buttons
                    ]
                    if first_card_buttons is None:
                        first_card_buttons = button_config
                    elif len(button_config) != len(first_card_buttons):
                        raise ValueError(
                            f"All product cards must have the same number of buttons. "
                            f"Card 0 has {len(first_card_buttons)} buttons, "
                            f"card {i} has {len(button_config)} buttons."
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
                    "name": "product_showcase_carousel",
                    "language": "en",
                    "category": "marketing",
                    "components": [
                        {
                            "type": "body",
                            "text": "Hi {{1}}! Check out our top products this week!",
                            "example": {"body_text": [["John"]]},
                        },
                        {
                            "type": "product_carousel",
                            "cards": [
                                {
                                    "product_retailer_id": "SKU001",
                                    "components": [
                                        {
                                            "type": "buttons",
                                            "buttons": [
                                                {"type": "quick_reply", "text": "Buy Now"},
                                                {
                                                    "type": "url",
                                                    "text": "View Details",
                                                    "url": "https://example.com/product/{{1}}",
                                                    "example": ["SKU001"],
                                                },
                                            ],
                                        }
                                    ],
                                },
                                {
                                    "product_retailer_id": "SKU002",
                                    "components": [
                                        {
                                            "type": "buttons",
                                            "buttons": [
                                                {"type": "quick_reply", "text": "Buy Now"},
                                                {
                                                    "type": "url",
                                                    "text": "View Details",
                                                    "url": "https://example.com/product/{{1}}",
                                                    "example": ["SKU002"],
                                                },
                                            ],
                                        }
                                    ],
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


def validate_product_card_carousel_template(
    data: dict,
) -> ProductCardCarouselTemplateRequestValidator:
    """
    Validate a Product Card Carousel template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        ProductCardCarouselTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return ProductCardCarouselTemplateRequestValidator(**data)


def parse_and_validate_product_card_carousel_template(
    json_str: str,
) -> ProductCardCarouselTemplateRequestValidator:
    """
    Parse JSON string and validate as Product Card Carousel template.

    Args:
        json_str: JSON string containing template data

    Returns:
        ProductCardCarouselTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_product_card_carousel_template(data)
