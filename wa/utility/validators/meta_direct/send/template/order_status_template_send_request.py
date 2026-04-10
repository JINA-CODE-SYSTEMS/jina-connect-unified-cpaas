"""
Order Status Template Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Order Status template sending requests.

Order Status templates are used to send shipping updates, delivery notifications,
and other order status changes with dynamic information.
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


# Union type for header parameters
HeaderParameter = Union[
    HeaderTextParameter, HeaderImageParameter, HeaderDocumentParameter
]


# ============================================================================
# Order Status Models for Send
# ============================================================================


class ShippingAddressSend(BaseModel):
    """Shipping address for send request"""
    name: str = Field(..., description="Recipient name")
    address: str = Field(..., description="Street address")
    city: str = Field(..., description="City")
    state: Optional[str] = Field(None, description="State/Province")
    postal_code: Optional[str] = Field(None, description="Postal/ZIP code")
    country: str = Field(..., description="Country")


class ShippingInfoSend(BaseModel):
    """Shipping information for send request"""
    carrier: Optional[str] = Field(None, description="Shipping carrier name")
    tracking_number: Optional[str] = Field(None, description="Tracking number")
    tracking_url: Optional[str] = Field(None, description="Tracking URL")
    estimated_delivery: Optional[str] = Field(None, description="Estimated delivery")
    shipping_address: Optional[ShippingAddressSend] = Field(
        None, description="Delivery address"
    )


class OrderStatusDetailsSend(BaseModel):
    """Order status details for send request"""
    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=35,
        description="Order reference/ID",
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
    description: Optional[str] = Field(None, description="Additional status description")
    shipping: Optional[ShippingInfoSend] = Field(None, description="Shipping information")
    updated_at: Optional[str] = Field(None, description="Status update timestamp")


class OrderStatusActionSend(BaseModel):
    """Action containing order status details for send request"""
    name: Literal["order_status"] = Field(
        "order_status",
        description="Action name for order status",
    )
    parameters: OrderStatusDetailsSend = Field(
        ...,
        description="Order status parameters",
    )


# ============================================================================
# Button Parameter Models
# ============================================================================


class URLButtonParameter(BaseModel):
    """Button parameter for URL buttons with dynamic suffix"""
    type: Literal["text"] = "text"
    text: str = Field(..., description="Dynamic URL suffix value")


# ============================================================================
# Component Models for Send
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


class OrderStatusComponentSend(BaseModel):
    """Order status component for send request"""
    type: Literal["order_status"] = "order_status"
    action: OrderStatusActionSend = Field(
        ...,
        description="Order status action with parameters",
    )


class URLButtonComponentSend(BaseModel):
    """URL button component for send request"""
    type: Literal["button"] = "button"
    sub_type: Literal["url"] = "url"
    index: int = Field(..., ge=0, le=2, description="Button index (0-based)")
    parameters: List[URLButtonParameter] = Field(
        ..., min_length=1, max_length=1, description="URL button parameters"
    )


# Union type for send components
SendOrderStatusTemplateComponent = Union[
    HeaderComponentSend,
    BodyComponentSend,
    OrderStatusComponentSend,
    URLButtonComponentSend,
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


class OrderStatusTemplateSendBody(BaseModel):
    """Template body for order status template send request"""
    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name",
    )
    language: LanguageInput = Field(..., description="Template language")
    components: Optional[List[SendOrderStatusTemplateComponent]] = Field(
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
                if comp_type == "header":
                    # Parse header parameters
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
                                    parsed_params.append(
                                        HeaderDocumentParameter(**param)
                                    )
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
                                    shipping_data[
                                        "shipping_address"
                                    ] = ShippingAddressSend(
                                        **shipping_data["shipping_address"]
                                    )
                                params["shipping"] = ShippingInfoSend(**shipping_data)
                            action_data["parameters"] = OrderStatusDetailsSend(**params)
                        comp["action"] = OrderStatusActionSend(**action_data)
                    parsed.append(OrderStatusComponentSend(**comp))
                elif comp_type == "button":
                    sub_type = comp.get("sub_type")
                    if sub_type == "url":
                        if "parameters" in comp:
                            parsed_params = []
                            for param in comp["parameters"]:
                                if isinstance(param, BaseModel):
                                    parsed_params.append(param)
                                elif isinstance(param, dict):
                                    param_type = param.get("type")
                                    if param_type == "text":
                                        parsed_params.append(
                                            URLButtonParameter(**param)
                                        )
                                    else:
                                        raise ValueError(
                                            f"Unknown URL button parameter type: {param_type}"
                                        )
                                else:
                                    raise ValueError(
                                        f"Parameter must be a dictionary, got {type(param)}"
                                    )
                            comp["parameters"] = parsed_params
                        parsed.append(URLButtonComponentSend(**comp))
                    else:
                        raise ValueError(f"Unknown button sub_type: {sub_type}")
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed


# ============================================================================
# Main Request Validator
# ============================================================================


class OrderStatusTemplateSendRequestValidator(BaseModel):
    """
    Validator for META Direct API Order Status template send request.

    Validates the complete message structure including:
    - messaging_product: "whatsapp"
    - recipient_type: "individual"
    - to: Recipient phone number
    - type: "template"
    - template: Template details with name, language, and components
      including order_status with dynamic status information

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "template",
        ...     "template": {
        ...         "name": "order_shipped",
        ...         "language": {"code": "en"},
        ...         "components": [
        ...             {"type": "body", "parameters": [
        ...                 {"type": "text", "text": "John"},
        ...                 {"type": "text", "text": "ORD-12345"},
        ...                 {"type": "text", "text": "FedEx"}
        ...             ]},
        ...             {"type": "order_status", "action": {
        ...                 "name": "order_status",
        ...                 "parameters": {
        ...                     "reference_id": "ORD-12345",
        ...                     "status": "shipped",
        ...                     "shipping": {
        ...                         "carrier": "FedEx",
        ...                         "tracking_number": "789456123"
        ...                     }
        ...                 }
        ...             }},
        ...             {"type": "button", "sub_type": "url", "index": 0,
        ...              "parameters": [{"type": "text", "text": "789456123"}]}
        ...         ]
        ...     }
        ... }
        >>> request = OrderStatusTemplateSendRequestValidator(**data)
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
    template: OrderStatusTemplateSendBody = Field(..., description="Template details")

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
                        "name": "order_shipped_notification",
                        "language": {"code": "en"},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": "John"},
                                    {"type": "text", "text": "ORD-12345"},
                                    {"type": "text", "text": "FedEx"},
                                    {"type": "text", "text": "789456123"},
                                    {"type": "text", "text": "Feb 5, 2026"},
                                ],
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
                                            "tracking_url": "https://fedex.com/track/789456123",
                                            "estimated_delivery": "Feb 5, 2026",
                                        },
                                    },
                                },
                            },
                            {
                                "type": "button",
                                "sub_type": "url",
                                "index": 0,
                                "parameters": [{"type": "text", "text": "789456123"}],
                            },
                        ],
                    },
                },
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": "919876543210",
                    "type": "template",
                    "template": {
                        "name": "order_delivered",
                        "language": {"code": "en"},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": "Jane"},
                                    {"type": "text", "text": "ORD-67890"},
                                    {"type": "text", "text": "Feb 5, 2026 at 2:30 PM"},
                                ],
                            },
                            {
                                "type": "order_status",
                                "action": {
                                    "name": "order_status",
                                    "parameters": {
                                        "reference_id": "ORD-67890",
                                        "status": "delivered",
                                        "description": "Left at front door",
                                        "updated_at": "2026-02-05T14:30:00Z",
                                    },
                                },
                            },
                            {
                                "type": "button",
                                "sub_type": "url",
                                "index": 0,
                                "parameters": [{"type": "text", "text": "ORD-67890"}],
                            },
                        ],
                    },
                },
            ]
        }
    }


# ============================================================================
# Utility Functions
# ============================================================================


def validate_order_status_template_send(
    data: dict,
) -> OrderStatusTemplateSendRequestValidator:
    """
    Validate an Order Status template send request dictionary.

    Args:
        data: Dictionary containing request data

    Returns:
        OrderStatusTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
    """
    return OrderStatusTemplateSendRequestValidator(**data)


def parse_and_validate_order_status_template_send(
    json_str: str,
) -> OrderStatusTemplateSendRequestValidator:
    """
    Parse JSON string and validate as Order Status template send request.

    Args:
        json_str: JSON string containing request data

    Returns:
        OrderStatusTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_order_status_template_send(data)
