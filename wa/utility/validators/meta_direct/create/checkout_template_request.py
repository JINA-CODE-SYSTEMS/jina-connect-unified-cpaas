"""
Checkout/Order Details Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Order Details template creation requests.

Per Meta's API, ORDER_DETAILS is a **button type** inside the BUTTONS component.
The CREATE payload does NOT include order data (items, amounts, etc.) — that goes
in the SEND payload. CREATE only defines template structure:
  - HEADER (optional, text or image)
  - BODY (required)
  - FOOTER (optional)
  - BUTTONS (required, single ORDER_DETAILS button)

Categories:
  - UTILITY: button text = "Review and Pay"
  - MARKETING: button text = "Buy now", requires display_format = "order_details"
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.buttons import OrderDetailsButton
from wa.utility.data_model.meta_direct.buttons_component import \
    ButtonsComponent
from wa.utility.data_model.meta_direct.footer import FooterComponent
from wa.utility.data_model.meta_direct.header import (HeaderComponent,
                                                      HeaderHandleExample,
                                                      HeaderTextExample)


# Union type for checkout template components
CheckoutTemplateComponent = Union[
    HeaderComponent,
    BodyComponent,
    FooterComponent,
    ButtonsComponent,
]


class CheckoutTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API Order Details template creation request.

    ORDER_DETAILS templates allow businesses to send order/payment messages
    with a "Review and Pay" or "Buy now" call-to-action button.

    Requirements:
    - Category: UTILITY or MARKETING
    - Body component is required
    - BUTTONS component is required with exactly one ORDER_DETAILS button
    - Header (text or image) and footer are optional
    - For MARKETING: display_format must be "order_details"
    - ORDER_DETAILS button text must match category

    Example (UTILITY):
        >>> data = {
        ...     "name": "order_payment",
        ...     "language": "en_US",
        ...     "category": "UTILITY",
        ...     "components": [
        ...         {"type": "body", "text": "Your order {{1}} is ready for payment."},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "ORDER_DETAILS", "text": "Review and Pay"}
        ...         ]}
        ...     ]
        ... }
        >>> template = CheckoutTemplateRequestValidator(**data)

    Example (MARKETING):
        >>> data = {
        ...     "name": "promo_order",
        ...     "language": "en_US",
        ...     "category": "MARKETING",
        ...     "display_format": "order_details",
        ...     "components": [
        ...         {"type": "body", "text": "Special offer on {{1}}!"},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "ORDER_DETAILS", "text": "Buy now"}
        ...         ]}
        ...     ]
        ... }
        >>> template = CheckoutTemplateRequestValidator(**data)
    """

    category: Literal["UTILITY", "utility", "MARKETING", "marketing"] = Field(
        ...,
        description="Template category (UTILITY or MARKETING for order details)",
    )
    display_format: Optional[Literal["order_details"]] = Field(
        default=None,
        description="Required for MARKETING category. Must be 'order_details'.",
    )
    components: List[CheckoutTemplateComponent] = Field(
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
                elif comp_type == "buttons":
                    if "buttons" in comp:
                        parsed_buttons = []
                        for btn in comp["buttons"]:
                            if isinstance(btn, BaseModel):
                                parsed_buttons.append(btn)
                            elif isinstance(btn, dict):
                                btn_type = btn.get("type", "")
                                if btn_type.upper() == "ORDER_DETAILS":
                                    parsed_buttons.append(OrderDetailsButton(**btn))
                                else:
                                    raise ValueError(
                                        f"Only ORDER_DETAILS button is allowed in order details templates, "
                                        f"got '{btn_type}'"
                                    )
                            else:
                                raise ValueError(
                                    f"Button must be a dictionary, got {type(btn)}"
                                )
                        comp["buttons"] = parsed_buttons
                    parsed.append(ButtonsComponent(**comp))
                else:
                    raise ValueError(
                        f"Unknown component type for order details template: '{comp_type}'. "
                        f"Allowed types: 'header', 'body', 'footer', 'buttons'."
                    )
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_template_structure(self):
        """Validate the overall template structure for order details templates"""
        component_types = [comp.type.lower() for comp in self.components]

        # Body is required
        if "body" not in component_types:
            raise ValueError("Body component is required for order details templates")

        # Buttons is required (must contain ORDER_DETAILS button)
        if "buttons" not in component_types:
            raise ValueError(
                "Buttons component with ORDER_DETAILS button is required for order details templates"
            )

        # Check for duplicates
        for ctype in ("header", "body", "footer", "buttons"):
            if component_types.count(ctype) > 1:
                raise ValueError(f"Only one {ctype} component is allowed")

        # Validate component order: header -> body -> footer -> buttons
        expected_order = ["header", "body", "footer", "buttons"]
        current_order = [t for t in expected_order if t in component_types]
        actual_order = [
            t.lower() for t in component_types if t.lower() in expected_order
        ]
        if current_order != actual_order:
            raise ValueError(
                f"Components must be in order: header -> body -> footer -> buttons. "
                f"Got: {actual_order}"
            )

        # Validate buttons: must have exactly 1 ORDER_DETAILS button
        buttons_comp = next(c for c in self.components if c.type.lower() == "buttons")
        if len(buttons_comp.buttons) != 1:
            raise ValueError(
                "Order details templates must have exactly one button (ORDER_DETAILS)"
            )
        button = buttons_comp.buttons[0]
        if not isinstance(button, OrderDetailsButton):
            raise ValueError(
                f"Order details templates require an ORDER_DETAILS button, "
                f"got {type(button).__name__}"
            )

        # Validate category-specific rules
        cat = self.category.upper()
        if cat == "MARKETING":
            if self.display_format != "order_details":
                raise ValueError(
                    "MARKETING order details templates require display_format='order_details'"
                )
            if button.text != "Buy now":
                raise ValueError(
                    f"MARKETING order details button text must be 'Buy now', got '{button.text}'"
                )
        elif cat == "UTILITY":
            if self.display_format is not None:
                raise ValueError(
                    "UTILITY order details templates must not set display_format"
                )
            if button.text != "Review and Pay":
                raise ValueError(
                    f"UTILITY order details button text must be 'Review and Pay', got '{button.text}'"
                )

        # Validate header format (only TEXT or IMAGE allowed for order details)
        if "header" in component_types:
            header_comp = next(c for c in self.components if c.type.lower() == "header")
            if hasattr(header_comp, "format") and header_comp.format:
                allowed_formats = ("TEXT", "IMAGE")
                if header_comp.format.upper() not in allowed_formats:
                    raise ValueError(
                        f"Order details template header must be TEXT or IMAGE, "
                        f"got '{header_comp.format}'"
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

        if self.display_format:
            payload["display_format"] = self.display_format

        for comp in self.components:
            payload["components"].append(comp.model_dump(exclude_none=True))

        return payload

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "order_payment_utility",
                    "language": "en_US",
                    "category": "UTILITY",
                    "components": [
                        {
                            "type": "body",
                            "text": "Your order {{1}} is ready for payment. Total: ₹{{2}}",
                            "example": {"body_text": [["ORD-12345", "500.00"]]},
                        },
                        {
                            "type": "buttons",
                            "buttons": [
                                {"type": "ORDER_DETAILS", "text": "Review and Pay"}
                            ],
                        },
                    ],
                },
                {
                    "name": "promo_order_marketing",
                    "language": "en_US",
                    "category": "MARKETING",
                    "display_format": "order_details",
                    "components": [
                        {
                            "type": "header",
                            "format": "IMAGE",
                            "example": {"header_handle": ["https://example.com/promo.jpg"]},
                        },
                        {
                            "type": "body",
                            "text": "Special offer: {{1}}! Order now and save {{2}}%",
                            "example": {"body_text": [["Widget Pro", "20"]]},
                        },
                        {
                            "type": "buttons",
                            "buttons": [
                                {"type": "ORDER_DETAILS", "text": "Buy now"}
                            ],
                        },
                    ],
                },
            ]
        }
    }


# ============================================================================
# Utility Functions
# ============================================================================


def validate_checkout_template(data: dict) -> CheckoutTemplateRequestValidator:
    """
    Validate an Order Details template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        CheckoutTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return CheckoutTemplateRequestValidator(**data)


def parse_and_validate_checkout_template(
    json_str: str,
) -> CheckoutTemplateRequestValidator:
    """
    Parse JSON string and validate as Order Details template.

    Args:
        json_str: JSON string containing template data

    Returns:
        CheckoutTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_checkout_template(data)
