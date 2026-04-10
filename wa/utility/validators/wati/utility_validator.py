"""
WATI Utility Template Validator

Validates utility templates before submission to the WATI API.

Utility templates are used for transactional messages like order confirmations,
shipping updates, account notifications, and appointment reminders.
They have higher delivery priority and different rate limits compared to
marketing templates.

┌──────────────────────────────────────────────────────────────────────────────┐
│                     UTILITY TEMPLATE GUARDRAILS                             │
├───────────────┬──────────────────────────────────────────────────────────────┤
│ Sub-Category  │ Rules                                                       │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ STANDARD      │ Header : TEXT, IMAGE, VIDEO, DOCUMENT (optional)            │
│               │ Body   : Required, max 1024 chars, supports {{variables}}   │
│               │ Footer : Optional, max 60 chars, NO variables               │
│               │ Buttons: Max 10 total                                       │
│               │   • quick_reply   : max 10                                  │
│               │   • url           : max 2                                   │
│               │   • phone_number  : max 1                                   │
│               │   • copy_code     : max 1                                   │
│               │   • flow          : max 1                                   │
│               │   ⚠ Cannot mix QR + CTA unless buttonsType =               │
│               │     "quick_reply_and_call_to_action"                        │
│               │ TTL    : Default 43200s (12h)                               │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ ORDER_STATUS  │ Header : TEXT, IMAGE, VIDEO, DOCUMENT (optional)            │
│               │ Body   : Required, must include order-related variables     │
│               │ Footer : Optional, max 60 chars                             │
│               │ Order  : order_status component recommended                 │
│               │   • reference_id  : max 35 chars (required)                 │
│               │   • status        : pending | processing | confirmed |      │
│               │                     shipped | out_for_delivery | delivered | │
│               │                     cancelled | returned | refunded |       │
│               │                     failed | on_hold                        │
│               │   • shipping_info : carrier (≤100), tracking_number (≤100), │
│               │                     tracking_url (≤2000), address fields    │
│               │ Buttons: Max 3 total                                        │
│               │   • url           : max 2                                   │
│               │   • quick_reply   : max 3                                   │
│               │   • phone_number  : max 1                                   │
├───────────────┼──────────────────────────────────────────────────────────────┤
│ CHECKOUT      │ Header : TEXT, IMAGE, VIDEO, DOCUMENT (optional)            │
│ _BUTTON       │ Body   : Required                                           │
│ (Order        │ Footer : Optional, max 60 chars                             │
│  Details)     │ Order  : order_details component required                   │
│               │   • action        : review_and_pay | review_order           │
│               │   • items         : 1–999, each with name (≤60), amount, qty│
│               │   • reference_id  : max 35 chars                            │
│               │ Buttons: Max 3 total                                        │
│               │   • url           : max 2                                   │
│               │   • quick_reply   : max 3                                   │
│               │   • copy_code     : max 1                                   │
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

VALID_ORDER_STATUSES = [
    "pending",
    "processing",
    "confirmed",
    "shipped",
    "out_for_delivery",
    "delivered",
    "cancelled",
    "returned",
    "refunded",
    "failed",
    "on_hold",
]

UTILITY_GUARDRAILS: Dict[str, Dict[str, Any]] = {
    "STANDARD": {
        "allowed_headers": ["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "NONE"],
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": True,
        "footer_max_length": 60,
        "buttons": {
            "max_total": 10,
            "allowed_types": ["quick_reply", "url", "phone_number", "copy_code", "flow"],
            "limits": {
                "quick_reply": 10,
                "url": 2,
                "phone_number": 1,
                "copy_code": 1,
                "flow": 1,
            },
            "can_mix_qr_and_cta": True,
        },
        "default_ttl_seconds": 43200,
    },
    "ORDER_STATUS": {
        "allowed_headers": ["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "NONE"],
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": True,
        "footer_max_length": 60,
        "order_status_component": {
            "recommended": True,
            "reference_id_max_length": 35,
            "valid_statuses": VALID_ORDER_STATUSES,
            "shipping_info": {
                "carrier_max_length": 100,
                "tracking_number_max_length": 100,
                "tracking_url_max_length": 2000,
            },
        },
        "buttons": {
            "max_total": 3,
            "allowed_types": ["url", "quick_reply", "phone_number"],
            "limits": {
                "url": 2,
                "quick_reply": 3,
                "phone_number": 1,
            },
        },
        "default_ttl_seconds": 43200,
    },
    "CHECKOUT_BUTTON": {
        "allowed_headers": ["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "NONE"],
        "body_required": True,
        "body_max_length": 1024,
        "footer_allowed": True,
        "footer_max_length": 60,
        "order_details_component": {
            "required": True,
            "actions": ["review_and_pay", "review_order"],
            "items_min": 1,
            "items_max": 999,
            "item_name_max_length": 60,
            "reference_id_max_length": 35,
        },
        "buttons": {
            "max_total": 3,
            "allowed_types": ["url", "quick_reply", "copy_code"],
            "limits": {
                "url": 2,
                "quick_reply": 3,
                "copy_code": 1,
            },
        },
        "default_ttl_seconds": 43200,
    },
}


class UtilityTemplateValidator(BaseTemplateValidator):
    """
    Validator for WATI utility templates.

    Extends BaseTemplateValidator with utility-specific:
    - Category enforcement (UTILITY only)
    - Sub-category support (STANDARD, ORDER_STATUS, CHECKOUT_BUTTON)
    - Per-sub-category header, footer, and button rules
    - Order status / order details validation

    Use ``UTILITY_GUARDRAILS`` dict for programmatic access to the rules.

    Usage:
        validator = UtilityTemplateValidator(
            elementName="order_confirmation",
            language="en",
            body="Hi {{1}}, your order #{{2}} has been confirmed. Delivery: {{3}}.",
            buttonsType="call_to_action",
            buttons=[
                {"type": "url", "text": "Track Order", "url": "https://example.com/track/{{1}}"},
            ],
            customParams=[
                {"name": "1", "value": "John"},
                {"name": "2", "value": "ORD-12345"},
                {"name": "3", "value": "Feb 15, 2026"},
            ],
        )
        payload = validator.to_wati_payload()
    """

    category: Literal["UTILITY", "utility"] = "UTILITY"
    subCategory: Optional[WATITemplateSubCategory] = Field(
        default=WATITemplateSubCategory.STANDARD,
        description="Utility template sub-category",
    )
    message_send_ttl_seconds: Optional[int] = Field(
        default=43200,
        description="Message send TTL in seconds (default 12 hours)",
    )

    # ORDER_STATUS fields
    order_reference_id: Optional[str] = Field(
        None, max_length=35, description="Order reference ID (max 35 chars)"
    )
    order_status: Optional[str] = Field(
        None, description="Current order status value"
    )
    shipping_carrier: Optional[str] = Field(
        None, max_length=100, description="Shipping carrier name"
    )
    tracking_number: Optional[str] = Field(
        None, max_length=100, description="Shipment tracking number"
    )
    tracking_url: Optional[str] = Field(
        None, max_length=2000, description="Shipment tracking URL"
    )

    # CHECKOUT_BUTTON fields
    order_action: Optional[str] = Field(
        None, description="Order action: 'review_and_pay' or 'review_order'"
    )
    order_items: Optional[List[Dict[str, Any]]] = Field(
        None, description="Order items list for checkout templates"
    )

    # =========================================================================
    # Helpers
    # =========================================================================

    def _get_guardrails(self) -> Dict[str, Any]:
        """Return the guardrails dict for the current sub-category."""
        key = (self.subCategory or WATITemplateSubCategory.STANDARD).value
        return UTILITY_GUARDRAILS.get(key, UTILITY_GUARDRAILS["STANDARD"])

    def _count_buttons_by_type(self) -> Dict[str, int]:
        """Count buttons grouped by type."""
        counts: Dict[str, int] = {}
        if self.buttons:
            for btn in self.buttons:
                btn_type = btn.type if isinstance(btn, WATITemplateButton) else btn.get("type", "unknown")
                counts[btn_type] = counts.get(btn_type, 0) + 1
        return counts

    # =========================================================================
    # Utility-Specific Validators
    # =========================================================================

    @model_validator(mode="after")
    def validate_header_for_sub_category(self):
        """Validate header against sub-category guardrails."""
        guardrails = self._get_guardrails()
        allowed = guardrails.get("allowed_headers", [])

        if self.header and allowed:
            header_fmt = (
                self.header.format.value
                if isinstance(self.header.format, WATIHeaderFormat)
                else str(self.header.format)
            )
            if header_fmt not in allowed:
                raise ValueError(
                    f"{self.subCategory.value} utility templates do not allow "
                    f"'{header_fmt}' headers. Allowed: {allowed}"
                )
        return self

    @model_validator(mode="after")
    def validate_footer_for_sub_category(self):
        """Validate footer against sub-category guardrails."""
        guardrails = self._get_guardrails()
        if self.footer and not guardrails.get("footer_allowed", True):
            raise ValueError(
                f"{self.subCategory.value} utility templates do not allow footers."
            )
        return self

    @model_validator(mode="after")
    def validate_utility_buttons(self):
        """
        Validate button constraints per sub-category guardrails.

        Rules are looked up from UTILITY_GUARDRAILS[subCategory]["buttons"].
        """
        guardrails = self._get_guardrails()
        btn_rules = guardrails.get("buttons", {})
        max_total = btn_rules.get("max_total", 10)
        allowed_types = btn_rules.get("allowed_types", [])
        limits = btn_rules.get("limits", {})

        if not self.buttons:
            return self

        # Total count
        if len(self.buttons) > max_total:
            raise ValueError(
                f"{self.subCategory.value} utility templates allow max "
                f"{max_total} buttons, got {len(self.buttons)}."
            )

        counts = self._count_buttons_by_type()

        # Validate allowed types
        if allowed_types:
            for btn_type in counts:
                if btn_type not in allowed_types:
                    raise ValueError(
                        f"Button type '{btn_type}' is not allowed for "
                        f"{self.subCategory.value} utility templates. "
                        f"Allowed: {allowed_types}"
                    )

        # Validate per-type limits
        for btn_type, count in counts.items():
            max_for_type = limits.get(btn_type)
            if max_for_type is not None and count > max_for_type:
                raise ValueError(
                    f"{self.subCategory.value} utility templates allow max "
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

        return self

    @model_validator(mode="after")
    def validate_order_status_requirements(self):
        """
        Validate ORDER_STATUS sub-category requirements.

        Rules:
        - At least one customParam for order information
        - order_reference_id max 35 chars
        - order_status must be a valid status value
        - Shipping info field length constraints
        """
        if self.subCategory != WATITemplateSubCategory.ORDER_STATUS:
            return self

        rules = UTILITY_GUARDRAILS["ORDER_STATUS"]

        # customParams required for order variables
        if not self.customParams or len(self.customParams) == 0:
            raise ValueError(
                "ORDER_STATUS templates require at least one custom parameter "
                "for order information."
            )

        # Validate order_status value if provided
        if self.order_status:
            os_rules = rules.get("order_status_component", {})
            valid = os_rules.get("valid_statuses", VALID_ORDER_STATUSES)
            if self.order_status not in valid:
                raise ValueError(
                    f"Invalid order status '{self.order_status}'. "
                    f"Valid: {valid}"
                )

        # Validate reference_id length
        if self.order_reference_id:
            max_len = rules.get("order_status_component", {}).get("reference_id_max_length", 35)
            if len(self.order_reference_id) > max_len:
                raise ValueError(
                    f"order_reference_id max {max_len} chars, got {len(self.order_reference_id)}."
                )

        return self

    @model_validator(mode="after")
    def validate_checkout_requirements(self):
        """
        Validate CHECKOUT_BUTTON sub-category requirements.

        Rules:
        - order_action required: 'review_and_pay' or 'review_order'
        - order_items required: 1–999 items
        - Item name max 60 chars
        """
        if self.subCategory != WATITemplateSubCategory.CHECKOUT_BUTTON:
            return self

        rules = UTILITY_GUARDRAILS["CHECKOUT_BUTTON"].get("order_details_component", {})

        # Validate order action
        if not self.order_action:
            raise ValueError("CHECKOUT_BUTTON templates require order_action.")
        valid_actions = rules.get("actions", ["review_and_pay", "review_order"])
        if self.order_action not in valid_actions:
            raise ValueError(
                f"Invalid order_action '{self.order_action}'. Valid: {valid_actions}"
            )

        # Validate order items
        if not self.order_items:
            raise ValueError("CHECKOUT_BUTTON templates require order_items.")
        if len(self.order_items) < rules.get("items_min", 1):
            raise ValueError(
                f"CHECKOUT_BUTTON templates require at least {rules.get('items_min', 1)} item."
            )
        if len(self.order_items) > rules.get("items_max", 999):
            raise ValueError(
                f"CHECKOUT_BUTTON templates allow max {rules.get('items_max', 999)} items."
            )

        # Validate item names
        name_max = rules.get("item_name_max_length", 60)
        for i, item in enumerate(self.order_items):
            name = item.get("name", "")
            if not name:
                raise ValueError(f"Order item {i+1}: name is required.")
            if len(name) > name_max:
                raise ValueError(f"Order item {i+1}: name max {name_max} chars, got {len(name)}.")

        return self
