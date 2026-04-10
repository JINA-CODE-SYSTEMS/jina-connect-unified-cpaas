"""
Coupon Code Template Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
coupon code template message sending requests.

Based on META's send coupon code template message structure:
- messaging_product: "whatsapp"
- recipient_type: "individual"
- to: Recipient phone number
- type: "template"
- template: Template details with name, language, and components
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


class CouponCodeButtonParameter(BaseModel):
    """Button parameter for copy_code buttons - contains the actual coupon code"""
    type: Literal["coupon_code"] = "coupon_code"
    coupon_code: str = Field(..., description="The coupon code to copy")


class QuickReplyButtonParameter(BaseModel):
    """Button parameter for quick_reply buttons"""
    type: Literal["payload"] = "payload"
    payload: str = Field(..., description="Payload for quick reply button")


# ============================================================================
# Component Models
# ============================================================================


class HeaderTextComponentSend(BaseModel):
    """Header component with text parameter for sending template message"""
    type: Literal["header"] = "header"
    parameters: List[dict] = Field(
        ..., min_length=1, description="Header parameters"
    )


class BodyComponentSend(BaseModel):
    """Body component for sending template message"""
    type: Literal["body"] = "body"
    parameters: List[BodyParameter] = Field(
        ..., min_length=1, description="Body parameters"
    )


class CopyCodeButtonComponentSend(BaseModel):
    """Button component for copy_code button in send request"""
    type: Literal["button"] = "button"
    sub_type: Literal["copy_code"] = "copy_code"
    index: int = Field(..., ge=0, le=9, description="Button index (0-based)")
    parameters: List[CouponCodeButtonParameter] = Field(
        ..., min_length=1, max_length=1, description="Coupon code button parameters"
    )


class QuickReplyButtonComponentSend(BaseModel):
    """Button component for quick_reply button in send request"""
    type: Literal["button"] = "button"
    sub_type: Literal["quick_reply"] = "quick_reply"
    index: int = Field(..., ge=0, le=9, description="Button index (0-based)")
    parameters: List[QuickReplyButtonParameter] = Field(
        ..., min_length=1, max_length=1, description="Quick reply button parameters"
    )


# Union type for send components
SendCouponCodeTemplateComponent = Union[
    HeaderTextComponentSend,
    BodyComponentSend,
    CopyCodeButtonComponentSend,
    QuickReplyButtonComponentSend,
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


class CouponCodeTemplateSendBody(BaseModel):
    """Template body for coupon code template send request"""
    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name",
    )
    language: LanguageInput = Field(..., description="Template language")
    components: Optional[List[SendCouponCodeTemplateComponent]] = Field(
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
                    parsed.append(HeaderTextComponentSend(**comp))
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
                elif comp_type == "button":
                    sub_type = comp.get("sub_type")
                    if sub_type == "copy_code":
                        # Parse copy_code button parameters
                        if "parameters" in comp:
                            parsed_params = []
                            for param in comp["parameters"]:
                                if isinstance(param, BaseModel):
                                    parsed_params.append(param)
                                elif isinstance(param, dict):
                                    param_type = param.get("type")
                                    if param_type == "coupon_code":
                                        parsed_params.append(
                                            CouponCodeButtonParameter(**param)
                                        )
                                    else:
                                        raise ValueError(
                                            f"Unknown copy_code button parameter type: {param_type}"
                                        )
                                else:
                                    raise ValueError(
                                        f"Parameter must be a dictionary, got {type(param)}"
                                    )
                            comp["parameters"] = parsed_params
                        parsed.append(CopyCodeButtonComponentSend(**comp))
                    elif sub_type == "quick_reply":
                        # Parse quick_reply button parameters
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
                                            f"Unknown quick_reply button parameter type: {param_type}"
                                        )
                                else:
                                    raise ValueError(
                                        f"Parameter must be a dictionary, got {type(param)}"
                                    )
                            comp["parameters"] = parsed_params
                        parsed.append(QuickReplyButtonComponentSend(**comp))
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


class CouponCodeTemplateSendRequestValidator(BaseModel):
    """
    Validator for META Direct API coupon code template send request.

    Validates the complete message structure including:
    - messaging_product: "whatsapp"
    - recipient_type: "individual"
    - to: Recipient phone number
    - type: "template"
    - template: Template details with name, language, and components

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "template",
        ...     "template": {
        ...         "name": "discount_coupon",
        ...         "language": {"code": "en"},
        ...         "components": [
        ...             {"type": "body", "parameters": [
        ...                 {"type": "text", "text": "20"},
        ...                 {"type": "text", "text": "December 31"}
        ...             ]},
        ...             {"type": "button", "sub_type": "copy_code", "index": 1,
        ...              "parameters": [
        ...                 {"type": "coupon_code", "coupon_code": "SAVE20OFF"}
        ...             ]}
        ...         ]
        ...     }
        ... }
        >>> request = CouponCodeTemplateSendRequestValidator(**data)
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
    template: CouponCodeTemplateSendBody = Field(..., description="Template details")

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
                        "name": "discount_code_promo",
                        "language": {"code": "en"},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": "20"},
                                    {"type": "text", "text": "December 31"},
                                ],
                            },
                            {
                                "type": "button",
                                "sub_type": "quick_reply",
                                "index": 0,
                                "parameters": [
                                    {"type": "payload", "payload": "SHOP_NOW"}
                                ],
                            },
                            {
                                "type": "button",
                                "sub_type": "copy_code",
                                "index": 1,
                                "parameters": [
                                    {"type": "coupon_code", "coupon_code": "SAVE20OFF"}
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


def validate_coupon_code_template_send(
    data: dict,
) -> CouponCodeTemplateSendRequestValidator:
    """
    Validate a coupon code template send request dictionary.

    Args:
        data: Dictionary containing request data

    Returns:
        CouponCodeTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
    """
    return CouponCodeTemplateSendRequestValidator(**data)


def parse_and_validate_coupon_code_template_send(
    json_str: str,
) -> CouponCodeTemplateSendRequestValidator:
    """
    Parse JSON string and validate as coupon code template send request.

    Args:
        json_str: JSON string containing request data

    Returns:
        CouponCodeTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_coupon_code_template_send(data)
