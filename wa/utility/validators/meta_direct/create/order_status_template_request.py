"""
Order Status Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Order Status template creation requests.

Order Status templates are used to send shipping updates, delivery notifications,
and other order status changes to customers.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from wa.utility.data_model.meta_direct.body import (BodyComponent,
                                                    BodyTextExample)
from wa.utility.validators.meta_direct.create.base_validator import BaseTemplateValidator
from wa.utility.data_model.meta_direct.buttons import (PhoneNumberButton,
                                                       QuickReplyButton,
                                                       URLButton)
from wa.utility.data_model.meta_direct.buttons_component import \
    ButtonsComponent
from wa.utility.data_model.meta_direct.footer import FooterComponent
from wa.utility.data_model.meta_direct.header import (HeaderComponent,
                                                      HeaderHandleExample,
                                                      HeaderTextExample)

# ============================================================================
# Order Status Specific Models
# ============================================================================


class OrderStatusType(BaseModel):
    """Order status type enumeration"""
    status: Literal[
        "pending",
        "processing",
        "confirmed",
        "shipped",
        "out_for_delivery",
        "delivered",
        "cancelled",
        "returned",
        "refunded",
        "failed",
        "on_hold",
    ] = Field(..., description="Current order status")


class ShippingAddress(BaseModel):
    """Shipping address for order status"""
    name: str = Field(..., min_length=1, max_length=100, description="Recipient name")
    address: str = Field(..., min_length=1, max_length=256, description="Street address")
    city: str = Field(..., min_length=1, max_length=100, description="City")
    state: Optional[str] = Field(None, max_length=100, description="State/Province")
    postal_code: Optional[str] = Field(None, max_length=20, description="Postal/ZIP code")
    country: str = Field(..., min_length=2, max_length=100, description="Country")


class ShippingInfo(BaseModel):
    """Shipping information for order status updates"""
    carrier: Optional[str] = Field(
        None,
        max_length=100,
        description="Shipping carrier name (e.g., 'FedEx', 'UPS', 'DHL')",
    )
    tracking_number: Optional[str] = Field(
        None,
        max_length=100,
        description="Tracking number for the shipment",
    )
    tracking_url: Optional[str] = Field(
        None,
        max_length=2000,
        description="URL to track the shipment",
    )
    estimated_delivery: Optional[str] = Field(
        None,
        description="Estimated delivery date/time",
    )
    shipping_address: Optional[ShippingAddress] = Field(
        None,
        description="Delivery address",
    )


class OrderStatusDetails(BaseModel):
    """Order status details for the template"""
    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=35,
        description="Order reference/ID (max 35 characters)",
    )
    status: Literal[
        "pending",
        "processing",
        "confirmed",
        "shipped",
        "out_for_delivery",
        "delivered",
        "cancelled",
        "returned",
        "refunded",
        "failed",
        "on_hold",
    ] = Field(..., description="Current order status")
    description: Optional[str] = Field(
        None,
        max_length=256,
        description="Additional status description",
    )
    shipping: Optional[ShippingInfo] = Field(
        None,
        description="Shipping information (for shipped/delivered statuses)",
    )
    updated_at: Optional[str] = Field(
        None,
        description="Status update timestamp",
    )


class OrderStatusAction(BaseModel):
    """Action containing order status details"""
    name: Literal["order_status"] = Field(
        "order_status",
        description="Action name for order status",
    )
    parameters: OrderStatusDetails = Field(
        ...,
        description="Order status parameters",
    )


class OrderStatusComponent(BaseModel):
    """Order status component for the template"""
    type: Literal["order_status", "ORDER_STATUS"] = "order_status"
    action: OrderStatusAction = Field(
        ...,
        description="Order status action with parameters",
    )


# ============================================================================
# Button Types for Order Status
# ============================================================================


class OrderStatusButtonsComponent(BaseModel):
    """Buttons component for order status templates"""
    type: Literal["buttons"] = "buttons"
    buttons: List[Union[URLButton, QuickReplyButton, PhoneNumberButton]] = Field(
        ..., min_length=1, max_length=3, description="List of buttons (max 3)"
    )


# Union type for order status template components
OrderStatusTemplateComponent = Union[
    HeaderComponent,
    BodyComponent,
    FooterComponent,
    OrderStatusComponent,
    OrderStatusButtonsComponent,
]


class OrderStatusTemplateRequestValidator(BaseTemplateValidator):
    """
    Validator for META Direct API Order Status template creation request.

    Order Status templates are used to notify customers about their order status:
    - Order confirmed
    - Order shipped
    - Out for delivery
    - Order delivered
    - Order cancelled/returned

    Requirements:
    - Category must be "utility" (transactional)
    - Body component is required
    - Order status component is optional but recommended
    - Header and footer are optional
    - Buttons are optional (for tracking links, support contact, etc.)

    Example usage:
        >>> data = {
        ...     "name": "order_shipped",
        ...     "language": "en",
        ...     "category": "utility",
        ...     "components": [
        ...         {"type": "header", "format": "text", "text": "Your Order Has Shipped! 📦"},
        ...         {"type": "body", "text": "Hi {{1}}, great news! Your order #{{2}} has been shipped via {{3}}.",
        ...          "example": {"body_text": [["John", "ORD123", "FedEx"]]}},
        ...         {"type": "order_status", "action": {
        ...             "name": "order_status",
        ...             "parameters": {
        ...                 "reference_id": "ORD123",
        ...                 "status": "shipped",
        ...                 "shipping": {
        ...                     "carrier": "FedEx",
        ...                     "tracking_number": "123456789",
        ...                     "estimated_delivery": "Feb 5, 2026"
        ...                 }
        ...             }
        ...         }},
        ...         {"type": "footer", "text": "Track your package using the link below."},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "url", "text": "Track Package", "url": "https://example.com/track/{{1}}"}
        ...         ]}
        ...     ]
        ... }
        >>> template = OrderStatusTemplateRequestValidator(**data)
    """

    category: Literal["UTILITY", "utility"] = Field(
        ...,
        description="Template category (must be 'utility' for order status templates)",
    )
    components: List[OrderStatusTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (header, body, order_status, footer, buttons)",
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
                elif comp_type == "order_status":
                    # Parse order status action
                    if "action" in comp and isinstance(comp["action"], dict):
                        action_data = comp["action"]
                        if "parameters" in action_data and isinstance(
                            action_data["parameters"], dict
                        ):
                            params = action_data["parameters"]
                            # Parse shipping info if present
                            if "shipping" in params and isinstance(
                                params["shipping"], dict
                            ):
                                shipping_data = params["shipping"]
                                if "shipping_address" in shipping_data and isinstance(
                                    shipping_data["shipping_address"], dict
                                ):
                                    shipping_data["shipping_address"] = ShippingAddress(
                                        **shipping_data["shipping_address"]
                                    )
                                params["shipping"] = ShippingInfo(**shipping_data)
                            action_data["parameters"] = OrderStatusDetails(**params)
                        comp["action"] = OrderStatusAction(**action_data)
                    parsed.append(OrderStatusComponent(**comp))
                elif comp_type == "buttons":
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
                                elif btn_type == "quick_reply":
                                    parsed_buttons.append(QuickReplyButton(**btn))
                                elif btn_type == "phone_number":
                                    parsed_buttons.append(PhoneNumberButton(**btn))
                                else:
                                    raise ValueError(
                                        f"Unsupported button type for order status: {btn_type}"
                                    )
                            else:
                                raise ValueError(
                                    f"Button must be a dictionary, got {type(btn)}"
                                )
                        comp["buttons"] = parsed_buttons
                    parsed.append(OrderStatusButtonsComponent(**comp))
                else:
                    raise ValueError(
                        f"Unknown component type for order status template: {comp_type}. "
                        f"Allowed types: 'header', 'body', 'footer', 'order_status', 'buttons'."
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
            raise ValueError("Body component is required for order status templates")

        # Check for duplicates
        if component_types.count("header") > 1:
            raise ValueError("Only one header component is allowed")
        if component_types.count("body") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("footer") > 1:
            raise ValueError("Only one footer component is allowed")
        if component_types.count("order_status") > 1:
            raise ValueError("Only one order_status component is allowed")
        if component_types.count("buttons") > 1:
            raise ValueError("Only one buttons component is allowed")

        # Validate component order: header -> body -> order_status -> footer -> buttons
        expected_order = ["header", "body", "order_status", "footer", "buttons"]
        current_order = [t for t in expected_order if t in component_types]
        actual_order = [
            t.lower() for t in component_types if t.lower() in expected_order
        ]

        if current_order != actual_order:
            raise ValueError(
                f"Components must be in order: header -> body -> order_status -> footer -> buttons. "
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
                    "name": "order_shipped_notification",
                    "language": "en",
                    "category": "utility",
                    "components": [
                        {
                            "type": "header",
                            "format": "text",
                            "text": "Your Order Has Shipped! 📦",
                        },
                        {
                            "type": "body",
                            "text": "Hi {{1}}, great news! Your order #{{2}} has been shipped.\n\nCarrier: {{3}}\nTracking: {{4}}\nEstimated Delivery: {{5}}",
                            "example": {
                                "body_text": [
                                    [
                                        "John",
                                        "ORD-12345",
                                        "FedEx",
                                        "789456123",
                                        "Feb 5, 2026",
                                    ]
                                ]
                            },
                        },
                        {
                            "type": "order_status",
                            "action": {
                                "name": "order_status",
                                "parameters": {
                                    "reference_id": "ORD-12345",
                                    "status": "shipped",
                                    "shipping": {
                                        "carrier": "FedEx",
                                        "tracking_number": "789456123",
                                        "estimated_delivery": "Feb 5, 2026",
                                    },
                                },
                            },
                        },
                        {
                            "type": "footer",
                            "text": "Track your package using the button below.",
                        },
                        {
                            "type": "buttons",
                            "buttons": [
                                {
                                    "type": "url",
                                    "text": "Track Package",
                                    "url": "https://example.com/track/{{1}}",
                                    "example": ["789456123"],
                                },
                                {
                                    "type": "phone_number",
                                    "text": "Contact Support",
                                    "phone_number": "+1234567890",
                                },
                            ],
                        },
                    ],
                },
                {
                    "name": "order_delivered",
                    "language": "en",
                    "category": "utility",
                    "components": [
                        {
                            "type": "header",
                            "format": "text",
                            "text": "Order Delivered! ✅",
                        },
                        {
                            "type": "body",
                            "text": "Hi {{1}}, your order #{{2}} has been delivered!\n\nDelivered on: {{3}}\n\nWe hope you love your purchase!",
                            "example": {
                                "body_text": [
                                    ["John", "ORD-12345", "Feb 5, 2026 at 2:30 PM"]
                                ]
                            },
                        },
                        {
                            "type": "order_status",
                            "action": {
                                "name": "order_status",
                                "parameters": {
                                    "reference_id": "ORD-12345",
                                    "status": "delivered",
                                    "description": "Left at front door",
                                },
                            },
                        },
                        {"type": "footer", "text": "Thank you for shopping with us!"},
                        {
                            "type": "buttons",
                            "buttons": [
                                {
                                    "type": "url",
                                    "text": "Rate Your Experience",
                                    "url": "https://example.com/review/{{1}}",
                                    "example": ["ORD-12345"],
                                }
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


def validate_order_status_template(data: dict) -> OrderStatusTemplateRequestValidator:
    """
    Validate an Order Status template dictionary.

    Args:
        data: Dictionary containing template data

    Returns:
        OrderStatusTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
    """
    return OrderStatusTemplateRequestValidator(**data)


def parse_and_validate_order_status_template(
    json_str: str,
) -> OrderStatusTemplateRequestValidator:
    """
    Parse JSON string and validate as Order Status template.

    Args:
        json_str: JSON string containing template data

    Returns:
        OrderStatusTemplateRequestValidator: Validated template object

    Raises:
        ValidationError: If template data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_order_status_template(data)
