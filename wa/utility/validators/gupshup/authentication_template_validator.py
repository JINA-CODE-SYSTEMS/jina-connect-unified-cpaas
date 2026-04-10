
from typing import List, Literal, Optional

from pydantic import field_validator
from wa.utility.data_model.gupshup.template_button_input import (
    TemplateButtonsInput, parse_template_buttons)

from .base_validatory import BaseTemplateValidator


class AuthTemplateValidator(BaseTemplateValidator):
    """
    Gupshup AUTHENTICATION template validator.

    Enforces that AUTHENTICATION templates have mandatory OTP buttons
    (COPY_CODE or ONE_TAP) and validates OTP-specific fields before
    submission to Gupshup.
    """
    category: Literal['AUTHENTICATION']
    message_send_ttl_seconds: Optional[int] = 300 # 5 minutes
    buttons: TemplateButtonsInput  # Make buttons mandatory (not Optional)

    @field_validator('buttons', mode='before')
    @classmethod
    def validate_buttons(cls, v):
        """
        Convert raw JSON buttons data to TemplateButtonsInput object.
        Handles both list of dicts (from database/API) and already parsed TemplateButtonsInput objects.
        For AUTHENTICATION category, buttons are MANDATORY and must be OTP type only.
        """
        if v is None or v == []:
            raise ValueError("OTP buttons are mandatory for AUTHENTICATION category templates. At least one OTP button is required.")
        


        # If it's already a TemplateButtonsInput object, validate its buttons
        if isinstance(v, TemplateButtonsInput):
            cls._validate_otp_buttons_only(v.buttons)
            return v
        
        # If it's a list of dictionaries (raw JSON from database), parse it
        if isinstance(v, list):
            try:
                parsed_buttons = parse_template_buttons(v)
                cls._validate_otp_buttons_only(parsed_buttons.buttons)
                return parsed_buttons
            except Exception as e:
                raise ValueError(f"Invalid buttons data: {e}")
        
        # If it's any other type, it's invalid
        raise ValueError(f"Buttons must be a list of button objects or TemplateButtonsInput, got {type(v)}")
    
    @classmethod
    def _validate_otp_buttons_only(cls, buttons):
        """
        Validate that all buttons are of OTP type for AUTHENTICATION category.
        """
        from wa.utility.data_model.gupshup.template_button_input import \
            ButtonType
        
        if not buttons:
            return  # No buttons to validate
        
        for i, button in enumerate(buttons):
            if button.type != ButtonType.OTP:
                raise ValueError(
                    f"Button {i+1}: AUTHENTICATION category templates can only have OTP type buttons, "
                    f"found '{button.type}' button. Only OTP buttons (COPY_CODE or ONE_TAP) are allowed."
                )
            
            # Additional validation for OTP buttons
            if not button.otp_type:
                raise ValueError(
                    f"Button {i+1}: OTP button must specify otp_type (COPY_CODE or ONE_TAP)"
                )
            
            # Validate ONE_TAP specific requirements
            if button.otp_type == "ONE_TAP":
                if not button.package_name:
                    raise ValueError(
                        f"Button {i+1}: ONE_TAP OTP button requires package_name"
                    )
                if not button.signature_hash:
                    raise ValueError(
                        f"Button {i+1}: ONE_TAP OTP button requires signature_hash"
                    )




    
    

