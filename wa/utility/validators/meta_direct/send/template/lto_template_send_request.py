"""
Limited Time Offer (LTO) Template Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Limited Time Offer template message sending requests.

Based on META's send LTO template message structure:
- messaging_product: "whatsapp"
- recipient_type: "individual"
- to: Recipient phone number
- type: "template"
- template: Template details with name, language, and components including
  limited_time_offer with expiration timestamp
"""

import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

# ============================================================================
# Parameter Models
# ============================================================================


class HeaderImageParameter(BaseModel):
    """Header parameter with image"""

    type: Literal["image"] = "image"
    image: dict = Field(
        ...,
        description="Image object with 'link' (URL) or 'id' (media ID)",
    )

    @field_validator("image")
    @classmethod
    def validate_image(cls, v):
        if not v:
            raise ValueError("Image object cannot be empty")
        if "link" not in v and "id" not in v:
            raise ValueError("Image must have either 'link' or 'id'")
        return v


class HeaderVideoParameter(BaseModel):
    """Header parameter with video"""

    type: Literal["video"] = "video"
    video: dict = Field(
        ...,
        description="Video object with 'link' (URL) or 'id' (media ID)",
    )

    @field_validator("video")
    @classmethod
    def validate_video(cls, v):
        if not v:
            raise ValueError("Video object cannot be empty")
        if "link" not in v and "id" not in v:
            raise ValueError("Video must have either 'link' or 'id'")
        return v


class HeaderDocumentParameter(BaseModel):
    """Header parameter with document"""

    type: Literal["document"] = "document"
    document: dict = Field(
        ...,
        description="Document object with 'link' (URL) or 'id' (media ID)",
    )

    @field_validator("document")
    @classmethod
    def validate_document(cls, v):
        if not v:
            raise ValueError("Document object cannot be empty")
        if "link" not in v and "id" not in v:
            raise ValueError("Document must have either 'link' or 'id'")
        return v


# Union type for header parameters
HeaderParameter = Union[HeaderImageParameter, HeaderVideoParameter, HeaderDocumentParameter]


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
    """Button parameter for copy_code buttons"""

    type: Literal["coupon_code"] = "coupon_code"
    coupon_code: str = Field(..., description="The coupon code to copy")


class URLButtonParameter(BaseModel):
    """Button parameter for URL buttons with dynamic suffix"""

    type: Literal["text"] = "text"
    text: str = Field(..., description="Dynamic URL suffix value")


# ============================================================================
# Component Models
# ============================================================================


class HeaderComponentSend(BaseModel):
    """Header component for sending template message"""

    type: Literal["header"] = "header"
    parameters: List[HeaderParameter] = Field(..., min_length=1, max_length=1, description="Header parameters")


class LimitedTimeOfferComponentSend(BaseModel):
    """Limited Time Offer component for sending template message with expiration"""

    type: Literal["limited_time_offer"] = "limited_time_offer"
    parameters: List[dict] = Field(
        ...,
        min_length=1,
        max_length=1,
        description="LTO parameters with expiration_time_ms",
    )

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, v):
        if not v:
            raise ValueError("LTO parameters cannot be empty")
        for param in v:
            if "type" not in param or param["type"] != "limited_time_offer":
                raise ValueError("LTO parameter must have type 'limited_time_offer'")
            if "limited_time_offer" not in param:
                raise ValueError("LTO parameter must have 'limited_time_offer' object")
            lto_data = param["limited_time_offer"]
            if "expiration_time_ms" not in lto_data:
                raise ValueError("limited_time_offer must have 'expiration_time_ms' timestamp")
            # Validate expiration_time_ms is a valid timestamp
            exp_time = lto_data["expiration_time_ms"]
            if not isinstance(exp_time, int) or exp_time <= 0:
                raise ValueError("expiration_time_ms must be a positive integer (Unix timestamp in milliseconds)")
        return v


class BodyComponentSend(BaseModel):
    """Body component for sending template message"""

    type: Literal["body"] = "body"
    parameters: List[BodyParameter] = Field(..., min_length=1, description="Body parameters")


class CopyCodeButtonComponentSend(BaseModel):
    """Button component for copy_code button in send request"""

    type: Literal["button"] = "button"
    sub_type: Literal["copy_code"] = "copy_code"
    index: int = Field(..., ge=0, le=9, description="Button index (0-based)")
    parameters: List[CouponCodeButtonParameter] = Field(
        ..., min_length=1, max_length=1, description="Coupon code button parameters"
    )


class URLButtonComponentSend(BaseModel):
    """Button component for URL button in send request"""

    type: Literal["button"] = "button"
    sub_type: Literal["url"] = "url"
    index: int = Field(..., ge=0, le=9, description="Button index (0-based)")
    parameters: List[URLButtonParameter] = Field(..., min_length=1, max_length=1, description="URL button parameters")


# Union type for send components
SendLTOTemplateComponent = Union[
    HeaderComponentSend,
    LimitedTimeOfferComponentSend,
    BodyComponentSend,
    CopyCodeButtonComponentSend,
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


class LTOTemplateSendBody(BaseModel):
    """Template body for LTO template send request"""

    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name",
    )
    language: LanguageInput = Field(..., description="Template language")
    components: Optional[List[SendLTOTemplateComponent]] = Field(
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
                                if param_type == "image":
                                    parsed_params.append(HeaderImageParameter(**param))
                                elif param_type == "video":
                                    parsed_params.append(HeaderVideoParameter(**param))
                                elif param_type == "document":
                                    parsed_params.append(HeaderDocumentParameter(**param))
                                else:
                                    raise ValueError(f"Unknown header parameter type: {param_type}")
                            else:
                                raise ValueError(f"Parameter must be a dictionary, got {type(param)}")
                        comp["parameters"] = parsed_params
                    parsed.append(HeaderComponentSend(**comp))
                elif comp_type == "limited_time_offer":
                    parsed.append(LimitedTimeOfferComponentSend(**comp))
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
                                    raise ValueError(f"Unknown body parameter type: {param_type}")
                            else:
                                raise ValueError(f"Parameter must be a dictionary, got {type(param)}")
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
                                        parsed_params.append(CouponCodeButtonParameter(**param))
                                    else:
                                        raise ValueError(f"Unknown copy_code button parameter type: {param_type}")
                                else:
                                    raise ValueError(f"Parameter must be a dictionary, got {type(param)}")
                            comp["parameters"] = parsed_params
                        parsed.append(CopyCodeButtonComponentSend(**comp))
                    elif sub_type == "url":
                        # Parse URL button parameters
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
                                        raise ValueError(f"Unknown URL button parameter type: {param_type}")
                                else:
                                    raise ValueError(f"Parameter must be a dictionary, got {type(param)}")
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


class LTOTemplateSendRequestValidator(BaseModel):
    """
    Validator for META Direct API Limited Time Offer template send request.

    Validates the complete message structure including:
    - messaging_product: "whatsapp"
    - recipient_type: "individual"
    - to: Recipient phone number
    - type: "template"
    - template: Template details with name, language, and components
      including limited_time_offer with expiration_time_ms

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "template",
        ...     "template": {
        ...         "name": "flash_sale_offer",
        ...         "language": {"code": "en"},
        ...         "components": [
        ...             {"type": "header", "parameters": [
        ...                 {"type": "image", "image": {"link": "https://example.com/sale.jpg"}}
        ...             ]},
        ...             {"type": "limited_time_offer", "parameters": [
        ...                 {"type": "limited_time_offer",
        ...                  "limited_time_offer": {"expiration_time_ms": 1704067200000}}
        ...             ]},
        ...             {"type": "body", "parameters": [
        ...                 {"type": "text", "text": "50"}
        ...             ]},
        ...             {"type": "button", "sub_type": "copy_code", "index": 0,
        ...              "parameters": [{"type": "coupon_code", "coupon_code": "FLASH50"}]}
        ...         ]
        ...     }
        ... }
        >>> request = LTOTemplateSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field("whatsapp", description="Messaging product (must be 'whatsapp')")
    recipient_type: Literal["individual"] = Field("individual", description="Recipient type (must be 'individual')")
    to: str = Field(..., description="Recipient phone number")
    type: Literal["template"] = Field("template", description="Message type (must be 'template')")
    template: LTOTemplateSendBody = Field(..., description="Template details")

    @field_validator("to")
    @classmethod
    def validate_phone_number(cls, v):
        if not v or not v.strip():
            raise ValueError("Recipient phone number cannot be empty")
        # Remove any non-digit characters except +
        cleaned = re.sub(r"[^\d+]", "", v)
        # Validate phone number format
        if not re.match(r"^\+?[0-9]{10,15}$", cleaned):
            raise ValueError("Invalid phone number format. Must be 10-15 digits, optionally starting with +")
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
                        "name": "flash_sale_offer",
                        "language": {"code": "en"},
                        "components": [
                            {
                                "type": "header",
                                "parameters": [
                                    {
                                        "type": "image",
                                        "image": {"link": "https://example.com/flash-sale.jpg"},
                                    }
                                ],
                            },
                            {
                                "type": "limited_time_offer",
                                "parameters": [
                                    {
                                        "type": "limited_time_offer",
                                        "limited_time_offer": {"expiration_time_ms": 1704067200000},
                                    }
                                ],
                            },
                            {
                                "type": "body",
                                "parameters": [{"type": "text", "text": "50"}],
                            },
                            {
                                "type": "button",
                                "sub_type": "copy_code",
                                "index": 0,
                                "parameters": [{"type": "coupon_code", "coupon_code": "FLASH50"}],
                            },
                            {
                                "type": "button",
                                "sub_type": "url",
                                "index": 1,
                                "parameters": [{"type": "text", "text": "summer2024"}],
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


def validate_lto_template_send(data: dict) -> LTOTemplateSendRequestValidator:
    """
    Validate a Limited Time Offer template send request dictionary.

    Args:
        data: Dictionary containing request data

    Returns:
        LTOTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
    """
    return LTOTemplateSendRequestValidator(**data)


def parse_and_validate_lto_template_send(json_str: str) -> LTOTemplateSendRequestValidator:
    """
    Parse JSON string and validate as LTO template send request.

    Args:
        json_str: JSON string containing request data

    Returns:
        LTOTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_lto_template_send(data)
