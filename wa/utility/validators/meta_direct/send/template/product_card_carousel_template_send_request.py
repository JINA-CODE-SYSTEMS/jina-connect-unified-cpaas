"""
Product Card Carousel Template Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Product Card Carousel template message sending requests.

Product Card Carousels display products from a connected catalog, with
product details pulled automatically.
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

# ============================================================================
# Parameter Models
# ============================================================================


class BodyTextParameter(BaseModel):
    """Body parameter with text"""
    type: Literal["text"] = "text"
    text: str = Field(..., description="Text value for the body parameter")


class BodyCurrencyParameter(BaseModel):
    """Body parameter with currency"""
    type: Literal["currency"] = "currency"
    currency: dict = Field(
        ...,
        description="Currency object with fallback_value, code, and amount_1000",
    )


class BodyDateTimeParameter(BaseModel):
    """Body parameter with date_time"""
    type: Literal["date_time"] = "date_time"
    date_time: dict = Field(..., description="DateTime object with fallback_value")


# Union type for body parameters
BodyParameter = Union[BodyTextParameter, BodyCurrencyParameter, BodyDateTimeParameter]


# ============================================================================
# Button Parameter Models
# ============================================================================


class QuickReplyButtonParameter(BaseModel):
    """Button parameter for quick_reply buttons"""
    type: Literal["payload"] = "payload"
    payload: str = Field(..., description="Payload for quick reply button")


class URLButtonParameter(BaseModel):
    """Button parameter for URL buttons with dynamic suffix"""
    type: Literal["text"] = "text"
    text: str = Field(..., description="Dynamic URL suffix value")


# ============================================================================
# Card Component Models for Send
# ============================================================================


class CardQuickReplyButtonComponentSend(BaseModel):
    """Quick reply button component for product card"""
    type: Literal["button"] = "button"
    sub_type: Literal["quick_reply"] = "quick_reply"
    index: int = Field(..., ge=0, le=1, description="Button index (0 or 1)")
    parameters: List[QuickReplyButtonParameter] = Field(
        ..., min_length=1, max_length=1, description="Quick reply button parameters"
    )


class CardURLButtonComponentSend(BaseModel):
    """URL button component for product card"""
    type: Literal["button"] = "button"
    sub_type: Literal["url"] = "url"
    index: int = Field(..., ge=0, le=1, description="Button index (0 or 1)")
    parameters: List[URLButtonParameter] = Field(
        ..., min_length=1, max_length=1, description="URL button parameters"
    )


# Union type for card button components
CardButtonComponentSend = Union[
    CardQuickReplyButtonComponentSend, CardURLButtonComponentSend
]


# ============================================================================
# Product Card for Send
# ============================================================================


class ProductCardSend(BaseModel):
    """A single product card in the carousel for send request"""
    card_index: int = Field(..., ge=0, le=9, description="Card index (0-based)")
    components: List[CardButtonComponentSend] = Field(
        ..., min_length=1, max_length=2, description="Card button components"
    )

    @field_validator("components", mode="before")
    @classmethod
    def parse_components(cls, v):
        """Parse component dictionaries into appropriate component types"""
        if not v:
            raise ValueError("Product card must have at least one button component")

        parsed = []
        for comp in v:
            if isinstance(comp, BaseModel):
                parsed.append(comp)
                continue

            if not isinstance(comp, dict):
                raise ValueError(f"Component must be a dictionary, got {type(comp)}")

            comp_type = comp.get("type")
            if comp_type != "button":
                raise ValueError(
                    f"Product card components must be of type 'button', got {comp_type}"
                )

            sub_type = comp.get("sub_type")
            try:
                if sub_type == "quick_reply":
                    if "parameters" in comp:
                        parsed_params = []
                        for param in comp["parameters"]:
                            if isinstance(param, BaseModel):
                                parsed_params.append(param)
                            elif isinstance(param, dict):
                                param_type = param.get("type")
                                if param_type == "payload":
                                    parsed_params.append(
                                        QuickReplyButtonParameter(**param)
                                    )
                                else:
                                    raise ValueError(
                                        f"Unknown quick_reply parameter type: {param_type}"
                                    )
                            else:
                                raise ValueError(
                                    f"Parameter must be a dictionary, got {type(param)}"
                                )
                        comp["parameters"] = parsed_params
                    parsed.append(CardQuickReplyButtonComponentSend(**comp))
                elif sub_type == "url":
                    if "parameters" in comp:
                        parsed_params = []
                        for param in comp["parameters"]:
                            if isinstance(param, BaseModel):
                                parsed_params.append(param)
                            elif isinstance(param, dict):
                                param_type = param.get("type")
                                if param_type == "text":
                                    parsed_params.append(URLButtonParameter(**param))
                                else:
                                    raise ValueError(
                                        f"Unknown URL button parameter type: {param_type}"
                                    )
                            else:
                                raise ValueError(
                                    f"Parameter must be a dictionary, got {type(param)}"
                                )
                        comp["parameters"] = parsed_params
                    parsed.append(CardURLButtonComponentSend(**comp))
                else:
                    raise ValueError(f"Unknown button sub_type: {sub_type}")
            except Exception as e:
                raise ValueError(f"Error parsing button component: {e}")

        return parsed


# ============================================================================
# Top-level Component Models for Send
# ============================================================================


class BodyComponentSend(BaseModel):
    """Body component for sending template message"""
    type: Literal["body"] = "body"
    parameters: List[BodyParameter] = Field(
        ..., min_length=1, description="Body parameters"
    )


class ProductCarouselComponentSend(BaseModel):
    """Product carousel component for sending template message"""
    type: Literal["product_carousel"] = "product_carousel"
    cards: List[ProductCardSend] = Field(
        ...,
        min_length=2,
        max_length=10,
        description="List of product cards (2-10 cards)",
    )

    @field_validator("cards")
    @classmethod
    def validate_card_indices(cls, v):
        """Validate that card indices are sequential starting from 0"""
        if not v:
            return v
        indices = [card.card_index for card in v]
        expected = list(range(len(v)))
        if sorted(indices) != expected:
            raise ValueError(
                f"Card indices must be sequential starting from 0. "
                f"Expected {expected}, got {sorted(indices)}"
            )
        return v


# Union type for send components
SendProductCardCarouselTemplateComponent = Union[
    BodyComponentSend, ProductCarouselComponentSend
]


# ============================================================================
# Language Model
# ============================================================================


class LanguageInput(BaseModel):
    """Language input for template request"""
    code: str = Field(
        ...,
        min_length=2,
        max_length=10,
        description="Template language code (e.g., 'en', 'en_US')",
    )

    @field_validator("code")
    @classmethod
    def validate_code(cls, v):
        if not v or not v.strip():
            raise ValueError("Language code cannot be empty")
        return v.strip()


# ============================================================================
# Template Body Model
# ============================================================================


class ProductCardCarouselTemplateSendBody(BaseModel):
    """Template body for product card carousel template send request"""
    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name",
    )
    language: LanguageInput = Field(..., description="Template language")
    components: Optional[List[SendProductCardCarouselTemplateComponent]] = Field(
        None, description="Template components with parameter values"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Template name cannot be empty")
        return v.strip().lower()

    @field_validator("components", mode="before")
    @classmethod
    def parse_components(cls, v):
        """Parse component dictionaries into appropriate component types"""
        if v is None:
            return None

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

            try:
                if comp_type == "body":
                    # Parse body parameters
                    if "parameters" in comp:
                        parsed_params = []
                        for param in comp["parameters"]:
                            if isinstance(param, BaseModel):
                                parsed_params.append(param)
                            elif isinstance(param, dict):
                                param_type = param.get("type")
                                if param_type == "text":
                                    parsed_params.append(BodyTextParameter(**param))
                                elif param_type == "currency":
                                    parsed_params.append(BodyCurrencyParameter(**param))
                                elif param_type == "date_time":
                                    parsed_params.append(BodyDateTimeParameter(**param))
                                else:
                                    raise ValueError(
                                        f"Unknown body parameter type: {param_type}"
                                    )
                            else:
                                raise ValueError(
                                    f"Parameter must be a dictionary, got {type(param)}"
                                )
                        comp["parameters"] = parsed_params
                    parsed.append(BodyComponentSend(**comp))
                elif comp_type == "product_carousel":
                    # Parse product cards
                    if "cards" in comp:
                        parsed_cards = []
                        for card in comp["cards"]:
                            if isinstance(card, BaseModel):
                                parsed_cards.append(card)
                            elif isinstance(card, dict):
                                parsed_cards.append(ProductCardSend(**card))
                            else:
                                raise ValueError(
                                    f"Card must be a dictionary, got {type(card)}"
                                )
                        comp["cards"] = parsed_cards
                    parsed.append(ProductCarouselComponentSend(**comp))
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed


# ============================================================================
# Main Request Validator
# ============================================================================


class ProductCardCarouselTemplateSendRequestValidator(BaseModel):
    """
    Validator for META Direct API Product Card Carousel template send request.

    Validates the complete message structure including:
    - messaging_product: "whatsapp"
    - recipient_type: "individual"
    - to: Recipient phone number
    - type: "template"
    - template: Template details with name, language, and components
      including product_carousel with cards

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "template",
        ...     "template": {
        ...         "name": "product_showcase_carousel",
        ...         "language": {"code": "en"},
        ...         "components": [
        ...             {"type": "body", "parameters": [
        ...                 {"type": "text", "text": "John"}
        ...             ]},
        ...             {"type": "product_carousel", "cards": [
        ...                 {"card_index": 0, "components": [
        ...                     {"type": "button", "sub_type": "quick_reply", "index": 0,
        ...                      "parameters": [{"type": "payload", "payload": "BUY_SKU001"}]},
        ...                     {"type": "button", "sub_type": "url", "index": 1,
        ...                      "parameters": [{"type": "text", "text": "SKU001"}]}
        ...                 ]},
        ...                 {"card_index": 1, "components": [...]}
        ...             ]}
        ...         ]
        ...     }
        ... }
        >>> request = ProductCardCarouselTemplateSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field(
        "whatsapp", description="Messaging product (must be 'whatsapp')"
    )
    recipient_type: Literal["individual"] = Field(
        "individual", description="Recipient type (must be 'individual')"
    )
    to: str = Field(..., description="Recipient phone number")
    type: Literal["template"] = Field(
        "template", description="Message type (must be 'template')"
    )
    template: ProductCardCarouselTemplateSendBody = Field(
        ..., description="Template details"
    )

    @field_validator("to")
    @classmethod
    def validate_phone_number(cls, v):
        if not v or not v.strip():
            raise ValueError("Recipient phone number cannot be empty")
        # Remove any non-digit characters except +
        cleaned = re.sub(r"[^\d+]", "", v)
        # Validate phone number format
        if not re.match(r"^\+?[0-9]{10,15}$", cleaned):
            raise ValueError(
                "Invalid phone number format. Must be 10-15 digits, optionally starting with +"
            )
        return cleaned

    def to_meta_payload(self) -> dict:
        """Convert validated request to META API payload format"""
        payload = {
            "messaging_product": self.messaging_product,
            "recipient_type": self.recipient_type,
            "to": self.to,
            "type": self.type,
            "template": {
                "name": self.template.name,
                "language": {"code": self.template.language.code},
            },
        }

        if self.template.components:
            payload["template"]["components"] = [
                comp.model_dump(exclude_none=True) for comp in self.template.components
            ]

        return payload

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "template",
                    "template": {
                        "name": "product_showcase_carousel",
                        "language": {"code": "en"},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [{"type": "text", "text": "John"}],
                            },
                            {
                                "type": "product_carousel",
                                "cards": [
                                    {
                                        "card_index": 0,
                                        "components": [
                                            {
                                                "type": "button",
                                                "sub_type": "quick_reply",
                                                "index": 0,
                                                "parameters": [
                                                    {
                                                        "type": "payload",
                                                        "payload": "BUY_SKU001",
                                                    }
                                                ],
                                            },
                                            {
                                                "type": "button",
                                                "sub_type": "url",
                                                "index": 1,
                                                "parameters": [
                                                    {"type": "text", "text": "SKU001"}
                                                ],
                                            },
                                        ],
                                    },
                                    {
                                        "card_index": 1,
                                        "components": [
                                            {
                                                "type": "button",
                                                "sub_type": "quick_reply",
                                                "index": 0,
                                                "parameters": [
                                                    {
                                                        "type": "payload",
                                                        "payload": "BUY_SKU002",
                                                    }
                                                ],
                                            },
                                            {
                                                "type": "button",
                                                "sub_type": "url",
                                                "index": 1,
                                                "parameters": [
                                                    {"type": "text", "text": "SKU002"}
                                                ],
                                            },
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                }
            ]
        }
    }


# ============================================================================
# Utility Functions
# ============================================================================


def validate_product_card_carousel_template_send(
    data: dict,
) -> ProductCardCarouselTemplateSendRequestValidator:
    """
    Validate a Product Card Carousel template send request dictionary.

    Args:
        data: Dictionary containing request data

    Returns:
        ProductCardCarouselTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
    """
    return ProductCardCarouselTemplateSendRequestValidator(**data)


def parse_and_validate_product_card_carousel_template_send(
    json_str: str,
) -> ProductCardCarouselTemplateSendRequestValidator:
    """
    Parse JSON string and validate as Product Card Carousel template send request.

    Args:
        json_str: JSON string containing request data

    Returns:
        ProductCardCarouselTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_product_card_carousel_template_send(data)
