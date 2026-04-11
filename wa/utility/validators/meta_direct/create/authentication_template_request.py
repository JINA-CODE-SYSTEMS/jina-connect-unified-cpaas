"""
Authentication Template Request Validator for META Direct API

This module provides Pydantic validators for META's WhatsApp Business API
authentication template creation requests.

Authentication templates have a UNIQUE component structure compared to
marketing/utility templates:
- body: Optional `add_security_recommendation` boolean (NOT a text body —
        META auto-generates the body text)
- footer: Optional `code_expiration_minutes` integer (NOT a text footer —
        META auto-generates the footer)
- buttons: `type: "otp"` with `otp_type`: `copy_code`, `one_tap`, or `zero_tap`

Based on META's official API curl examples:

  Copy Code button:
    {"type": "otp", "otp_type": "copy_code", "text": "<COPY_CODE_BUTTON_TEXT>"}

  One Tap button:
    {"type": "otp", "otp_type": "one_tap", "text": "<COPY_CODE_BUTTON_TEXT>",
     "autofill_text": "<AUTOFILL_BUTTON_TEXT>",
     "supported_apps": [{"package_name": "...", "signature_hash": "..."}]}

  Zero Tap button:
    {"type": "otp", "otp_type": "zero_tap", "text": "<COPY_CODE_BUTTON_TEXT>",
     "autofill_text": "<AUTOFILL_BUTTON_TEXT>",
     "zero_tap_terms_accepted": true,
     "supported_apps": [{"package_name": "...", "signature_hash": "..."}]}
"""

import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

# =============================================================================
# Guardrails constant — machine-readable rules for authentication templates
# =============================================================================

AUTHENTICATION_GUARDRAILS: Dict[str, Any] = {
    "allowed_headers": ["TEXT", "NONE"],
    "body_required": False,  # META auto-generates the body; add_security_recommendation is optional
    "body_add_security_recommendation": True,  # Optional boolean
    "footer_allowed": True,  # Only code_expiration_minutes, not custom text
    "footer_code_expiration_minutes": {
        "min": 1,
        "max": 90,
    },
    "buttons": {
        "max_total": 1,
        "allowed_otp_types": ["copy_code", "one_tap", "zero_tap"],
    },
    "ttl": {
        "default_seconds": 600,
        "minimum_seconds": 60,
        "warning_threshold": 3600,
    },
}


# =============================================================================
# Authentication-Specific Component Models
#
# These differ from marketing/utility components because META auto-generates
# the body text and footer for authentication templates.
# =============================================================================


class AuthBodyComponent(BaseModel):
    """
    Authentication body component.

    Unlike marketing/utility body components, authentication body does NOT
    contain user-provided text. META auto-generates the body message.
    The only option is `add_security_recommendation` which appends a
    "Do not share this code" type message.
    """

    type: Literal["body"] = "body"
    add_security_recommendation: Optional[bool] = Field(
        default=None,
        description=(
            "If true, META appends a security recommendation to the "
            "auto-generated body (e.g., 'Do not share this code with anyone')."
        ),
    )


class AuthFooterComponent(BaseModel):
    """
    Authentication footer component.

    Unlike marketing/utility footer components, authentication footer does NOT
    contain user-provided text. The only option is `code_expiration_minutes`
    which tells the user how long the code is valid.
    """

    type: Literal["footer"] = "footer"
    code_expiration_minutes: Optional[int] = Field(
        default=None,
        ge=1,
        le=90,
        description="Code expiration time in minutes (1–90). META displays this in the footer.",
    )


class SupportedApp(BaseModel):
    """
    Supported app for one_tap and zero_tap OTP buttons.

    Identifies the Android app that can auto-fill or zero-tap the OTP.
    """

    package_name: str = Field(
        ...,
        min_length=1,
        description="Android package name (e.g., 'com.example.app')",
    )
    signature_hash: str = Field(
        ...,
        min_length=1,
        description="Android app signature hash for verification",
    )


class OTPCopyCodeButton(BaseModel):
    """
    Copy Code OTP button — user taps to copy the OTP to clipboard.

    This is the simplest OTP button type. Recommended for most use cases.
    """

    type: Literal["otp"] = "otp"
    otp_type: Literal["copy_code"] = "copy_code"
    text: Optional[str] = Field(
        default=None,
        max_length=25,
        description="Custom button text (optional, META provides default)",
    )


class OTPOneTapButton(BaseModel):
    """
    One Tap OTP button — auto-fills the OTP in the companion Android app.

    Requires `supported_apps` to identify the Android app and its signature.
    Falls back to copy_code if the user doesn't have the app or is on iOS.
    """

    type: Literal["otp"] = "otp"
    otp_type: Literal["one_tap"] = "one_tap"
    text: Optional[str] = Field(
        default=None,
        max_length=25,
        description="Fallback copy-code button text (optional)",
    )
    autofill_text: Optional[str] = Field(
        default=None,
        max_length=25,
        description="Text shown on the autofill button (optional)",
    )
    supported_apps: List[SupportedApp] = Field(
        ...,
        min_length=1,
        description="List of Android apps that support one-tap autofill",
    )


class OTPZeroTapButton(BaseModel):
    """
    Zero Tap OTP button — automatically fills the OTP without user interaction.

    Requires `supported_apps` and explicit `zero_tap_terms_accepted`.
    Falls back to one_tap → copy_code if conditions aren't met.
    """

    type: Literal["otp"] = "otp"
    otp_type: Literal["zero_tap"] = "zero_tap"
    text: Optional[str] = Field(
        default=None,
        max_length=25,
        description="Fallback copy-code button text (optional)",
    )
    autofill_text: Optional[str] = Field(
        default=None,
        max_length=25,
        description="Fallback autofill button text (optional)",
    )
    zero_tap_terms_accepted: bool = Field(
        ...,
        description="Must be true — confirms acceptance of zero-tap terms",
    )
    supported_apps: List[SupportedApp] = Field(
        ...,
        min_length=1,
        description="List of Android apps that support zero-tap autofill",
    )


# Union type for all OTP button types
OTPButton = Union[OTPCopyCodeButton, OTPOneTapButton, OTPZeroTapButton]


class AuthButtonsComponent(BaseModel):
    """
    Authentication buttons component.

    Unlike marketing/utility buttons, authentication templates only support
    OTP buttons with type "otp". Maximum 1 button allowed.
    """

    type: Literal["buttons"] = "buttons"
    buttons: List[OTPButton] = Field(
        ...,
        min_length=1,
        max_length=1,
        description="Exactly 1 OTP button",
    )

    @field_validator("buttons", mode="before")
    @classmethod
    def parse_buttons(cls, v):
        """Parse button dictionaries into appropriate OTP button types"""
        if not v or not isinstance(v, list):
            raise ValueError("At least one OTP button is required")

        parsed = []
        for btn in v:
            if isinstance(btn, BaseModel):
                parsed.append(btn)
                continue

            if not isinstance(btn, dict):
                raise ValueError(f"Button must be a dictionary, got {type(btn)}")

            btn_type = btn.get("type", "")
            if btn_type != "otp":
                raise ValueError(f"Authentication templates only support 'otp' buttons, got '{btn_type}'")

            otp_type = btn.get("otp_type", "")
            try:
                if otp_type == "copy_code":
                    parsed.append(OTPCopyCodeButton(**btn))
                elif otp_type == "one_tap":
                    parsed.append(OTPOneTapButton(**btn))
                elif otp_type == "zero_tap":
                    parsed.append(OTPZeroTapButton(**btn))
                else:
                    raise ValueError(f"Invalid otp_type '{otp_type}'. Must be one of: copy_code, one_tap, zero_tap")
            except Exception as e:
                raise ValueError(f"Error parsing OTP button: {e}")

        return parsed


# Union type for all authentication template components
AuthenticationTemplateComponent = Union[AuthBodyComponent, AuthFooterComponent, AuthButtonsComponent]


# =============================================================================
# Main Validator
# =============================================================================


class AuthenticationTemplateRequestValidator(BaseModel):
    """
    Validator for META Direct API authentication template creation request.

    Authentication templates have a UNIQUE structure compared to marketing/utility:
    - Body is auto-generated by META (only `add_security_recommendation` is user-controlled)
    - Footer is auto-generated by META (only `code_expiration_minutes` is user-controlled)
    - Only OTP buttons are allowed (copy_code, one_tap, zero_tap)
    - No header component (or TEXT-only if needed)
    - message_send_ttl_seconds controls how long META attempts delivery

    Example usage (copy_code):
        >>> data = {
        ...     "name": "login_otp",
        ...     "language": "en",
        ...     "category": "authentication",
        ...     "message_send_ttl_seconds": 600,
        ...     "components": [
        ...         {"type": "body", "add_security_recommendation": True},
        ...         {"type": "footer", "code_expiration_minutes": 10},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "otp", "otp_type": "copy_code", "text": "Copy Code"}
        ...         ]}
        ...     ]
        ... }
        >>> template = AuthenticationTemplateRequestValidator(**data)

    Example usage (one_tap):
        >>> data = {
        ...     "name": "app_otp",
        ...     "language": "en",
        ...     "category": "authentication",
        ...     "components": [
        ...         {"type": "body", "add_security_recommendation": True},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "otp", "otp_type": "one_tap",
        ...              "text": "Copy Code", "autofill_text": "Autofill",
        ...              "supported_apps": [
        ...                  {"package_name": "com.example.app",
        ...                   "signature_hash": "ABC123XYZ"}
        ...              ]}
        ...         ]}
        ...     ]
        ... }
        >>> template = AuthenticationTemplateRequestValidator(**data)

    Example usage (zero_tap):
        >>> data = {
        ...     "name": "auto_otp",
        ...     "language": "en",
        ...     "category": "authentication",
        ...     "components": [
        ...         {"type": "body"},
        ...         {"type": "buttons", "buttons": [
        ...             {"type": "otp", "otp_type": "zero_tap",
        ...              "text": "Copy Code", "autofill_text": "Autofill",
        ...              "zero_tap_terms_accepted": True,
        ...              "supported_apps": [
        ...                  {"package_name": "com.example.app",
        ...                   "signature_hash": "ABC123XYZ"}
        ...              ]}
        ...         ]}
        ...     ]
        ... }
        >>> template = AuthenticationTemplateRequestValidator(**data)
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Template name (alphanumeric and underscores only)",
    )
    language: str = Field(
        ...,
        min_length=2,
        max_length=10,
        description="Template language code (e.g., 'en', 'en_US')",
    )
    category: Literal["authentication", "AUTHENTICATION"] = Field(
        ..., description="Template category (must be 'authentication')"
    )
    message_send_ttl_seconds: Optional[int] = Field(
        default=600,
        ge=60,
        description="Message send TTL in seconds (default 600s / 10 min, minimum 60s)",
    )
    components: List[AuthenticationTemplateComponent] = Field(
        ...,
        min_length=1,
        description="Template components (body, footer, buttons)",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        """Validate template name — lowercase alphanumeric + underscores"""
        if not v or not v.strip():
            raise ValueError("Template name cannot be empty")
        v = v.strip().lower()
        if not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError("Template name must contain only lowercase letters, numbers, and underscores")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v):
        """Validate language code format"""
        if not v or not v.strip():
            raise ValueError("Language code cannot be empty")
        if not re.match(r"^[a-z]{2}(_[A-Z]{2})?$", v):
            raise ValueError("Language code must be in format 'xx' or 'xx_XX' (e.g., 'en', 'en_US')")
        return v

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
            if not comp_type:
                raise ValueError("Component must have a 'type' field")

            try:
                if comp_type == "body":
                    parsed.append(AuthBodyComponent(**comp))
                elif comp_type == "footer":
                    parsed.append(AuthFooterComponent(**comp))
                elif comp_type == "buttons":
                    parsed.append(AuthButtonsComponent(**comp))
                elif comp_type == "header":
                    raise ValueError(
                        "Authentication templates do not support custom header components. "
                        "Only body, footer, and buttons are allowed."
                    )
                else:
                    raise ValueError(f"Unknown component type: '{comp_type}'")
            except Exception as e:
                raise ValueError(f"Error parsing {comp_type} component: {e}")

        return parsed

    @model_validator(mode="after")
    def validate_template_structure(self):
        """Validate the overall authentication template structure"""
        component_types = [comp.type for comp in self.components]

        # Check for duplicate components
        if component_types.count("body") > 1:
            raise ValueError("Only one body component is allowed")
        if component_types.count("footer") > 1:
            raise ValueError("Only one footer component is allowed")
        if component_types.count("buttons") > 1:
            raise ValueError("Only one buttons component is allowed")

        # Buttons component must be present (OTP is the point of auth templates)
        if "buttons" not in component_types:
            raise ValueError(
                "Authentication templates must include a buttons component "
                "with an OTP button (copy_code, one_tap, or zero_tap)"
            )

        # Validate component order: body -> footer -> buttons
        expected_order = ["body", "footer", "buttons"]
        actual_order = [t for t in component_types if t in expected_order]
        correct_order = [t for t in expected_order if t in component_types]

        if actual_order != correct_order:
            raise ValueError(f"Components must be in order: body → footer → buttons. Got: {actual_order}")

        # Validate TTL
        ttl_rules = AUTHENTICATION_GUARDRAILS["ttl"]
        if self.message_send_ttl_seconds is not None:
            if self.message_send_ttl_seconds < ttl_rules["minimum_seconds"]:
                raise ValueError(
                    f"Authentication template TTL must be at least "
                    f"{ttl_rules['minimum_seconds']} seconds, "
                    f"got {self.message_send_ttl_seconds}."
                )

        return self

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)

    def to_meta_payload(self) -> dict:
        """
        Convert to the META Direct API request payload.

        Returns the dict structure matching META's curl format, ready to be
        JSON-serialized and sent to the Graph API.
        """
        payload = {
            "name": self.name,
            "language": self.language,
            "category": self.category.lower(),
            "components": [comp.model_dump(exclude_none=True) for comp in self.components],
        }
        if self.message_send_ttl_seconds is not None:
            payload["message_send_ttl_seconds"] = self.message_send_ttl_seconds
        return payload


# =============================================================================
# Convenience functions
# =============================================================================


def validate_authentication_template(data: dict) -> AuthenticationTemplateRequestValidator:
    """
    Validate an authentication template creation request dictionary.

    Args:
        data: Dictionary with template creation data

    Returns:
        AuthenticationTemplateRequestValidator: Validated request object

    Raises:
        ValueError: If validation fails
    """
    return AuthenticationTemplateRequestValidator(**data)


def validate_authentication_template_json(
    json_str: str,
) -> AuthenticationTemplateRequestValidator:
    """
    Parse JSON string and validate as authentication template creation request.

    Args:
        json_str: JSON string with template creation data

    Returns:
        AuthenticationTemplateRequestValidator: Validated request object

    Raises:
        ValueError: If validation fails
    """
    import json

    data = json.loads(json_str)
    return validate_authentication_template(data)
