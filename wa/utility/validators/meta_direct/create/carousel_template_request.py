"""
Carousel Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
carousel template creation requests.

Carousel templates contain multiple cards, each with header (image/video),
body, and buttons.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.buttons import (PhoneNumberButton,
                                                       QuickReplyButton,
                                                       URLButton)
from wa.utility.data_model.meta_direct.header import HeaderHandleExample

# ============================================================================
# Card Component Models
# ============================================================================


class CardHeaderComponent(BaseModel):
    """Header component for carousel card - only IMAGE or VIDEO allowed"""
    type: Literal["header", "HEADER"] = "header"
    format: Literal["IMAGE", "VIDEO", "image", "video"] = Field(
        ..., description="Header format (IMAGE or VIDEO only for carousel cards)"
    )
    example: Optional[HeaderHandleExample] = Field(
        None, description="Example header handle for media"
    )

    @field_validator("format")
    @classmethod
    def normalize_format(cls, v):
        return v.upper() if v else v


class CardBodyComponent(BaseModel):
    """Body component for carousel card"""
    type: Literal["body", "BODY"] = "body"
    text: str = Field(
        ...,
        min_length=1,
        max_length=160,
        description="Card body text (max 160 characters)",
    )
    example: Optional[BodyTextExample] = Field(
        None, description="Example values for body variables"
    )


class CardButtonsComponent(BaseModel):
    """Buttons component for carousel card - max 2 buttons per card"""
    type: Literal["buttons", "BUTTONS"] = "buttons"
    buttons: List[Union[QuickReplyButton, URLButton, PhoneNumberButton]] = Field(
        ...,
        min_length=1,
        max_length=2,
        description="List of buttons (max 2 per card)",
    )


# Union type for card components
CardComponent = Union[CardHeaderComponent, CardBodyComponent, CardButtonsComponent]


class CarouselCard(BaseModel):
    """A single card in the carousel"""
    components: List[CardComponent] = Field(
        ...,
        min_length=1,
        description="Card components (header, body, buttons)",
    )

    @field_validator("components", mode="before")
    @classmethod
    def parse_components(cls, v):
        """Parse component dictionaries into appropriate component types"""
        if not v:
            raise ValueError("Card must have at least one component")

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
                    parsed.append(CardHeaderComponent(**comp))
                elif comp_type == "body":
                    if "example" in comp and isinstance(comp["example"], dict):
                        comp["example"] = BodyTextExample(**comp["example"])
                    parsed.append(CardBodyComponent(**comp))
                elif comp_type == "buttons":
                    if "buttons" in comp:
                        parsed_buttons = []
                        for btn in comp["buttons"]:
                            if isinstance(btn, BaseModel):
                                parsed_buttons.append(btn)
                            elif isinstance(btn, dict):
                                btn_type = btn.get("type")
                                if btn_type == "quick_reply":
                                    parsed_buttons.append(QuickReplyButton(**btn))
                                elif btn_type == "url":
                                    parsed_buttons.append(URLButton(**btn))
                                elif btn_type == "phone_number":
                                    parsed_buttons.append(PhoneNumberButton(**btn))
                                else:
                                    raise ValueError(
                                        f"Unsupported button type for carousel: {btn_type}"
                                    )
                            else:
                                raise ValueError(
                                    f"Button must be a dictionary, got {type(btn)}"
                                )
                        comp["buttons"] = parsed_buttons
                    parsed.append(CardButtonsComponent(**comp))
                else:
                    raise ValueError(f"Unknown card component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing card {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_card_structure(self):
        """Validate card has required components"""
        component_types = [comp.type.lower() for comp in self.components]

        # Header is required for carousel cards
        if "header" not in component_types:
            raise ValueError("Header component is required for carousel cards")

        # Body is required for carousel cards
        if "body" not in component_types:
            raise ValueError("Body component is required for carousel cards")

        # Buttons is required for carousel cards
        if "buttons" not in component_types:
            raise ValueError("Buttons component is required for carousel cards")

        return self


class CarouselComponent(BaseModel):
    """Carousel component containing multiple cards"""
    type: Literal["carousel", "CAROUSEL"] = "carousel"
    cards: List[CarouselCard] = Field(
        ...,
        min_length=2,
        max_length=10,
        description="List of carousel cards (2-10 cards)",
    )


# Union type for carousel template components
CarouselTemplateComponent = Union[BodyComponent, CarouselComponent]


class CarouselTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API carousel template creation request.

    Carousel templates allow sending multiple cards that users can swipe through.
    Each card contains a header (image/video), body, and buttons.

    Requirements:
    - 2-10 cards per carousel
    - Each card must have: header (IMAGE or VIDEO), body, buttons (1-2)
    - All cards must have the same button configuration

    Example usage:
        >>> data = {
        ...     "name": "product_carousel",
        ...     "language": "en",
        ...     "category": "marketing",
        ...     "components": [
        ...         {"type": "body", "text": "Check out our products!"},
        ...         {"type": "carousel", "cards": [
        ...             {"components": [
        ...                 {"type": "header", "format": "image",
        ...                  "example": {"header_handle": ["4::..."]}},
        ...                 {"type": "body", "text": "Product 1 - {{1}}",
        ...                  "example": {"body_text": [["$99"]]}},
        ...                 {"type": "buttons", "buttons": [
        ...                     {"type": "quick_reply", "text": "Buy Now"},
        ...                     {"type": "url", "text": "Details", "url": "https://..."}
        ...                 ]}
        ...             ]},
        ...             // more cards...
        ...         ]}
        ...     ]
        ... }
        >>> template = CarouselTemplateRequestValidator(**data)
    """

    category: Literal["MARKETING", "marketing"] = Field(
        ...,
        description="Template category (must be 'marketing' for carousel templates)",
    )
    components: List[CarouselTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (body and carousel)",
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
                elif comp_type == "carousel":
                    if "cards" in comp:
                        parsed_cards = []
                        for card in comp["cards"]:
                            if isinstance(card, BaseModel):
                                parsed_cards.append(card)
                            elif isinstance(card, dict):
                                parsed_cards.append(CarouselCard(**card))
                            else:
                                raise ValueError(
                                    f"Card must be a dictionary, got {type(card)}"
                                )
                        comp["cards"] = parsed_cards
                    parsed.append(CarouselComponent(**comp))
                else:
                    raise ValueError(
                        f"Unknown component type for carousel template: {comp_type}. "
                        f"Only 'body' and 'carousel' are allowed."
                    )
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_template_structure(self):
        """Validate the overall template structure"""
        component_types = [comp.type.lower() for comp in self.components]

        # Carousel component is required
        if "carousel" not in component_types:
            raise ValueError("Carousel component is required for carousel templates")

        # Check for duplicates
        if component_types.count("body") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("carousel") > 1:
            raise ValueError("Only one carousel component is allowed")

        # Validate all cards have consistent button configuration
        carousel_comp = next(
            c for c in self.components if c.type.lower() == "carousel"
        )
        if carousel_comp.cards:
            first_card_buttons = None
            for i, card in enumerate(carousel_comp.cards):
                buttons_comp = next(
                    (c for c in card.components if c.type.lower() == "buttons"), None
                )
                if buttons_comp:
                    button_types = [
                        (b.type, getattr(b, "text", None))
                        for b in buttons_comp.buttons
                    ]
                    if first_card_buttons is None:
                        first_card_buttons = button_types
                    elif len(button_types) != len(first_card_buttons):
                        raise ValueError(
                            f"All carousel cards must have the same number of buttons. "
                            f"Card 0 has {len(first_card_buttons)} buttons, "
                            f"card {i} has {len(button_types)} buttons."
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
                    "category": "marketing",
                    "components": [
                        {
                            "type": "body",
                            "text": "Check out our latest products! {{1}}",
                            "example": {"body_text": [["Summer Sale"]]},
                        },
                        {
                            "type": "carousel",
                            "cards": [
                                {
                                    "components": [
                                        {
                                            "type": "header",
                                            "format": "image",
                                            "example": {
                                                "header_handle": ["4::aW1hZ2UxLmpwZw=="]
                                            },
                                        },
                                        {
                                            "type": "body",
                                            "text": "Product A - Only {{1}}!",
                                            "example": {"body_text": [["$49.99"]]},
                                        },
                                        {
                                            "type": "buttons",
                                            "buttons": [
                                                {"type": "quick_reply", "text": "Buy Now"},
                                                {
                                                    "type": "url",
                                                    "text": "View Details",
                                                    "url": "https://example.com/product/{{1}}",
                                                    "example": ["product-a"],
                                                },
                                            ],
                                        },
                                    ]
                                },
                                {
                                    "components": [
                                        {
                                            "type": "header",
                                            "format": "image",
                                            "example": {
                                                "header_handle": ["4::aW1hZ2UyLmpwZw=="]
                                            },
                                        },
                                        {
                                            "type": "body",
                                            "text": "Product B - Only {{1}}!",
                                            "example": {"body_text": [["$59.99"]]},
                                        },
                                        {
                                            "type": "buttons",
                                            "buttons": [
                                                {"type": "quick_reply", "text": "Buy Now"},
                                                {
                                                    "type": "url",
                                                    "text": "View Details",
                                                    "url": "https://example.com/product/{{1}}",
                                                    "example": ["product-b"],
                                                },
                                            ],
                                        },
                                    ]
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


def validate_carousel_template(data: dict) -> CarouselTemplateRequestValidator:
    """
    Validate a carousel template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        CarouselTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return CarouselTemplateRequestValidator(**data)


def parse_and_validate_carousel_template(
    json_str: str,
) -> CarouselTemplateRequestValidator:
    """
    Parse JSON string and validate as carousel template.

    Args:
        json_str: JSON string containing template data

    Returns:
        CarouselTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_carousel_template(data)
