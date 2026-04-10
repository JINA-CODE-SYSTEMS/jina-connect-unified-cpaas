"""
Single Product Message (SPM) Template Send Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
Single Product Message template sending requests.

SPM templates display a single product from a connected catalog.
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
# Product Action Model for Send
# ============================================================================


class ProductActionSend(BaseModel):
    """Action configuration for SPM send request"""
    catalog_id: str = Field(
        ...,
        description="The catalog ID containing the product",
    )
    product_retailer_id: str = Field(
        ...,
        min_length=1,
        description="Product retailer ID from the catalog to display",
    )


# ============================================================================
# Component Models
# ============================================================================


class BodyComponentSend(BaseModel):
    """Body component for sending SPM template message"""
    type: Literal["body"] = "body"
    parameters: List[BodyParameter] = Field(
        ..., min_length=1, description="Body parameters"
    )


class SPMComponentSend(BaseModel):
    """Single Product Message component for send request"""
    type: Literal["product"] = "product"
    action: ProductActionSend = Field(
        ...,
        description="Product action with catalog ID and product retailer ID",
    )


# Union type for send components
SendSPMTemplateComponent = Union[BodyComponentSend, SPMComponentSend]


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


class SPMTemplateSendBody(BaseModel):
    """Template body for SPM template send request"""
    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name",
    )
    language: LanguageInput = Field(..., description="Template language")
    components: Optional[List[SendSPMTemplateComponent]] = Field(
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
                elif comp_type == "product":
                    # Parse product action
                    if "action" in comp and isinstance(comp["action"], dict):
                        comp["action"] = ProductActionSend(**comp["action"])
                    parsed.append(SPMComponentSend(**comp))
                else:
                    raise ValueError(f"Unknown component type: {comp_type}")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed


# ============================================================================
# Main Request Validator
# ============================================================================


class SPMTemplateSendRequestValidator(BaseModel):
    """
    Validator for META Direct API Single Product Message template send request.

    Validates the complete message structure including:
    - messaging_product: "whatsapp"
    - recipient_type: "individual"
    - to: Recipient phone number
    - type: "template"
    - template: Template details with name, language, and components
      including product with catalog_id and product_retailer_id

    Example usage:
        >>> data = {
        ...     "messaging_product": "whatsapp",
        ...     "recipient_type": "individual",
        ...     "to": "919876543210",
        ...     "type": "template",
        ...     "template": {
        ...         "name": "featured_product",
        ...         "language": {"code": "en"},
        ...         "components": [
        ...             {"type": "body", "parameters": [
        ...                 {"type": "text", "text": "John"}
        ...             ]},
        ...             {"type": "product", "action": {
        ...                 "catalog_id": "123456789",
        ...                 "product_retailer_id": "SKU001"
        ...             }}
        ...         ]
        ...     }
        ... }
        >>> request = SPMTemplateSendRequestValidator(**data)
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
    template: SPMTemplateSendBody = Field(..., description="Template details")

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
                        "name": "featured_product_promo",
                        "language": {"code": "en"},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [{"type": "text", "text": "John"}],
                            },
                            {
                                "type": "product",
                                "action": {
                                    "catalog_id": "123456789012",
                                    "product_retailer_id": "SKU001",
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


def validate_spm_template_send(data: dict) -> SPMTemplateSendRequestValidator:
    """
    Validate a Single Product Message template send request dictionary.

    Args:
        data: Dictionary containing request data

    Returns:
        SPMTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
    """
    return SPMTemplateSendRequestValidator(**data)


def parse_and_validate_spm_template_send(
    json_str: str,
) -> SPMTemplateSendRequestValidator:
    """
    Parse JSON string and validate as SPM template send request.

    Args:
        json_str: JSON string containing request data

    Returns:
        SPMTemplateSendRequestValidator: Validated request object

    Raises:
        ValidationError: If request data is invalid
        JSONDecodeError: If JSON is malformed
    """
    import json

    data = json.loads(json_str)
    return validate_spm_template_send(data)
