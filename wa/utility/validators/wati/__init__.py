"""
WATI Template Validators

This package provides Pydantic validators for WATI's WhatsApp Business API
template creation and sending operations.

Each category validator exposes a ``*_GUARDRAILS`` dict that documents
the machine-readable rules (allowed headers, button types/limits,
footer rules, TTL defaults, etc.) for every sub-category.

Modules:
- base_validator           : Base template validator with common fields
- marketing_validator      : Marketing template validator  → MARKETING_GUARDRAILS
- utility_validator        : Utility template validator    → UTILITY_GUARDRAILS
- authentication_validator : Authentication (OTP) validator→ AUTHENTICATION_GUARDRAILS

Usage:
    from wa.utility.validators.wati import (
        MarketingTemplateValidator, MARKETING_GUARDRAILS,
        UtilityTemplateValidator, UTILITY_GUARDRAILS,
        AuthenticationTemplateValidator, AUTHENTICATION_GUARDRAILS,
    )

    # Programmatic access to guardrails
    carousel_rules = MARKETING_GUARDRAILS["CAROUSEL"]
    print(carousel_rules["cards"]["max"])  # 10

    validator = MarketingTemplateValidator(
        elementName="promo_offer",
        language="en",
        body="Hi {{1}}, check out our {{2}} sale!",
        buttonsType="call_to_action",
        buttons=[{"type": "url", "text": "Shop Now", "url": "https://example.com"}],
    )
    payload = validator.to_wati_payload()
"""

from wa.utility.validators.wati.authentication_validator import (
    AUTHENTICATION_GUARDRAILS, AuthenticationTemplateValidator)
from wa.utility.validators.wati.base_validator import BaseTemplateValidator
from wa.utility.validators.wati.marketing_validator import (
    MARKETING_GUARDRAILS, MarketingTemplateValidator)
from wa.utility.validators.wati.utility_validator import (
    UTILITY_GUARDRAILS, UtilityTemplateValidator)

__all__ = [
    "BaseTemplateValidator",
    "MarketingTemplateValidator",
    "MARKETING_GUARDRAILS",
    "UtilityTemplateValidator",
    "UTILITY_GUARDRAILS",
    "AuthenticationTemplateValidator",
    "AUTHENTICATION_GUARDRAILS",
]
