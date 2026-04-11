"""
Multi-Product Message (MPM) Template Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Multi-Product Message template sending requests.

MPM templates allow businesses to send messages showcasing multiple products
from their catalog.
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


# Union type for header parameters
HeaderParameter = Union[HeaderTextParameter, HeaderImageParameter, HeaderVideoParameter]


# ============================================================================
# Product Section Models for Send
# ============================================================================


class ProductItemSend(BaseModel):
    """A product item in the MPM send request"""

    product_retailer_id: str = Field(
        ...,
        min_length=1,
        description="Product retailer ID from the catalog",
    )


class ProductSectionSend(BaseModel):
    """A section containing products in the MPM send request"""

    title: Optional[str] = Field(
        None,
        max_length=24,
        description="Section title (max 24 characters, optional)",
    )
    product_items: List[ProductItemSend] = Field(
        ...,
        min_length=1,
        max_length=30,
        description="List of products in this section",
    )


class MPMActionSend(BaseModel):
    """Action configuration for MPM send request"""

    catalog_id: str = Field(
        ...,
        description="The catalog ID containing the products",
    )
    thumbnail_product_retailer_id: Optional[str] = Field(
        None,
        description="Product ID to use as thumbnail",
    )
    sections: List[ProductSectionSend] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Product sections (max 10 sections)",
    )

    @model_validator(mode="after")
    def validate_total_products(self):
        """Validate total products across all sections"""
        total_products = sum(len(section.product_items) for section in self.sections)
        if total_products > 30:
            raise ValueError(f"Total products across all sections cannot exceed 30. Got {total_products}")
        return self


# ============================================================================
# Component Models
# ============================================================================


class HeaderComponentSend(BaseModel):
    """Header component for sending MPM template message"""

    type: Literal["header"] = "header"
    parameters: List[HeaderParameter] = Field(..., min_length=1, max_length=1, description="Header parameters")


class BodyComponentSend(BaseModel):
    """Body component for sending MPM template message"""

    type: Literal["body"] = "body"
    parameters: List[BodyParameter] = Field(..., min_length=1, description="Body parameters")


class MPMComponentSend(BaseModel):
    """Multi-Product Message component for send request"""

    type: Literal["product_list"] = "product_list"
    action: MPMActionSend = Field(
        ...,
        description="MPM action with catalog ID and product sections",
    )


# Union type for send components
SendMPMTemplateComponent = Union[HeaderComponentSend, BodyComponentSend, MPMComponentSend]


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


class MPMTemplateSendBody(BaseModel):
    """Template body for MPM template send request"""

    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name",
    )
    language: LanguageInput = Field(..., description="Template language")
    components: Optional[List[SendMPMTemplateComponent]] = Field(
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
                                elif param_type == "video":
                                    parsed_params.append(HeaderVideoParameter(**param))
                                else:
                                    raise ValueError(f"Unknown header parameter type: {param_type}")
                            else:
                                raise ValueError(f"Parameter must be a dictionary, got {type(param)}")
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
                                    raise ValueError(f"Unknown body parameter type: {param_type}")
                            else:
                                raise ValueError(f"Parameter must be a dictionary, got {type(param)}")
                        comp["parameters"] = parsed_params
                    parsed.append(BodyComponentSend(**comp))
                elif comp_type == "product_list":
                    # Parse MPM action
                    if "action" in comp and isinstance(comp["action"], dict):
                        action_data = comp["action"]
                        # Parse sections
                        if "sections" in action_data:
                            parsed_sections = []
                            for section in action_data["sections"]:
                                if isinstance(section, BaseModel):
                                    parsed_sections.append(section)
                                elif isinstance(section, dict):
                                    # Parse product items
                                    if "product_items" in section:
                                        parsed_items = []
                                        for item in section["product_items"]:
                                            if isinstance(item, BaseModel):
                                                parsed_items.append(item)
                                            elif isinstance(item, dict):
                                                parsed_items.append(ProductItemSend(**item))
                                            else:
                                                raise ValueError(f"Product item must be a dictionary, got {type(item)}")
                                        section["product_items"] = parsed_items
                                    parsed_sections.append(ProductSectionSend(**section))
                                else:
                                    raise ValueError(f"Section must be a dictionary, got {type(section)}")
                            action_data["sections"] = parsed_sections
                        comp["action"] = MPMActionSend(**action_data)
                    parsed.append(MPMComponentSend(**comp))
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed


# ============================================================================
# Main Request Validator
# ============================================================================


class MPMTemplateSendRequestValidator(BaseModel):
    """
    Validator for META Direct API Multi-Product Message template send request.

    Validates the complete message structure including:
    - messaging_product: "whatsapp"
    - recipient_type: "individual"
    - to: Recipient phone number
    - type: "template"
    - template: Template details with name, language, and components
      including product_list with catalog_id and sections

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "template",
        ...     "template": {
        ...         "name": "featured_products",
        ...         "language": {"code": "en"},
        ...         "components": [
        ...             {"type": "body", "parameters": [
        ...                 {"type": "text", "text": "John"}
        ...             ]},
        ...             {"type": "product_list", "action": {
        ...                 "catalog_id": "123456789",
        ...                 "sections": [
        ...                     {"title": "Best Sellers", "product_items": [
        ...                         {"product_retailer_id": "SKU001"},
        ...                         {"product_retailer_id": "SKU002"}
        ...                     ]}
        ...                 ]
        ...             }}
        ...         ]
        ...     }
        ... }
        >>> request = MPMTemplateSendRequestValidator(**data)
    """

    messaging_product: Literal["whatsapp"] = Field("whatsapp", description="Messaging product (must be 'whatsapp')")
    recipient_type: Literal["individual"] = Field("individual", description="Recipient type (must be 'individual')")
    to: str = Field(..., description="Recipient phone number")
    type: Literal["template"] = Field("template", description="Message type (must be 'template')")
    template: MPMTemplateSendBody = Field(..., description="Template details")

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
                        "name": "featured_products",
                        "language": {"code": "en"},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [{"type": "text", "text": "John"}],
                            },
                            {
                                "type": "product_list",
                                "action": {
                                    "catalog_id": "123456789012",
                                    "thumbnail_product_retailer_id": "SKU001",
                                    "sections": [
                                        {
                                            "title": "Best Sellers",
                                            "product_items": [
                                                {"product_retailer_id": "SKU001"},
                                                {"product_retailer_id": "SKU002"},
                                                {"product_retailer_id": "SKU003"},
                                            ],
                                        },
                                        {
                                            "title": "New Arrivals",
                                            "product_items": [
                                                {"product_retailer_id": "SKU004"},
                                                {"product_retailer_id": "SKU005"},
                                            ],
                                        },
                                    ],
                                },
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


def validate_mpm_template_send(data: dict) -> MPMTemplateSendRequestValidator:
    """
    Validate a Multi-Product Message template send request dictionary.

    Args:
        data: Dictionary containing request data

    Returns:
        MPMTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
    """
    return MPMTemplateSendRequestValidator(**data)


def parse_and_validate_mpm_template_send(
    json_str: str,
) -> MPMTemplateSendRequestValidator:
    """
    Parse JSON string and validate as MPM template send request.

    Args:
        json_str: JSON string containing request data

    Returns:
        MPMTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_mpm_template_send(data)
