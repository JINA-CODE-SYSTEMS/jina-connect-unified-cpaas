"""
WATI Authentication Template Validator

Validates authentication (OTP) templates before submission to the WATI API.

Authentication templates are used for one-time passwords, login verification
codes, account verification, and two-factor authentication.

┌──────────────────────────────────────────────────────────────────────────────┐
│                   AUTHENTICATION TEMPLATE GUARDRAILS                        │
├───────────────┬──────────────────────────────────────────────────────────────┤
│ Sub-Category  │ Rules                                                       │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ STANDARD      │ Header : TEXT or NONE only                                  │
│               │   ⚠ No media headers (IMAGE, VIDEO, DOCUMENT)             │
│               │ Body   : Required, MUST contain ≥1 variable ({{1}} / {{code}})│
│               │   • The variable is the OTP / verification code             │
│               │   • Max 1024 chars                                          │
│               │ Footer : ❌ Not allowed (META enforces fixed footer)        │
│               │ Buttons: Max 1 button only                                  │
│               │   • copy_code     : ✅ Recommended (auto-fill OTP)         │
│               │   • url           : ✅ Allowed (one-tap verification link)  │
│               │   ⚠ No quick_reply, phone_number, catalog, flow           │
│               │   ⚠ copy_code requires example value (e.g., "123456")     │
│               │ TTL    : Default 600s (10 min), minimum 60s, warn >3600s   │
│               │ Extra  : No promotional content allowed                     │
│               │   • customParams must include the OTP variable              │
└───────────────┴──────────────────────────────────────────────────────────────┘

Reference: https://docs.wati.io/reference/post_api-v1-whatsapp-templates
"""

import re
from typing import Any, Dict, Literal, Optional

from pydantic import Field, model_validator

from wa.utility.data_model.wati.template_input import WATIHeaderFormat, WATITemplateSubCategory

from .base_validator import BaseTemplateValidator

# =============================================================================
# Guardrails constant – machine-readable rules for authentication templates
# =============================================================================

AUTHENTICATION_GUARDRAILS: Dict[str, Any] = {
    "allowed_headers": ["TEXT", "NONE"],
    "body_required": True,
    "body_max_length": 1024,
    "body_must_contain_variable": True,
    "footer_allowed": False,
    "buttons": {
        "max_total": 1,
        "allowed_types": ["copy_code", "url"],
        "limits": {
            "copy_code": 1,
            "url": 1,
        },
    },
    "ttl": {
        "default_seconds": 600,  # 10 minutes
        "minimum_seconds": 60,  # 1 minute
        "warning_threshold": 3600,  # warn if > 1 hour
    },
}


class AuthenticationTemplateValidator(BaseTemplateValidator):
    """
    Validator for WATI authentication (OTP) templates.

    Extends BaseTemplateValidator with authentication-specific:
    - Category enforcement (AUTHENTICATION only)
    - OTP variable requirement in body
    - No media headers (TEXT or NONE only)
    - No footer allowed
    - Max 1 button (copy_code or url)
    - Strict TTL validation (default 10 min, min 60s)

    Use ``AUTHENTICATION_GUARDRAILS`` dict for programmatic access to the rules.

    Usage:
        validator = AuthenticationTemplateValidator(
            elementName="login_otp",
            language="en",
            body="Your verification code is {{1}}. Do not share this with anyone.",
            buttonsType="call_to_action",
            buttons=[
                {"type": "copy_code", "text": "Copy Code", "example": "123456"},
            ],
            customParams=[
                {"name": "1", "value": "123456"},
            ],
            message_send_ttl_seconds=600,
        )
        payload = validator.to_wati_payload()
    """

    category: Literal["AUTHENTICATION", "authentication"] = "AUTHENTICATION"
    subCategory: Optional[WATITemplateSubCategory] = Field(
        default=WATITemplateSubCategory.STANDARD,
        description="Authentication template sub-category (typically STANDARD)",
    )
    message_send_ttl_seconds: Optional[int] = Field(
        default=600,
        description="Message send TTL in seconds (default 10 minutes for OTP)",
    )

    # =========================================================================
    # Authentication-Specific Validators
    # =========================================================================

    @model_validator(mode="after")
    def validate_no_media_header(self):
        """
        Authentication templates must not have media headers.

        Guardrail: Only TEXT or NONE header allowed.
        """
        allowed = AUTHENTICATION_GUARDRAILS["allowed_headers"]
        if self.header:
            header_fmt = (
                self.header.format.value
                if isinstance(self.header.format, WATIHeaderFormat)
                else str(self.header.format)
            )
            if header_fmt not in allowed:
                raise ValueError(
                    f"Authentication templates cannot have '{header_fmt}' headers. Only {allowed} are allowed."
                )
        return self

    @model_validator(mode="after")
    def validate_no_footer(self):
        """
        Authentication templates do not allow footers.

        META enforces a fixed footer for authentication templates;
        user-provided footers are rejected.
        """
        if self.footer:
            raise ValueError(
                "Authentication templates do not allow custom footers. META enforces a fixed footer automatically."
            )
        return self

    @model_validator(mode="after")
    def validate_otp_variable(self):
        """
        Authentication templates MUST contain at least one variable
        for the OTP / verification code.

        Guardrail: body_must_contain_variable = True
        """
        has_named = bool(re.findall(r"\{\{[a-zA-Z_]\w*\}\}", self.body))
        has_positional = bool(re.findall(r"\{\{\d+\}\}", self.body))

        if not has_named and not has_positional:
            raise ValueError(
                "Authentication templates must contain at least one variable "
                "(e.g., {{1}} or {{code}}) for the verification code."
            )
        return self

    @model_validator(mode="after")
    def validate_authentication_buttons(self):
        """
        Validate authentication template button constraints.

        Guardrails:
        - Max 1 button total
        - Allowed types: copy_code, url
        - No quick_reply, phone_number, catalog, flow
        """
        btn_rules = AUTHENTICATION_GUARDRAILS["buttons"]

        if not self.buttons:
            return self

        if len(self.buttons) > btn_rules["max_total"]:
            raise ValueError(
                f"Authentication templates allow max {btn_rules['max_total']} button, got {len(self.buttons)}."
            )

        for btn in self.buttons:
            btn_type = btn.type if hasattr(btn, "type") else btn.get("type", "")
            if btn_type not in btn_rules["allowed_types"]:
                raise ValueError(
                    f"Authentication templates only support {btn_rules['allowed_types']} buttons, got '{btn_type}'."
                )

        return self

    @model_validator(mode="after")
    def validate_ttl(self):
        """
        Validate TTL for authentication templates.

        Guardrails:
        - Minimum 60 seconds
        - Default 600 seconds (10 minutes)
        - Warning threshold at 3600 seconds (1 hour)
        """
        ttl_rules = AUTHENTICATION_GUARDRAILS["ttl"]

        if self.message_send_ttl_seconds is not None:
            if self.message_send_ttl_seconds < ttl_rules["minimum_seconds"]:
                raise ValueError(
                    f"Authentication template TTL must be at least "
                    f"{ttl_rules['minimum_seconds']} seconds, "
                    f"got {self.message_send_ttl_seconds}."
                )
            if self.message_send_ttl_seconds > ttl_rules["warning_threshold"]:
                # Long TTL for OTP is unusual but not a hard error
                pass
        return self
