from enum import Enum
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


class ButtonType(str, Enum):
    """Enum for different button types"""
    PHONE_NUMBER = "PHONE_NUMBER"
    URL = "URL"
    OTP = "OTP"
    QUICK_REPLY = "QUICK_REPLY"
    COPY_CODE = "COPY_CODE"


class OTPType(str, Enum):
    """Enum for OTP button types"""
    COPY_CODE = "COPY_CODE"
    ONE_TAP = "ONE_TAP"


class TemplateButtonInput(BaseModel):
    """
    Template button input class that handles different button types:
    - PHONE_NUMBER: Call buttons
    - URL: Website/link buttons with dynamic parameters
    - OTP: Authentication buttons (COPY_CODE or ONE_TAP)
    - QUICK_REPLY: Quick reply buttons (for MARKETING/UTILITY templates)
    """
    type: ButtonType = Field(..., description="Type of button")
    text: str = Field(..., description="Button text displayed to user")
    
    # Phone number button fields
    phone_number: Optional[str] = Field(None, description="Phone number for PHONE_NUMBER type")
    
    # URL button fields
    url: Optional[str] = Field(None, description="URL for URL type buttons, can contain {{1}}, {{2}} etc for dynamic content")
    example: Optional[List[str]] = Field(None, description="Example URLs showing how dynamic content would be replaced")
    
    # OTP button fields - handle both otp_type and otp-type
    otp_type: Optional[OTPType] = Field(None, description="OTP type for OTP buttons")
    autofill_text: Optional[str] = Field(None, description="Autofill text for ONE_TAP OTP buttons")
    package_name: Optional[str] = Field(None, description="Android package name for ONE_TAP OTP buttons")
    signature_hash: Optional[str] = Field(None, description="Signature hash for ONE_TAP OTP buttons")
    
    # COPY_CODE button fields (MARKETING coupon-code templates)
    coupon_code: Optional[str] = Field(None, description="Coupon code for COPY_CODE buttons")
    
    def __init__(self, **data):
        # Handle the otp-type alias before validation
        if 'otp-type' in data and 'otp_type' not in data:
            data['otp_type'] = data.pop('otp-type')
        super().__init__(**data)
    
    @field_validator('phone_number')
    @classmethod
    def validate_phone_number(cls, v, info):
        if info.data.get('type') == ButtonType.PHONE_NUMBER and not v:
            raise ValueError('phone_number is required for PHONE_NUMBER type buttons')
        return v
    
    @field_validator('url')
    @classmethod
    def validate_url(cls, v, info):
        if info.data.get('type') == ButtonType.URL and not v:
            raise ValueError('url is required for URL type buttons')
        return v
    
    @field_validator('otp_type')
    @classmethod
    def validate_otp_type(cls, v, info):
        if info.data.get('type') == ButtonType.OTP and not v:
            raise ValueError('otp_type is required for OTP type buttons')
        return v
    
    @field_validator('coupon_code')
    @classmethod
    def validate_coupon_code(cls, v, info):
        if info.data.get('type') == ButtonType.COPY_CODE and not v:
            raise ValueError('coupon_code is required for COPY_CODE type buttons')
        return v
    
    @field_validator('package_name')
    @classmethod
    def validate_package_name(cls, v, info):
        if info.data.get('otp_type') == OTPType.ONE_TAP and not v:
            raise ValueError('package_name is required for ONE_TAP OTP buttons')
        return v
    
    @field_validator('signature_hash')
    @classmethod
    def validate_signature_hash(cls, v, info):
        if info.data.get('otp_type') == OTPType.ONE_TAP and not v:
            raise ValueError('signature_hash is required for ONE_TAP OTP buttons')
        return v
    
    model_config = {
        "populate_by_name": True,  # Updated for Pydantic v2
        "json_schema_extra": {     # Updated for Pydantic v2
            "examples": [
                {
                    "type": "PHONE_NUMBER",
                    "text": "Call Us",
                    "phone_number": "+919876543210"
                },
                {
                    "type": "URL",
                    "text": "Book A Demo",
                    "url": "https://bookins.gupshup.io/{{1}}",
                    "example": ["https://bookins.gupshup.io/abc"]
                },
                {
                    "type": "OTP",
                    "otp_type": "COPY_CODE",
                    "text": "Copy OTP"
                },
                {
                    "type": "OTP",
                    "otp_type": "ONE_TAP",
                    "text": "Book A Demo",
                    "autofill_text": "Autofill",
                    "package_name": "com.example.myapplication",
                    "signature_hash": "K8a%2FAINcGX7"
                },
                {
                    "type": "QUICK_REPLY",
                    "text": "Yes, I'm interested"
                },
                {
                    "type": "COPY_CODE",
                    "text": "Copy Coupon",
                    "coupon_code": "SAVE25"
                }
            ]
        }
    }


class TemplateButtonsInput(BaseModel):
    """Container for multiple template buttons"""
    buttons: List[TemplateButtonInput] = Field(..., description="List of template buttons")
    
    @field_validator('buttons')
    @classmethod
    def validate_buttons_count(cls, v):
        if len(v) == 0:
            raise ValueError('At least one button is required')
        if len(v) > 3:  # WhatsApp template limit
            raise ValueError('Maximum 3 buttons allowed per template')
        return v


# Utility functions for parsing button dictionaries
def parse_template_buttons(buttons_data: List[dict]) -> TemplateButtonsInput:
    """
    Parse a list of button dictionaries into TemplateButtonsInput
    
    Args:
        buttons_data: List of dictionaries containing button data
        
    Returns:
        TemplateButtonsInput: Parsed and validated buttons
    
    Example:
        >>> buttons = [
        ...     {"type": "PHONE_NUMBER", "text": "Call Us", "phone_number": "+919876543210"},
        ...     {"type": "URL", "text": "Book Demo", "url": "https://demo.com/{{1}}", "example": ["https://demo.com/abc"]}
        ... ]
        >>> parsed = parse_template_buttons(buttons)
    """
    return TemplateButtonsInput(buttons=[TemplateButtonInput(**btn) for btn in buttons_data])


def parse_single_template_button(button_data: dict) -> TemplateButtonInput:
    """
    Parse a single button dictionary into TemplateButtonInput
    
    Args:
        button_data: Dictionary containing button data
        
    Returns:
        TemplateButtonInput: Parsed and validated button
        
    Example:
        >>> button = {"type": "OTP", "otp-type": "COPY_CODE", "text": "Copy OTP"}
        >>> parsed = parse_single_template_button(button)
    """
    return TemplateButtonInput(**button_data)
