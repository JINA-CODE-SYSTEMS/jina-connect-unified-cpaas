"""
WATI Marketing Template Validator

Validates marketing templates before submission to the WATI API.

Marketing templates are used for promotional content, offers, and
general business communication. They require META approval.

┌──────────────────────────────────────────────────────────────────────────────┐
│                    MARKETING TEMPLATE GUARDRAILS                            │
├───────────────┬──────────────────────────────────────────────────────────────┤
│ Sub-Category  │ Rules                                                       │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ STANDARD      │ Header : TEXT, IMAGE, VIDEO, DOCUMENT                       │
│               │ Body   : Required, max 1024 chars, supports {{variables}}   │
│               │ Footer : Optional, max 60 chars, NO variables               │
│               │ Buttons: Max 10 total                                       │
│               │   • quick_reply   : max 10                                  │
│               │   • url           : max 2                                   │
│               │   • phone_number  : max 1                                   │
│               │   ⚠ Cannot mix QR + CTA unless buttonsType =               │
│               │     "quick_reply_and_call_to_action"                        │
│               │ TTL    : Default 43200s (12h)                               │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ CAROUSEL      │ Header : ❌ Not at template level                           │
│               │ Body   : Required at template level                         │
│               │ Footer : ❌ Not allowed                                     │
│               │ Cards  : 2–10 cards required                                │
│               │   Per card:                                                 │
│               │   • header : IMAGE or VIDEO only (required)                 │
│               │   • body   : max 160 chars (required)                       │
│               │   • buttons: 1–2 per card (quick_reply, url, phone_number)  │
│               │   • All cards must have SAME number of buttons              │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ CATALOG       │ Header : TEXT, IMAGE, VIDEO, DOCUMENT (optional)            │
│               │ Body   : Required                                           │
│               │ Footer : Optional, max 60 chars                             │
│               │ Buttons: Exactly 1 CATALOG button (required)                │
│               │   • Additional: url, phone_number, quick_reply allowed      │
│               │   • Max 10 total                                            │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ COUPON_CODE   │ Header : Optional (TEXT, IMAGE, VIDEO, DOCUMENT)            │
│  (via LTO or  │ Body   : Required                                           │
│   standalone) │ Footer : Optional, max 60 chars                             │
│               │ Buttons: Exactly 1 copy_code button (required)              │
│               │   • Additional: quick_reply only (max 3)                    │
│               │   ⚠ No URL or phone_number buttons                         │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ LIMITED_TIME  │ Header : Optional (IMAGE, VIDEO, DOCUMENT, TEXT)            │
│ _OFFER (LTO)  │ Body   : Required                                           │
│               │ Footer : ❌ Not allowed                                     │
│               │ LTO    : Required — text max 16 chars, has_expiration bool  │
│               │ Buttons: Exactly 1 copy_code (required), max 2 url          │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ MPM (Multi-   │ Header : Optional                                           │
│ Product Msg)  │ Body   : Required                                           │
│               │ Footer : Optional                                           │
│               │ Buttons: ❌ Not allowed                                     │
│               │ Product List: Required — 1–10 sections, max 30 products     │
│               │   • Section title max 24 chars                              │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ SPM (Single   │ Header : ❌ Not allowed                                     │
│ Product Msg)  │ Body   : Required                                           │
│               │ Footer : Optional                                           │
│               │ Buttons: ❌ Not allowed                                     │
│               │ Product: Required — product_retailer_id                     │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ PRODUCT_CARD  │ Header : ❌ Not at template level (image from catalog)      │
│ _CAROUSEL     │ Body   : Required                                           │
│               │ Footer : ❌ Not allowed                                     │
│               │ Cards  : 2–10 product cards with product_retailer_id        │
│               │   Per card:                                                 │
│               │   • buttons: 1–2 (quick_reply, url), max 25 char text       │
│               │   • All cards must have SAME number of buttons              │
└───────────────┴──────────────────────────────────────────────────────────────┘

Reference: https://docs.wati.io/reference/post_api-v1-whatsapp-templates
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, model_validator
from wa.utility.data_model.wati.template_input import (WATIButtonsType,
                                                       WATIHeaderFormat,
                                                       WATITemplateButton,
                                                       WATITemplateSubCategory)

from .base_validator import BaseTemplateValidator

# =============================================================================
# Guardrails constant – machine-readable rules per sub-category
# =============================================================================

MARKETING_GUARDRAILS: Dict[str, Dict[str, Any]] = {
    "STANDARD": {
        "allowed_headers": ["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "NONE"],
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": True,
        "footer_max_length": 60,
        "buttons": {
            "max_total": 10,
            "allowed_types": ["quick_reply", "url", "phone_number"],
            "limits": {
                "quick_reply": 10,
                "url": 2,
                "phone_number": 1,
            },
            "can_mix_qr_and_cta": True,  # only with buttonsType = quick_reply_and_call_to_action
        },
        "default_ttl_seconds": 43200,
    },
    "CAROUSEL": {
        "allowed_headers": [],  # no template-level header
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": False,
        "cards": {
            "min": 2,
            "max": 10,
            "card_header_formats": ["IMAGE", "VIDEO"],
            "card_body_max_length": 160,
            "card_buttons_min": 1,
            "card_buttons_max": 2,
            "card_button_types": ["quick_reply", "url", "phone_number"],
            "all_cards_same_button_count": True,
        },
        "buttons": {
            "max_total": 0,  # template-level buttons not allowed
            "allowed_types": [],
        },
    },
    "CATALOG": {
        "allowed_headers": ["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "NONE"],
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": True,
        "footer_max_length": 60,
        "buttons": {
            "max_total": 10,
            "allowed_types": ["catalog", "url", "phone_number", "quick_reply"],
            "required_types": ["catalog"],
            "limits": {
                "catalog": 1,  # exactly 1 catalog button
                "url": 2,
                "phone_number": 1,
                "quick_reply": 10,
            },
        },
    },
    "COUPON_CODE": {
        "allowed_headers": ["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "NONE"],
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": True,
        "footer_max_length": 60,
        "buttons": {
            "max_total": 4,  # 1 copy_code + max 3 quick_reply
            "allowed_types": ["copy_code", "quick_reply"],
            "required_types": ["copy_code"],
            "limits": {
                "copy_code": 1,  # exactly 1
                "quick_reply": 3,
            },
        },
    },
    "LIMITED_TIME_OFFER": {
        "allowed_headers": ["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "NONE"],
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": False,
        "lto_component_required": True,
        "lto_text_max_length": 16,
        "buttons": {
            "max_total": 3,  # 1 copy_code + up to 2 url
            "allowed_types": ["copy_code", "url"],
            "required_types": ["copy_code"],
            "limits": {
                "copy_code": 1,
                "url": 2,
            },
        },
    },
    "MPM": {
        "allowed_headers": ["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "NONE"],
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": True,
        "footer_max_length": 60,
        "buttons": {
            "max_total": 0,  # no buttons allowed
            "allowed_types": [],
        },
        "product_list": {
            "required": True,
            "sections_min": 1,
            "sections_max": 10,
            "products_max_total": 30,
            "section_title_max_length": 24,
        },
    },
    "SPM": {
        "allowed_headers": [],  # no header allowed
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": True,
        "footer_max_length": 60,
        "buttons": {
            "max_total": 0,  # no buttons allowed
            "allowed_types": [],
        },
        "product": {
            "required": True,
            "needs_retailer_id": True,
        },
    },
    "PRODUCT_CARD_CAROUSEL": {
        "allowed_headers": [],  # no template-level header (image from catalog)
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": False,
        "cards": {
            "min": 2,
            "max": 10,
            "card_has_product_retailer_id": True,
            "card_buttons_min": 1,
            "card_buttons_max": 2,
            "card_button_types": ["quick_reply", "url"],
            "card_button_text_max": 25,
            "all_cards_same_button_count": True,
        },
        "buttons": {
            "max_total": 0,
            "allowed_types": [],
        },
    },
}


class MarketingTemplateValidator(BaseTemplateValidator):
    """
    Validator for WATI marketing templates.

    Extends BaseTemplateValidator with marketing-specific:
    - Category enforcement (MARKETING only)
    - Sub-category support (STANDARD, CAROUSEL, CATALOG, etc.)
    - Per-sub-category header, footer, and button rules
    - Carousel / product card validation

    Use ``MARKETING_GUARDRAILS`` dict for programmatic access to the rules.

    Usage:
        validator = MarketingTemplateValidator(
            elementName="summer_sale",
            language="en",
            body="Hi {{1}}! Our {{2}} sale is live. Get {{3}}% off!",
            header=WATITemplateHeader(format="IMAGE", media_url="https://example.com/banner.jpg"),
            buttonsType="call_to_action",
            buttons=[
                {"type": "url", "text": "Shop Now", "url": "https://example.com/sale"},
            ],
            customParams=[
                {"name": "1", "value": "John"},
                {"name": "2", "value": "Summer"},
                {"name": "3", "value": "50"},
            ],
        )
        payload = validator.to_wati_payload()
    """

    category: Literal["MARKETING", "marketing"] = "MARKETING"
    subCategory: Optional[WATITemplateSubCategory] = Field(
        default=WATITemplateSubCategory.STANDARD,
        description="Marketing template sub-category",
    )

    # Carousel / product card fields (populated externally)
    cards: Optional[List[Dict[str, Any]]] = Field(
        None, description="Carousel or product card data (2-10 cards)"
    )

    # Product fields for MPM / SPM
    product_retailer_id: Optional[str] = Field(
        None, description="Product retailer ID for SPM templates"
    )
    product_sections: Optional[List[Dict[str, Any]]] = Field(
        None, description="Product list sections for MPM templates"
    )

    # LTO fields
    lto_text: Optional[str] = Field(
        None, max_length=16, description="Limited time offer text (max 16 chars)"
    )
    lto_has_expiration: Optional[bool] = Field(
        None, description="Whether the LTO has an expiration countdown"
    )

    # =========================================================================
    # Helpers
    # =========================================================================

    def _get_guardrails(self) -> Dict[str, Any]:
        """Return the guardrails dict for the current sub-category."""
        key = (self.subCategory or WATITemplateSubCategory.STANDARD).value
        return MARKETING_GUARDRAILS.get(key, MARKETING_GUARDRAILS["STANDARD"])

    def _count_buttons_by_type(self) -> Dict[str, int]:
        """Count buttons grouped by type."""
        counts: Dict[str, int] = {}
        if self.buttons:
            for btn in self.buttons:
                btn_type = btn.type if isinstance(btn, WATITemplateButton) else btn.get("type", "unknown")
                counts[btn_type] = counts.get(btn_type, 0) + 1
        return counts

    # =========================================================================
    # Marketing-Specific Validators
    # =========================================================================

    @model_validator(mode="after")
    def validate_header_for_sub_category(self):
        """Validate header against sub-category guardrails."""
        guardrails = self._get_guardrails()
        allowed = guardrails.get("allowed_headers", [])

        if self.header:
            header_fmt = (
                self.header.format.value
                if isinstance(self.header.format, WATIHeaderFormat)
                else str(self.header.format)
            )
            if allowed and header_fmt not in allowed:
                raise ValueError(
                    f"{self.subCategory.value} marketing templates do not allow "
                    f"'{header_fmt}' headers. Allowed: {allowed}"
                )
            if not allowed:
                raise ValueError(
                    f"{self.subCategory.value} marketing templates do not allow "
                    f"template-level headers."
                )
        return self

    @model_validator(mode="after")
    def validate_footer_for_sub_category(self):
        """Validate footer against sub-category guardrails."""
        guardrails = self._get_guardrails()
        if self.footer and not guardrails.get("footer_allowed", True):
            raise ValueError(
                f"{self.subCategory.value} marketing templates do not allow footers."
            )
        return self

    @model_validator(mode="after")
    def validate_marketing_buttons(self):
        """
        Validate button constraints per sub-category guardrails.

        Rules are looked up from MARKETING_GUARDRAILS[subCategory]["buttons"].
        """
        guardrails = self._get_guardrails()
        btn_rules = guardrails.get("buttons", {})
        max_total = btn_rules.get("max_total", 10)
        allowed_types = btn_rules.get("allowed_types", [])
        required_types = btn_rules.get("required_types", [])
        limits = btn_rules.get("limits", {})

        if not self.buttons:
            if required_types:
                raise ValueError(
                    f"{self.subCategory.value} marketing templates require "
                    f"at least one button of type: {required_types}"
                )
            return self

        # No buttons allowed for this sub-category?
        if max_total == 0:
            raise ValueError(
                f"{self.subCategory.value} marketing templates do not allow buttons."
            )

        # Total count
        if len(self.buttons) > max_total:
            raise ValueError(
                f"{self.subCategory.value} marketing templates allow max "
                f"{max_total} buttons, got {len(self.buttons)}."
            )

        counts = self._count_buttons_by_type()

        # Validate allowed types
        if allowed_types:
            for btn_type in counts:
                if btn_type not in allowed_types:
                    raise ValueError(
                        f"Button type '{btn_type}' is not allowed for "
                        f"{self.subCategory.value} marketing templates. "
                        f"Allowed: {allowed_types}"
                    )

        # Validate required types
        for req in required_types:
            if req not in counts:
                raise ValueError(
                    f"{self.subCategory.value} marketing templates require "
                    f"at least one '{req}' button."
                )

        # Validate per-type limits
        for btn_type, count in counts.items():
            max_for_type = limits.get(btn_type)
            if max_for_type is not None and count > max_for_type:
                raise ValueError(
                    f"{self.subCategory.value} marketing templates allow max "
                    f"{max_for_type} '{btn_type}' buttons, got {count}."
                )

        # QR + CTA mixing guard (STANDARD only)
        qr_count = counts.get("quick_reply", 0)
        cta_count = counts.get("url", 0) + counts.get("phone_number", 0)
        if qr_count > 0 and cta_count > 0:
            can_mix = btn_rules.get("can_mix_qr_and_cta", False)
            if can_mix and self.buttonsType != WATIButtonsType.QUICK_REPLY_AND_CALL_TO_ACTION:
                raise ValueError(
                    "Cannot mix quick_reply and call_to_action buttons. "
                    "Set buttonsType to 'quick_reply_and_call_to_action'."
                )
            elif not can_mix:
                raise ValueError(
                    f"{self.subCategory.value} marketing templates cannot mix "
                    f"quick_reply and call_to_action buttons."
                )

        return self

    @model_validator(mode="after")
    def validate_carousel_requirements(self):
        """
        Validate CAROUSEL sub-category requirements.

        Rules:
        - 2–10 cards required
        - Each card: header (IMAGE/VIDEO), body (≤160 chars), buttons (1–2)
        - All cards must have the same number of buttons
        - No template-level buttons or footer
        """
        if self.subCategory != WATITemplateSubCategory.CAROUSEL:
            return self

        if not self.cards or len(self.cards) < 2:
            raise ValueError("CAROUSEL marketing templates require 2–10 cards.")
        if len(self.cards) > 10:
            raise ValueError(
                f"CAROUSEL marketing templates allow max 10 cards, got {len(self.cards)}."
            )

        card_rules = MARKETING_GUARDRAILS["CAROUSEL"]["cards"]
        button_counts = []

        for i, card in enumerate(self.cards):
            card_header = card.get("header", {})
            card_header_fmt = card_header.get("format", "").upper() if card_header else ""
            if card_header_fmt not in card_rules["card_header_formats"]:
                raise ValueError(
                    f"Card {i+1}: header format must be IMAGE or VIDEO, got '{card_header_fmt}'."
                )

            card_body = card.get("body", "")
            if not card_body:
                raise ValueError(f"Card {i+1}: body is required.")
            if len(card_body) > card_rules["card_body_max_length"]:
                raise ValueError(
                    f"Card {i+1}: body max {card_rules['card_body_max_length']} chars, "
                    f"got {len(card_body)}."
                )

            card_buttons = card.get("buttons", [])
            if len(card_buttons) < card_rules["card_buttons_min"]:
                raise ValueError(f"Card {i+1}: at least {card_rules['card_buttons_min']} button(s) required.")
            if len(card_buttons) > card_rules["card_buttons_max"]:
                raise ValueError(f"Card {i+1}: max {card_rules['card_buttons_max']} buttons, got {len(card_buttons)}.")
            button_counts.append(len(card_buttons))

            for btn in card_buttons:
                btn_type = btn.get("type", "") if isinstance(btn, dict) else getattr(btn, "type", "")
                if btn_type not in card_rules["card_button_types"]:
                    raise ValueError(
                        f"Card {i+1}: button type '{btn_type}' not allowed. "
                        f"Allowed: {card_rules['card_button_types']}"
                    )

        if card_rules.get("all_cards_same_button_count") and len(set(button_counts)) > 1:
            raise ValueError(
                f"All carousel cards must have the same number of buttons. Found: {button_counts}"
            )

        return self

    @model_validator(mode="after")
    def validate_lto_requirements(self):
        """
        Validate LIMITED_TIME_OFFER sub-category requirements.

        Rules:
        - lto_text required, max 16 chars
        - lto_has_expiration required
        - No footer allowed
        - 1 copy_code button required
        """
        if self.subCategory != WATITemplateSubCategory.LIMITED_TIME_OFFER:
            return self

        if not self.lto_text:
            raise ValueError("LIMITED_TIME_OFFER templates require lto_text (max 16 chars).")
        if len(self.lto_text) > 16:
            raise ValueError(f"lto_text max 16 chars, got {len(self.lto_text)}.")
        if self.lto_has_expiration is None:
            raise ValueError("LIMITED_TIME_OFFER templates require lto_has_expiration flag.")
        return self

    @model_validator(mode="after")
    def validate_mpm_requirements(self):
        """
        Validate MPM (Multi-Product Message) sub-category.

        Rules:
        - product_sections required (1–10 sections)
        - Max 30 products total across all sections
        - Section title max 24 chars
        - No buttons allowed
        """
        sub = (self.subCategory or WATITemplateSubCategory.STANDARD).value
        if sub != "MPM":
            return self

        if not self.product_sections:
            raise ValueError("MPM templates require product_sections.")

        rules = MARKETING_GUARDRAILS.get("MPM", {}).get("product_list", {})
        if len(self.product_sections) < rules.get("sections_min", 1):
            raise ValueError(f"MPM templates require at least {rules.get('sections_min', 1)} section.")
        if len(self.product_sections) > rules.get("sections_max", 10):
            raise ValueError(f"MPM templates allow max {rules.get('sections_max', 10)} sections.")

        total_products = 0
        for i, section in enumerate(self.product_sections):
            title = section.get("title", "")
            if len(title) > rules.get("section_title_max_length", 24):
                raise ValueError(f"Section {i+1}: title max {rules.get('section_title_max_length', 24)} chars.")
            total_products += len(section.get("products", []))

        if total_products > rules.get("products_max_total", 30):
            raise ValueError(f"MPM templates allow max {rules.get('products_max_total', 30)} products total, got {total_products}.")

        return self

    @model_validator(mode="after")
    def validate_spm_requirements(self):
        """
        Validate SPM (Single Product Message) sub-category.

        Rules:
        - product_retailer_id required
        - No header allowed
        - No buttons allowed
        """
        sub = (self.subCategory or WATITemplateSubCategory.STANDARD).value
        if sub != "SPM":
            return self

        if not self.product_retailer_id:
            raise ValueError("SPM templates require product_retailer_id.")
        return self
