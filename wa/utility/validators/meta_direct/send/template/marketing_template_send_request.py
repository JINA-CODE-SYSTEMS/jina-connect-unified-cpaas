"""
Marketing Template Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
marketing template message sending requests.

Based on META's send template message structure:
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


class ImageParameter(BaseModel):
    """Image parameter for header component"""
    id: Optional[str] = Field(None, description="Media ID for the image")
    link: Optional[str] = Field(None, description="URL link for the image")

    @model_validator(mode="after")
    def validate_image_source(self):
        if not self.id and not self.link:
            raise ValueError("Either 'id' or 'link' must be provided for image parameter")
        if self.id and self.link:
            raise ValueError("Only one of 'id' or 'link' should be provided, not both")
        return self


class VideoParameter(BaseModel):
    """Video parameter for header component"""
    id: Optional[str] = Field(None, description="Media ID for the video")
    link: Optional[str] = Field(None, description="URL link for the video")

    @model_validator(mode="after")
    def validate_video_source(self):
        if not self.id and not self.link:
            raise ValueError("Either 'id' or 'link' must be provided for video parameter")
        if self.id and self.link:
            raise ValueError("Only one of 'id' or 'link' should be provided, not both")
        return self


class DocumentParameter(BaseModel):
    """Document parameter for header component"""
    id: Optional[str] = Field(None, description="Media ID for the document")
    link: Optional[str] = Field(None, description="URL link for the document")
    filename: Optional[str] = Field(None, description="Filename for the document")

    @model_validator(mode="after")
    def validate_document_source(self):
        if not self.id and not self.link:
            raise ValueError("Either 'id' or 'link' must be provided for document parameter")
        if self.id and self.link:
            raise ValueError("Only one of 'id' or 'link' should be provided, not both")
        return self


class LocationParameter(BaseModel):
    """Location parameter for header component"""
    latitude: float = Field(..., description="Latitude of the location")
    longitude: float = Field(..., description="Longitude of the location")
    name: Optional[str] = Field(None, description="Name of the location")
    address: Optional[str] = Field(None, description="Address of the location")


class HeaderImageParameter(BaseModel):
    """Header parameter with image"""
    type: Literal["image"] = "image"
    image: ImageParameter


class HeaderVideoParameter(BaseModel):
    """Header parameter with video"""
    type: Literal["video"] = "video"
    video: VideoParameter


class HeaderDocumentParameter(BaseModel):
    """Header parameter with document"""
    type: Literal["document"] = "document"
    document: DocumentParameter


class HeaderTextParameter(BaseModel):
    """Header parameter with text"""
    type: Literal["text"] = "text"
    text: str = Field(..., description="Text value for the header parameter")


class HeaderLocationParameter(BaseModel):
    """Header parameter with location"""
    type: Literal["location"] = "location"
    location: LocationParameter


# Union type for header parameters
HeaderParameter = Union[
    HeaderImageParameter,
    HeaderVideoParameter,
    HeaderDocumentParameter,
    HeaderTextParameter,
    HeaderLocationParameter,
]


class BodyTextParameter(BaseModel):
    """Body parameter with text (named parameter format)"""
    type: Literal["text"] = "text"
    parameter_name: Optional[str] = Field(
        None, description="Parameter name for named parameters"
    )
    text: str = Field(..., description="Text value for the body parameter")


class BodyCurrencyParameter(BaseModel):
    """Body parameter with currency"""
    type: Literal["currency"] = "currency"
    parameter_name: Optional[str] = Field(
        None, description="Parameter name for named parameters"
    )
    currency: dict = Field(
        ...,
        description="Currency object with fallback_value, code, and amount_1000",
    )


class BodyDateTimeParameter(BaseModel):
    """Body parameter with date_time"""
    type: Literal["date_time"] = "date_time"
    parameter_name: Optional[str] = Field(
        None, description="Parameter name for named parameters"
    )
    date_time: dict = Field(
        ..., description="DateTime object with fallback_value"
    )


# Union type for body parameters
BodyParameter = Union[BodyTextParameter, BodyCurrencyParameter, BodyDateTimeParameter]


class ButtonParameter(BaseModel):
    """Button parameter for dynamic URL buttons"""
    type: Literal["text"] = "text"
    text: str = Field(..., description="Dynamic URL suffix")


# ============================================================================
# Component Models
# ============================================================================


class HeaderComponentSend(BaseModel):
    """Header component for sending template message"""
    type: Literal["header"] = "header"
    parameters: List[HeaderParameter] = Field(
        ..., min_length=1, max_length=1, description="Header parameters (exactly 1)"
    )


class BodyComponentSend(BaseModel):
    """Body component for sending template message"""
    type: Literal["body"] = "body"
    parameters: List[BodyParameter] = Field(
        ..., min_length=1, description="Body parameters"
    )


class ButtonComponentSend(BaseModel):
    """Button component for sending template message (for dynamic URL buttons)"""
    type: Literal["button"] = "button"
    sub_type: Literal["url", "quick_reply"] = Field(
        ..., description="Button sub-type"
    )
    index: int = Field(..., ge=0, le=9, description="Button index (0-based)")
    parameters: List[ButtonParameter] = Field(
        ..., min_length=1, max_length=1, description="Button parameters"
    )


# Union type for send components
SendTemplateComponent = Union[
    HeaderComponentSend, BodyComponentSend, ButtonComponentSend
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


class MarketingTemplateSendBody(BaseModel):
    """Template body for marketing template send request"""
    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name",
    )
    language: LanguageInput = Field(..., description="Template language")
    components: Optional[List[SendTemplateComponent]] = Field(
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
                                    if "image" in param and isinstance(
                                        param["image"], dict
                                    ):
                                        param["image"] = ImageParameter(**param["image"])
                                    parsed_params.append(HeaderImageParameter(**param))
                                elif param_type == "video":
                                    if "video" in param and isinstance(
                                        param["video"], dict
                                    ):
                                        param["video"] = VideoParameter(**param["video"])
                                    parsed_params.append(HeaderVideoParameter(**param))
                                elif param_type == "document":
                                    if "document" in param and isinstance(
                                        param["document"], dict
                                    ):
                                        param["document"] = DocumentParameter(
                                            **param["document"]
                                        )
                                    parsed_params.append(HeaderDocumentParameter(**param))
                                elif param_type == "text":
                                    parsed_params.append(HeaderTextParameter(**param))
                                elif param_type == "location":
                                    if "location" in param and isinstance(
                                        param["location"], dict
                                    ):
                                        param["location"] = LocationParameter(
                                            **param["location"]
                                        )
                                    parsed_params.append(HeaderLocationParameter(**param))
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
                elif comp_type == "button":
                    # Parse button parameters
                    if "parameters" in comp:
                        parsed_params = []
                        for param in comp["parameters"]:
                            if isinstance(param, BaseModel):
                                parsed_params.append(param)
                            elif isinstance(param, dict):
                                parsed_params.append(ButtonParameter(**param))
                            else:
                                raise ValueError(
                                    f"Parameter must be a dictionary, got {type(param)}"
                                )
                        comp["parameters"] = parsed_params
                    parsed.append(ButtonComponentSend(**comp))
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed


# ============================================================================
# Main Request Validator
# ============================================================================


class MarketingTemplateSendRequestValidator(BaseModel):
    """
    Validator for META Direct API marketing template send request.

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
        ...         "name": "summer_sale",
        ...         "language": {"code": "en"},
        ...         "components": [
        ...             {"type": "header", "parameters": [
        ...                 {"type": "image", "image": {"id": "123456789"}}
        ...             ]},
        ...             {"type": "body", "parameters": [
        ...                 {"type": "text", "parameter_name": "customer_name", "text": "John"},
        ...                 {"type": "text", "parameter_name": "discount", "text": "20%"}
        ...             ]}
        ...         ]
        ...     }
        ... }
        >>> request = MarketingTemplateSendRequestValidator(**data)
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
    template: MarketingTemplateSendBody = Field(
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
                        "name": "summer_sale_promo",
                        "language": {"code": "en"},
                        "components": [
                            {
                                "type": "header",
                                "parameters": [
                                    {"type": "image", "image": {"id": "123456789"}}
                                ],
                            },
                            {
                                "type": "body",
                                "parameters": [
                                    {
                                        "type": "text",
                                        "parameter_name": "customer_name",
                                        "text": "John",
                                    },
                                    {
                                        "type": "text",
                                        "parameter_name": "sale_name",
                                        "text": "Summer Sale",
                                    },
                                    {
                                        "type": "text",
                                        "parameter_name": "discount",
                                        "text": "20",
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


def validate_marketing_template_send(data: dict) -> MarketingTemplateSendRequestValidator:
    """
    Validate a marketing template send request dictionary.

    Args:
        data: Dictionary containing request data

    Returns:
        MarketingTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
    """
    return MarketingTemplateSendRequestValidator(**data)


def parse_and_validate_marketing_template_send(
    json_str: str,
) -> MarketingTemplateSendRequestValidator:
    """
    Parse JSON string and validate as marketing template send request.

    Args:
        json_str: JSON string containing request data

    Returns:
        MarketingTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_marketing_template_send(data)
