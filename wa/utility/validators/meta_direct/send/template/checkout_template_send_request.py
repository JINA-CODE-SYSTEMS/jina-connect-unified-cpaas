"""
Order Details Template Send Request Validator for META Direct API

This module validates the SEND payload for order details template messages.

Per Meta's API, order details are sent as a button component with
sub_type "order_details" and an action parameter containing the full
order data (items, amounts, payment settings).

Correct SEND structure:
    template.components = [
        { type: "header", parameters: [...] },          # optional
        { type: "body", parameters: [...] },             # optional (for vars)
        {
            type: "button",
            sub_type: "order_details",
            index: 0,
            parameters: [{
                type: "action",
                action: {
                    order_details: {
                        currency: "INR",
                        type: "digital-goods" | "physical-goods",
                        reference_id: "...",
                        payment_configuration: "...",
                        total_amount: { value, offset },
                        order: { status, items, subtotal, tax, shipping, discount },
                        payment_settings: [...]
                    }
                }
            }]
        }
    ]
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from wa.utility.data_model.shared.order_models import (
    OrderAmount,
    OrderItem,
    PaymentGatewayConfig,
    PaymentSettings,
)


# ============================================================================
# Header / Body Parameter Models (for template variable substitution)
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


BodyParameter = Union[BodyTextParameter, BodyCurrencyParameter, BodyDateTimeParameter]


class HeaderTextParameter(BaseModel):
    """Header parameter with text"""
    type: Literal["text"] = "text"
    text: str = Field(..., description="Text value for the header parameter")


class HeaderImageParameter(BaseModel):
    """Header parameter with image"""
    type: Literal["image"] = "image"
    image: dict = Field(
        ...,
        description="Image object with 'link' (URL) or 'id' (media ID)",
    )


class HeaderDocumentParameter(BaseModel):
    """Header parameter with document"""
    type: Literal["document"] = "document"
    document: dict = Field(
        ...,
        description="Document object with 'link' (URL) or 'id' (media ID)",
    )


HeaderParameter = Union[HeaderTextParameter, HeaderImageParameter, HeaderDocumentParameter]


# ============================================================================
# Order Details Models for SEND
# ============================================================================


class OrderData(BaseModel):
    """Order block inside the order_details action payload."""
    status: Literal["pending"] = Field(
        default="pending",
        description="Order status. Must be 'pending' for new order details templates.",
    )
    catalog_id: Optional[str] = Field(
        default=None,
        description="Catalog ID from Meta Commerce Manager.",
    )
    order_type: Optional[Literal["ORDER"]] = Field(
        default=None,
        description="Order type. Only 'ORDER' is supported.",
    )
    items: List[OrderItem] = Field(
        ...,
        min_length=1,
        max_length=999,
        description="List of order items (min 1).",
    )
    subtotal: OrderAmount = Field(..., description="Order subtotal.")
    tax: Optional[OrderAmount] = Field(default=None, description="Tax amount.")
    shipping: Optional[OrderAmount] = Field(default=None, description="Shipping cost.")
    discount: Optional[OrderAmount] = Field(default=None, description="Discount amount.")
    expiration: Optional[dict] = Field(
        default=None,
        description="Expiration details, e.g., {'timestamp': '...', 'description': '...'}.",
    )
    order_index: Optional[int] = Field(
        default=None,
        ge=1,
        description="Index for multi-order messages.",
    )


class ShippingAddress(BaseModel):
    """Shipping address for physical-goods orders."""
    name: Optional[str] = Field(default=None, description="Recipient name.")
    phone_number: Optional[str] = Field(default=None, description="Recipient phone.")
    address: Optional[str] = Field(default=None, description="Street address.")
    city: Optional[str] = Field(default=None, description="City.")
    state: Optional[str] = Field(default=None, description="State / province.")
    pincode: Optional[str] = Field(default=None, description="PIN / ZIP code.")
    country: Optional[str] = Field(default=None, description="Country code (e.g., 'IN').")


class ShippingInfo(BaseModel):
    """Shipping information for physical-goods orders."""
    address: Optional[ShippingAddress] = Field(default=None, description="Shipping address.")


class ImporterAddress(BaseModel):
    """Importer address for India compliance."""
    address_line1: Optional[str] = Field(default=None)
    address_line2: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None)
    pincode: Optional[str] = Field(default=None)
    country_code: Optional[str] = Field(default=None)


class OrderDetailsPayload(BaseModel):
    """
    The order_details object inside action.

    Contains all order information: items, amounts, payment settings.
    Supports arithmetic validation: total_amount = subtotal + tax + shipping - discount.
    Currency is restricted to INR (India Payments).
    """
    currency: Literal["INR"] = Field(
        default="INR",
        description="Currency code. Only INR is currently supported.",
    )
    type: Literal["digital-goods", "physical-goods"] = Field(
        ...,
        description="Order type. 'physical-goods' requires shipping_info.",
    )
    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=35,
        description="Unique order reference ID.",
    )
    payment_configuration: str = Field(
        ...,
        min_length=1,
        description="Payment configuration name registered with Meta.",
    )
    total_amount: OrderAmount = Field(
        ...,
        description="Total order amount including tax and shipping minus discount.",
    )
    order: OrderData = Field(
        ...,
        description="Order details: items, subtotal, tax, shipping, discount.",
    )
    payment_settings: List[PaymentSettings] = Field(
        ...,
        min_length=1,
        description="Payment gateway configurations.",
    )
    # Optional fields
    thumbnail_product_retailer_id: Optional[str] = Field(
        default=None,
        description="Product retailer ID for the thumbnail image.",
    )
    shipping_info: Optional[ShippingInfo] = Field(
        default=None,
        description="Shipping info. Required for physical-goods.",
    )
    importer_name: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Importer name (India compliance).",
    )
    importer_address: Optional[ImporterAddress] = Field(
        default=None,
        description="Importer address (India compliance).",
    )
    country_of_origin: Optional[str] = Field(
        default=None,
        max_length=10,
        description="Country of origin code (e.g., 'IN').",
    )
    sale_amount: Optional[OrderAmount] = Field(
        default=None,
        description="Overall sale amount if different from total.",
    )

    @model_validator(mode="after")
    def validate_order_details(self):
        """Validate order details business rules."""
        order = self.order

        # Arithmetic validation: total = subtotal + tax + shipping - discount
        expected_total = order.subtotal.value
        if order.tax:
            expected_total += order.tax.value
        if order.shipping:
            expected_total += order.shipping.value
        if order.discount:
            expected_total -= order.discount.value

        if self.total_amount.value != expected_total:
            raise ValueError(
                f"total_amount ({self.total_amount.value}) does not match "
                f"subtotal ({order.subtotal.value}) + tax ({order.tax.value if order.tax else 0}) "
                f"+ shipping ({order.shipping.value if order.shipping else 0}) "
                f"- discount ({order.discount.value if order.discount else 0}) "
                f"= {expected_total}"
            )

        # physical-goods must have shipping_info
        if self.type == "physical-goods" and not self.shipping_info:
            raise ValueError("shipping_info is required for physical-goods orders")

        return self


class OrderDetailsActionWrapper(BaseModel):
    """Wrapper containing the order_details key inside the action."""
    order_details: OrderDetailsPayload = Field(
        ..., description="Full order details payload."
    )


class OrderDetailsActionParameter(BaseModel):
    """The action parameter inside the button component."""
    type: Literal["action"] = "action"
    action: OrderDetailsActionWrapper = Field(
        ..., description="Action wrapper containing order_details."
    )


class OrderDetailsButtonComponentSend(BaseModel):
    """Button component with sub_type order_details for SEND payload."""
    type: Literal["button"] = "button"
    sub_type: Literal["order_details"] = "order_details"
    index: Literal[0] = Field(
        default=0,
        description="Button index. Must be 0 (ORDER_DETAILS is the sole button).",
    )
    parameters: List[OrderDetailsActionParameter] = Field(
        ...,
        min_length=1,
        max_length=1,
        description="Must contain exactly one action parameter.",
    )


# ============================================================================
# Send Component Models
# ============================================================================


class HeaderComponentSend(BaseModel):
    """Header component for sending template message"""
    type: Literal["header"] = "header"
    parameters: List[HeaderParameter] = Field(
        ..., min_length=1, max_length=1, description="Header parameters"
    )


class BodyComponentSend(BaseModel):
    """Body component for sending template message"""
    type: Literal["body"] = "body"
    parameters: List[BodyParameter] = Field(
        ..., min_length=1, description="Body parameters"
    )


SendCheckoutTemplateComponent = Union[
    HeaderComponentSend,
    BodyComponentSend,
    OrderDetailsButtonComponentSend,
]


# ============================================================================
# Language & Template Body
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


class CheckoutTemplateSendBody(BaseModel):
    """Template body for order details template send request"""
    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name",
    )
    language: LanguageInput = Field(..., description="Template language")
    components: List[SendCheckoutTemplateComponent] = Field(
        ..., min_length=1, description="Template components with parameter values"
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

            try:
                if comp_type == "header":
                    if "parameters" in comp:
                        parsed_params = []
                        for param in comp["parameters"]:
                            if isinstance(param, BaseModel):
                                parsed_params.append(param)
                            elif isinstance(param, dict):
                                param_type = param.get("type")
                                if param_type == "text":
                                    parsed_params.append(HeaderTextParameter(**param))
                                elif param_type == "image":
                                    parsed_params.append(HeaderImageParameter(**param))
                                elif param_type == "document":
                                    parsed_params.append(HeaderDocumentParameter(**param))
                                else:
                                    raise ValueError(
                                        f"Unknown header parameter type: {param_type}"
                                    )
                            else:
                                raise ValueError(
                                    f"Parameter must be a dictionary, got {type(param)}"
                                )
                        comp["parameters"] = parsed_params
                    parsed.append(HeaderComponentSend(**comp))
                elif comp_type == "body":
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
                elif comp_type == "button":
                    sub_type = comp.get("sub_type")
                    if sub_type == "order_details":
                        # Parse the action parameter
                        if "parameters" in comp:
                            parsed_params = []
                            for param in comp["parameters"]:
                                if isinstance(param, BaseModel):
                                    parsed_params.append(param)
                                elif isinstance(param, dict):
                                    parsed_params.append(
                                        OrderDetailsActionParameter(**param)
                                    )
                                else:
                                    raise ValueError(
                                        f"Parameter must be a dictionary, got {type(param)}"
                                    )
                            comp["parameters"] = parsed_params
                        parsed.append(OrderDetailsButtonComponentSend(**comp))
                    else:
                        raise ValueError(
                            f"Only 'order_details' sub_type is supported for order details "
                            f"template buttons, got '{sub_type}'"
                        )
                else:
                    raise ValueError(
                        f"Unknown component type: '{comp_type}'. "
                        f"Allowed: 'header', 'body', 'button'"
                    )
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_send_structure(self):
        """Validate that the order_details button component is present."""
        has_order_button = any(
            isinstance(c, OrderDetailsButtonComponentSend) for c in self.components
        )
        if not has_order_button:
            raise ValueError(
                "Order details template send must include a button component "
                "with sub_type 'order_details'"
            )
        return self


# ============================================================================
# Main Request Validator
# ============================================================================


class CheckoutTemplateSendRequestValidator(BaseModel):
    """
    Validator for META Direct API Order Details template send request.

    Validates the complete message structure:
    - messaging_product: "whatsapp"
    - recipient_type: "individual"
    - to: Recipient phone number
    - type: "template"
    - template: Template with name, language, and components including
      a button component with sub_type "order_details"

    Example:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "to": "919876543210",
        ...     "type": "template",
        ...     "template": {
        ...         "name": "order_payment",
        ...         "language": {"code": "en_US"},
        ...         "components": [
        ...             {"type": "button", "sub_type": "order_details", "index": 0,
        ...              "parameters": [{"type": "action", "action": {
        ...                  "order_details": {
        ...                      "currency": "INR", "type": "digital-goods",
        ...                      "reference_id": "ORD-123",
        ...                      "payment_configuration": "my_config",
        ...                      "total_amount": {"value": 50000, "offset": 100},
        ...                      "order": {
        ...                          "status": "pending",
        ...                          "items": [{"name": "Item", "amount": {"value": 50000, "offset": 100}, "quantity": 1}],
        ...                          "subtotal": {"value": 50000, "offset": 100}
        ...                      },
        ...                      "payment_settings": [{"type": "payment_gateway",
        ...                          "payment_gateway": {"type": "razorpay", "configuration_name": "cfg"}}]
        ...                  }
        ...              }}]
        ...             }
        ...         ]
        ...     }
        ... }
        >>> request = CheckoutTemplateSendRequestValidator(**data)
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
    template: CheckoutTemplateSendBody = Field(..., description="Template details")

    @field_validator("to")
    @classmethod
    def validate_phone_number(cls, v):
        if not v or not v.strip():
            raise ValueError("Recipient phone number cannot be empty")
        cleaned = re.sub(r"[^\d+]", "", v)
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
                        "name": "order_payment",
                        "language": {"code": "en_US"},
                        "components": [
                            {
                                "type": "button",
                                "sub_type": "order_details",
                                "index": 0,
                                "parameters": [
                                    {
                                        "type": "action",
                                        "action": {
                                            "order_details": {
                                                "currency": "INR",
                                                "type": "digital-goods",
                                                "reference_id": "ORD-12345",
                                                "payment_configuration": "razorpay_config",
                                                "total_amount": {"value": 42500, "offset": 100},
                                                "order": {
                                                    "status": "pending",
                                                    "items": [
                                                        {
                                                            "name": "Wireless Headphones",
                                                            "amount": {"value": 40000, "offset": 100},
                                                            "quantity": 1,
                                                        }
                                                    ],
                                                    "subtotal": {"value": 40000, "offset": 100},
                                                    "tax": {"value": 5000, "offset": 100},
                                                    "discount": {"value": 2500, "offset": 100},
                                                },
                                                "payment_settings": [
                                                    {
                                                        "type": "payment_gateway",
                                                        "payment_gateway": {
                                                            "type": "razorpay",
                                                            "configuration_name": "my-razorpay-config",
                                                        },
                                                    }
                                                ],
                                            }
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                }
            ]
        }
    }


# ============================================================================
# Utility Functions
# ============================================================================


def validate_checkout_template_send(
    data: dict,
) -> CheckoutTemplateSendRequestValidator:
    """
    Validate an Order Details template send request dictionary.

    Args:
        data: Dictionary containing request data

    Returns:
        CheckoutTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
    """
    return CheckoutTemplateSendRequestValidator(**data)


def parse_and_validate_checkout_template_send(
    json_str: str,
) -> CheckoutTemplateSendRequestValidator:
    """
    Parse JSON string and validate as Order Details template send request.

    Args:
        json_str: JSON string containing request data

    Returns:
        CheckoutTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_checkout_template_send(data)
