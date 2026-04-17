"""
Canonical WhatsApp Models - BSP Agnostic (Django ORM)

This module contains clean, canonical Django models for WhatsApp integration
that are completely BSP (Business Solution Provider) agnostic.

These models are designed to:
1. Be the source of truth for frontend interactions
2. Store data in a normalized, BSP-independent format
3. Enable background jobs to transform data to/from BSP-specific formats
4. Provide clean API schemas for REST endpoints

Architecture:
    Frontend <---> Canonical Models (new_models.py) <---> BSP Adapters <---> BSP APIs

    - Frontend always interacts with these canonical models
    - Background tasks use adapters to sync with BSP-specific APIs
    - BSP-specific transformations happen in adapters, not in models

Supported BSPs (via adapters):
- META Direct API (native WhatsApp Business API)
- Gupshup
- Twilio (future)
- MessageBird (future)

Usage:
    from wa.new_models import WATemplate, WAMessage

    # Create a canonical template (frontend interaction)
    template = WATemplate.objects.create(
        name="welcome_message",
        language_code="en",
        category=TemplateCategory.MARKETING,
        content="Hello {{name}}, welcome to {{company}}!",
    )

    # Background job transforms and syncs to BSP
    from wa.adapters import MetaAdapter
    meta_payload = MetaAdapter.to_bsp(template)
    response = meta_api.create_template(meta_payload)
    template.meta_template_id = response['id']
    template.save()
"""

import uuid

from django.db import models

from abstract.models import BaseModel, BaseTenantModelForFilterUser, BaseWebhookDumps
from broadcast.models import Broadcast
from contacts.models import TenantContact
from message_templates.models import BaseTemplateMessages, TemplateNumber
from tenants.models import BSPChoices, Tenant, TenantWAApp
from wa.managers import WABroadcastManager, WAContactsManager

# Re-export so existing ``from wa.models import BSPChoices`` still works
BSPChoices = BSPChoices

# Alias so the rest of the wa app can use ``WAApp`` uniformly
WAApp = TenantWAApp

# =============================================================================
# ENUMS - Canonical choices for WhatsApp entities
# =============================================================================


class TemplateCategory(models.TextChoices):
    """WhatsApp template categories as defined by META."""

    AUTHENTICATION = "AUTHENTICATION", "Authentication"
    MARKETING = "MARKETING", "Marketing"
    UTILITY = "UTILITY", "Utility"


class TemplateStatus(models.TextChoices):
    """Template approval status from META review."""

    DRAFT = "DRAFT", "Draft"
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    PAUSED = "PAUSED", "Paused"
    DISABLED = "DISABLED", "Disabled"
    FAILED = "FAILED", "Failed"


# Alias for backward compatibility
StatusChoices = TemplateStatus


class TemplateType(models.TextChoices):
    """Types of WhatsApp templates based on media content."""

    TEXT = "TEXT", "Text"
    IMAGE = "IMAGE", "Image"
    VIDEO = "VIDEO", "Video"
    DOCUMENT = "DOCUMENT", "Document"
    LOCATION = "LOCATION", "Location"
    AUDIO = "AUDIO", "Audio"
    CAROUSEL = "CAROUSEL", "Carousel"
    CATALOG = "CATALOG", "Catalog"
    PRODUCT = "PRODUCT", "Product"
    ORDER_DETAILS = "ORDER_DETAILS", "Order Details"


class MessageStatus(models.TextChoices):
    """Message delivery status as reported by WhatsApp."""

    PENDING = "PENDING", "Pending"
    SENT = "SENT", "Sent"
    DELIVERED = "DELIVERED", "Delivered"
    READ = "READ", "Read"
    FAILED = "FAILED", "Failed"
    EXPIRED = "EXPIRED", "Expired"


class MessageDirection(models.TextChoices):
    """Direction of message flow."""

    INBOUND = "INBOUND", "Inbound"
    OUTBOUND = "OUTBOUND", "Outbound"


class MessageType(models.TextChoices):
    """Types of WhatsApp messages."""

    TEXT = "TEXT", "Text"
    TEMPLATE = "TEMPLATE", "Template"
    IMAGE = "IMAGE", "Image"
    VIDEO = "VIDEO", "Video"
    DOCUMENT = "DOCUMENT", "Document"
    AUDIO = "AUDIO", "Audio"
    LOCATION = "LOCATION", "Location"
    CONTACTS = "CONTACTS", "Contacts"
    STICKER = "STICKER", "Sticker"
    INTERACTIVE = "INTERACTIVE", "Interactive"
    REACTION = "REACTION", "Reaction"


class ButtonType(models.TextChoices):
    """Types of interactive buttons in templates."""

    QUICK_REPLY = "QUICK_REPLY", "Quick Reply"
    URL = "URL", "URL"
    PHONE_NUMBER = "PHONE_NUMBER", "Phone Number"
    COPY_CODE = "COPY_CODE", "Copy Code"
    FLOW = "FLOW", "Flow"
    VOICE_CALL = "VOICE_CALL", "Voice Call"  # Legacy alias for CALL_REQUEST
    CALL_REQUEST = "CALL_REQUEST", "Call Request"
    CATALOG = "CATALOG", "Catalog"
    ORDER_DETAILS = "ORDER_DETAILS", "Order Details"
    OTP = "OTP", "OTP"


class WebhookEventType(models.TextChoices):
    """Types of webhook events from WhatsApp."""

    MESSAGE = "MESSAGE", "Message"
    STATUS = "STATUS", "Status"
    TEMPLATE = "TEMPLATE", "Template"
    BILLING = "BILLING", "Billing"
    ACCOUNT = "ACCOUNT", "Account"
    PAYMENT = "PAYMENT", "Payment"
    UNKNOWN = "UNKNOWN", "Unknown"


class SubscriptionStatus(models.TextChoices):
    """Webhook subscription status."""

    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    PENDING = "PENDING", "Pending"
    FAILED = "FAILED", "Failed"


class BroadcastStatus(models.TextChoices):
    """Broadcast campaign status."""

    DRAFT = "DRAFT", "Draft"
    SCHEDULED = "SCHEDULED", "Scheduled"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent"
    PARTIALLY_SENT = "PARTIALLY_SENT", "Partially Sent"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


# =============================================================================
# CORE CANONICAL MODELS
# =============================================================================


class WATemplate(BaseTemplateMessages):
    """
    Canonical Message Template.

    Extends BaseTemplateMessages which provides:
    - name, description, is_active, created_at, updated_at (from BaseModel)
    - created_by, updated_by (from BaseModelWithOwner)
    - tag (ManyToMany to TenantTags)
    - content (TextField)
    - filter_by_user_tenant_fk pattern

    Frontend interacts with this model directly.
    Background jobs use adapters to sync with META/BSP APIs.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Channel platform — defaults to WHATSAPP for backward compatibility.
    platform = models.CharField(
        max_length=20,
        choices=[
            ("WHATSAPP", "WhatsApp"),
            ("SMS", "SMS"),
            ("TELEGRAM", "Telegram"),
            ("RCS", "RCS"),
        ],
        default="WHATSAPP",
        db_index=True,
        help_text="Channel this template belongs to.",
    )

    # Direct tenant link for non-WA templates (wa_app is nullable for them).
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="channel_templates",
        help_text="Tenant owning this template (set for non-WA templates).",
    )

    # Relationship to WA App (nullable for non-WA templates)
    wa_app = models.ForeignKey(WAApp, on_delete=models.CASCADE, related_name="templates", null=True, blank=True)

    # Link to TemplateNumber (used by Broadcast → TemplateNumber → WATemplate)
    # related_name kept as 'gupshup_template' for backward-compat with
    # broadcast models that access template_number.gupshup_template
    number = models.OneToOneField(
        TemplateNumber,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gupshup_template",
    )

    # Template identification (element_name is unique identifier for META)
    element_name = models.CharField(max_length=255, help_text="Unique template name (lowercase, underscores)")
    language_code = models.CharField(max_length=20, default="en", help_text="Template language (e.g., en, en_US)")

    # Classification
    category = models.CharField(max_length=20, choices=TemplateCategory.choices, default=TemplateCategory.MARKETING)
    template_type = models.CharField(max_length=20, choices=TemplateType.choices, default=TemplateType.TEXT)
    status = models.CharField(max_length=20, choices=TemplateStatus.choices, default=TemplateStatus.DRAFT)

    # Content fields - content is inherited from BaseTemplateMessages
    # content stores body text with named placeholders (e.g., {{name}}, {{order_id}})
    header = models.TextField(blank=True, null=True, help_text="Header text (optional)")
    footer = models.TextField(blank=True, null=True, help_text="Footer text (max 60 chars)")

    # Buttons stored as JSON array
    buttons = models.JSONField(blank=True, null=True, help_text="Array of button objects")

    # Examples for placeholder values (required by META for approval)
    example_body = models.JSONField(blank=True, null=True, help_text="Example values for body placeholders")
    example_header = models.JSONField(blank=True, null=True, help_text="Example values for header placeholders")

    # Media for header (IMAGE, VIDEO, DOCUMENT templates)
    media_handle = models.TextField(blank=True, null=True, help_text="META media handle ID")
    example_media_url = models.URLField(
        max_length=1024, blank=True, null=True, help_text="Example media URL for approval"
    )
    tenant_media = models.ForeignKey(
        "tenants.TenantMedia",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="templates",
        help_text="Local media file for header (IMAGE/VIDEO/DOCUMENT templates)",
    )

    # Carousel cards
    cards = models.JSONField(blank=True, null=True, help_text="Carousel card objects")
    card_media = models.ManyToManyField(
        "tenants.TenantMedia",
        blank=True,
        related_name="card_templates",
        help_text="Local media files for carousel cards (linked by TenantMedia.card_index)",
    )

    # META/BSP sync identifiers
    meta_template_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    bsp_template_id = models.CharField(max_length=255, blank=True, null=True)

    # Placeholder mapping: {"content": {"1": "name", "2": "order_id"}}
    placeholder_mapping = models.JSONField(blank=True, null=True)

    # Metadata
    vertical = models.CharField(max_length=100, default="OTHER", help_text="Business vertical")
    is_lto = models.BooleanField(default=False, help_text="Limited Time Offer template")
    lto_text = models.CharField(max_length=255, blank=True, null=True)
    lto_has_expiration = models.BooleanField(default=True, help_text="Whether LTO has expiration countdown")

    # Error tracking
    error_message = models.TextField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)

    # Sync tracking
    last_synced_at = models.DateTimeField(blank=True, null=True)
    needs_sync = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "wa_template_v2"
        verbose_name = "WA Template"
        verbose_name_plural = "WA Templates"
        indexes = [
            models.Index(fields=["status", "needs_sync"]),
            models.Index(fields=["wa_app", "status"]),
            models.Index(fields=["platform", "tenant"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["wa_app", "element_name", "language_code"],
                condition=models.Q(wa_app__isnull=False),
                name="unique_wa_app_element_lang",
            ),
            models.UniqueConstraint(
                fields=["tenant", "platform", "element_name", "language_code"],
                condition=models.Q(wa_app__isnull=True),
                name="unique_tenant_platform_element_lang",
            ),
        ]

    def __str__(self):
        return f"{self.element_name} ({self.language_code}) - {self.status}"

    @property
    def template_id(self) -> str | None:
        """Read-only canonical template ID: prefers bsp_template_id, falls back to meta_template_id."""
        return self.bsp_template_id or self.meta_template_id

    def to_dict(self) -> dict:
        """Serialize for API response."""
        return {
            "id": str(self.id),
            "wa_app_id": str(self.wa_app_id),
            "name": self.name,
            "element_name": self.element_name,
            "language_code": self.language_code,
            "category": self.category,
            "template_type": self.template_type,
            "status": self.status,
            "content": self.content,
            "header": self.header,
            "footer": self.footer,
            "buttons": self.buttons,
            "example_body": self.example_body,
            "example_header": self.example_header,
            "media_handle": self.media_handle,
            "cards": self.cards,
            "meta_template_id": self.meta_template_id,
            "is_lto": self.is_lto,
            "error_message": self.error_message,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def get_card_media_by_index(self) -> dict:
        """
        Return a dict mapping card_index → TenantMedia for carousel cards.

        Example return: {0: <TenantMedia>, 1: <TenantMedia>, 2: <TenantMedia>}
        Only includes entries where card_index is not None.
        """
        return {tm.card_index: tm for tm in self.card_media.all() if tm.card_index is not None}

    # =========================================================================
    # BSP Payload Builders
    #
    # Canonical storage uses NAMED placeholders: {{customer_name}}, {{order_id}}
    #
    #   META Direct  – keeps named, sends parameter_format="NAMED" +
    #                  body_text_named_params examples
    #   Gupshup      – converts named → positional ({{1}}, {{2}}) +
    #                  comma-separated example string
    #   WATI         – passes as-is (accepts both named and positional),
    #                  customParams list provides example values
    #
    # placeholder_mapping (populated on save) lets adapters look up the
    # original name for any numbered slot when building examples.
    # =========================================================================

    def to_meta_payload(self) -> dict:
        """
        Convert to META Direct API template creation payload.

        META Direct natively supports NAMED parameters, so we keep
        placeholders as-is ({{customer_name}}) and set
        parameter_format = "NAMED".

        Background jobs use this to submit templates to META.
        """
        components = []

        # Header component (not allowed for LTO templates)
        if (self.header or self.media_handle or self.example_media_url) and not self.is_lto:
            header_component = {"type": "header"}
            if self.template_type == TemplateType.TEXT:
                header_component["format"] = "TEXT"
                header_component["text"] = self.header or ""
                if self.example_header:
                    header_component["example"] = {"header_text": self.example_header}
            else:
                header_component["format"] = self.template_type
                if self.media_handle:
                    header_component["example"] = {"header_handle": [self.media_handle]}
                elif self.example_media_url:
                    header_component["example"] = {"header_url": [self.example_media_url]}
            components.append(header_component)

        # Limited Time Offer component (LTO templates only)
        if self.is_lto:
            components.append(
                {
                    "type": "limited_time_offer",
                    "limited_time_offer": {
                        "text": self.lto_text or "Limited offer",
                        "has_expiration": self.lto_has_expiration,
                    },
                }
            )

        # Body component (required) — keep named placeholders for META Direct
        body_text = self.content or ""
        body_component = {"type": "body", "text": body_text}
        if self.example_body:
            # If body uses named params, build body_text_named_params
            import re

            named_params = re.findall(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}", body_text)
            if named_params and isinstance(self.example_body, list):
                # example_body is a list of values matching placeholder order
                named_param_examples = []
                for i, name in enumerate(dict.fromkeys(named_params)):
                    example_val = self.example_body[i] if i < len(self.example_body) else name
                    named_param_examples.append({"param_name": name, "example": str(example_val)})
                body_component["example"] = {"body_text_named_params": named_param_examples}
            else:
                body_component["example"] = {"body_text": [self.example_body]}
        components.append(body_component)

        # Footer component (not allowed for LTO templates)
        if self.footer and not self.is_lto:
            components.append({"type": "footer", "text": self.footer})

        # Buttons component
        if self.buttons:
            buttons_data = []
            for btn in self.buttons:
                btn_type = (btn.get("type", "quick_reply") or "quick_reply").lower()
                if btn_type == "copy_code":
                    # Meta API: {"type": "copy_code", "example": "CODE123"}
                    btn_data = {"type": btn_type, "example": btn.get("coupon_code") or btn.get("example", "")}
                else:
                    btn_data = {"type": btn_type, "text": btn.get("text", "")}
                    if btn.get("url"):
                        btn_data["url"] = btn["url"]
                        if btn.get("example"):
                            btn_data["example"] = btn["example"]
                    if btn.get("phone_number"):
                        btn_data["phone_number"] = btn["phone_number"]
                    if btn.get("flow_id"):
                        btn_data["flow_id"] = btn["flow_id"]
                        btn_data["flow_action"] = btn.get("flow_action", "navigate")
                        if btn.get("navigate_screen"):
                            btn_data["navigate_screen"] = btn["navigate_screen"]
                buttons_data.append(btn_data)
            components.append({"type": "buttons", "buttons": buttons_data})

        # ── CAROUSEL cards ───────────────────────────────────────────────
        # META requires a top-level CAROUSEL component whose "cards" list
        # holds per-card HEADER / BODY / BUTTONS sub-components.
        # When template_type is CAROUSEL the top-level HEADER is NOT
        # allowed — each card has its own header instead.
        if self.template_type == TemplateType.CAROUSEL and self.cards:
            # Remove top-level HEADER, FOOTER, and BUTTONS — META only allows
            # 'body' and 'carousel' at the top level for carousel templates.
            # Each card carries its own header/buttons internally.
            _disallowed = {"HEADER", "header", "FOOTER", "footer", "BUTTONS", "buttons"}
            components = [c for c in components if c.get("type") not in _disallowed]

            meta_cards = []
            for card in self.cards:
                card_components = []

                # Card HEADER — media (IMAGE or VIDEO)
                # Detect video from media_handle base64 content when headerType is missing.
                # 'dmlkZW8v' is base64 for 'video/' (video/mp4, video/3gpp, etc.)
                explicit_type = (card.get("headerType") or "").upper()
                if explicit_type:
                    header_format = explicit_type
                else:
                    handle = card.get("media_handle") or card.get("exampleMedia") or ""
                    header_format = "VIDEO" if "dmlkZW8v" in handle else "IMAGE"
                card_header = {"type": "header", "format": header_format}
                media_handle = card.get("media_handle")
                if media_handle:
                    card_header["example"] = {"header_handle": [media_handle]}
                card_components.append(card_header)

                # Card BODY
                card_body_text = card.get("body", "")
                if card_body_text:
                    card_components.append({"type": "body", "text": card_body_text})

                # Card BUTTONS
                card_buttons = card.get("buttons")
                if card_buttons:
                    meta_btn_list = []
                    for btn in card_buttons:
                        btn_data = {
                            "type": (btn.get("type", "quick_reply") or "quick_reply").lower(),
                            "text": btn.get("text", ""),
                        }
                        if btn.get("url"):
                            btn_data["url"] = btn["url"]
                            if btn.get("example"):
                                btn_data["example"] = btn["example"]
                        if btn.get("phone_number"):
                            btn_data["phone_number"] = btn["phone_number"]
                        meta_btn_list.append(btn_data)
                    card_components.append({"type": "buttons", "buttons": meta_btn_list})

                meta_cards.append({"components": card_components})

            components.append({"type": "CAROUSEL", "cards": meta_cards})

        payload = {
            "name": self.element_name,
            "language": self.language_code,
            "category": self.category,
            "components": components,
        }

        # Set parameter_format based on whether body uses named placeholders
        import re

        if self.content and re.search(r"\{\{[a-zA-Z_]\w*\}\}", self.content):
            payload["parameter_format"] = "NAMED"

        return payload

    # Template types whose "header" is media, not text.
    _MEDIA_TEMPLATE_TYPES = {"IMAGE", "VIDEO", "DOCUMENT"}

    # Template types that use per-card structure instead of template-level
    # header / footer / buttons.
    _CARD_BASED_TYPES = {"CAROUSEL"}

    def to_gupshup_payload(self) -> dict:
        """
        Convert to Gupshup Partner API template creation payload.

        Gupshup requires POSITIONAL placeholders (``{{1}}``, ``{{2}}``), so we
        convert named → numbered using ``placeholder_mapping``.

        Key Gupshup API requirements
        ─────────────────────────────
        * ``content`` — body text.  **Required for all types** including
          CAROUSEL (template-level body introducing the cards).
        * ``example`` — the FULL body text with placeholder values substituted
          (e.g. ``"Hi Jane, order #789 has shipped!"``).  **Required** even for
          templates with no placeholders (in which case it equals the body).
        * ``header`` — *only* for ``templateType=TEXT``.  For IMAGE / VIDEO /
          DOCUMENT the header is conveyed via ``exampleMedia``, not ``header``.
          **Not allowed** for CAROUSEL (each card has its own header).
        * ``footer`` — **Not allowed** for CAROUSEL templates.
        * ``buttons`` — template-level buttons.  **Not allowed** for CAROUSEL
          (each card has its own buttons).
        * ``exampleHeader`` — rendered header text (same rule as ``example``).
        * ``exampleMedia`` — media handle for IMAGE / VIDEO / DOCUMENT types.
        * ``enableSample=true`` — required for all template types.
        * ``allowTemplateCategoryChange=false`` — prevents META from
          auto-recategorising the template.
        """
        import re

        template_type_upper = (self.template_type or "").upper()
        is_card_based = template_type_upper in self._CARD_BASED_TYPES
        is_media_type = template_type_upper in self._MEDIA_TEMPLATE_TYPES

        body_text = self._convert_to_numbered_placeholders(self.content) if self.content else None
        header_text = self._convert_to_numbered_placeholders(self.header) if self.header else None

        # ── core fields (only non-None values) ───────────────────────────
        payload = {
            "elementName": self.element_name,
            "languageCode": self.language_code,
            "category": self.category,
            "templateType": self.template_type,
            "enableSample": True,
            "allowTemplateCategoryChange": False,
        }

        # ── content — REQUIRED for all types including CAROUSEL ──────────
        # CAROUSEL templates need a template-level body introducing the
        # cards (e.g. "Check out our latest products:").
        if body_text is not None:
            payload["content"] = body_text
        elif is_card_based:
            # Body text is required by Meta for CAROUSEL templates.
            # If missing, raise an error so the user fills it in.
            raise ValueError(
                "Body text is required for CAROUSEL templates. This text appears above the carousel cards."
            )

        # ── header — NOT allowed for CAROUSEL or LTO ─────────────────
        if header_text and not is_media_type and not is_card_based and not self.is_lto:
            payload["header"] = header_text

        # ── footer — NOT allowed for CAROUSEL or LTO ─────────────────
        if self.footer and not is_card_based and not self.is_lto:
            payload["footer"] = self.footer

        # ── buttons — template-level NOT allowed for CAROUSEL ────────────
        if self.buttons and not is_card_based:
            payload["buttons"] = self._convert_button_placeholders(self.buttons)

        # ── cards — CAROUSEL only ────────────────────────────────────────
        if self.cards:
            payload["cards"] = [self._convert_card_placeholders(c) for c in self.cards]

        if self.is_lto:
            payload["isLTO"] = True
            payload["hasExpiration"] = self.lto_has_expiration
            payload["limitedOfferText"] = self.lto_text or "Limited offer"

        # ── example (REQUIRED by Gupshup for every template) ─────────────
        # Build the FULL rendered body text.  If example_body provides
        # values, substitute them; otherwise use the body text as-is (which
        # is valid for templates without placeholders).
        effective_body = payload.get("content")
        if effective_body and effective_body.strip():
            if self.example_body and isinstance(self.example_body, list):
                example_text = effective_body
                for i, val in enumerate(self.example_body, start=1):
                    example_text = example_text.replace(f"{{{{{i}}}}}", str(val))
                payload["example"] = example_text
            else:
                # No example values — strip any remaining positional
                # placeholders so Gupshup still gets a valid example.
                payload["example"] = re.sub(r"\{\{\d+\}\}", "[sample]", effective_body)
        elif is_card_based:
            # CAROUSEL with empty body — example must still be present.
            payload["example"] = " "

        # ── exampleHeader (rendered header text, TEXT type only, NOT LTO) ─
        if header_text and not is_media_type and not is_card_based and not self.is_lto:
            if self.example_header and isinstance(self.example_header, list):
                example_header_text = header_text
                for i, val in enumerate(self.example_header, start=1):
                    example_header_text = example_header_text.replace(f"{{{{{i}}}}}", str(val))
                payload["exampleHeader"] = example_header_text
            else:
                payload["exampleHeader"] = re.sub(r"\{\{\d+\}\}", "[sample]", header_text)

        # ── exampleMedia (IMAGE / VIDEO / DOCUMENT) ──────────────────────
        if self.media_handle:
            payload["exampleMedia"] = self.media_handle

        payload["vertical"] = self.vertical or "OTHER"
        payload["message_send_ttl_seconds"] = getattr(self, "message_send_ttl_seconds", 43200) or 43200

        return payload

    def to_wati_payload(self) -> dict:
        """
        Convert to WATI API template creation payload.

        WATI accepts both named ({{name}}) and positional ({{1}}) placeholders.
        We pass the body as-is (named) and build customParams for example values.
        """
        payload = {
            "type": "template",
            "category": self.category,
            "elementName": self.element_name,
            "language": self.language_code,
            "body": self.content or "",
        }

        if self.footer:
            payload["footer"] = self.footer

        # Header
        if self.header or self.media_handle:
            header_obj = {}
            if self.template_type == TemplateType.TEXT:
                header_obj["format"] = "TEXT"
                header_obj["text"] = self.header or ""
            elif self.template_type in (TemplateType.IMAGE, TemplateType.VIDEO, TemplateType.DOCUMENT):
                header_obj["format"] = self.template_type
                if self.media_handle:
                    header_obj["example"] = self.media_handle
                if self.example_media_url:
                    header_obj["media_url"] = self.example_media_url
            payload["header"] = header_obj

        # Buttons
        if self.buttons:
            # Determine buttonsType from button types present
            btn_types = {btn.get("type", "").lower() for btn in self.buttons}
            has_qr = "quick_reply" in btn_types
            has_cta = btn_types & {"url", "phone_number", "copy_code", "flow"}
            if has_qr and has_cta:
                payload["buttonsType"] = "quick_reply_and_call_to_action"
            elif has_qr:
                payload["buttonsType"] = "quick_reply"
            elif has_cta:
                payload["buttonsType"] = "call_to_action"
            else:
                payload["buttonsType"] = "NONE"
            payload["buttons"] = self.buttons
        else:
            payload["buttonsType"] = "NONE"

        # Build customParams from example_body using placeholder_mapping
        import re

        named_params = re.findall(r"\{\{([^}]+)\}\}", self.content or "")
        unique_params = list(dict.fromkeys(p.strip() for p in named_params))
        if unique_params and self.example_body and isinstance(self.example_body, list):
            custom_params = []
            for i, name in enumerate(unique_params):
                value = self.example_body[i] if i < len(self.example_body) else name
                custom_params.append({"name": name, "value": str(value)})
            payload["customParams"] = custom_params

        return payload

    def _convert_to_numbered_placeholders(self, text: str) -> str:
        """Convert {{name}} {{order_id}} -> {{1}} {{2}}."""
        if not text:
            return text

        import re

        pattern = r"\{\{([^}]+)\}\}"
        matches = re.findall(pattern, text)

        if not matches:
            return text

        seen = {}
        mapping = {}
        result = text

        for placeholder in matches:
            if placeholder.strip().isdigit():
                continue
            if placeholder not in seen:
                num = len(seen) + 1
                seen[placeholder] = num
                mapping[str(num)] = placeholder.strip()

        for name, num in seen.items():
            result = result.replace(f"{{{{{name}}}}}", f"{{{{{num}}}}}")

        if not self.placeholder_mapping:
            self.placeholder_mapping = {}
        self.placeholder_mapping["content"] = mapping

        return result

    @staticmethod
    def _convert_named_to_positional(text: str) -> str:
        """
        Pure helper: convert {{name}} → {{1}}, {{order_id}} → {{2}}.

        Unlike ``_convert_to_numbered_placeholders`` this has **no side effects**
        (does not touch ``placeholder_mapping``).  Each call starts numbering
        from 1 independently — exactly what Gupshup requires for button URLs
        and card bodies.
        """
        if not text:
            return text
        import re

        pattern = r"\{\{([^}]+)\}\}"
        matches = re.findall(pattern, text)
        if not matches:
            return text
        seen: dict = {}
        result = text
        for placeholder in matches:
            if placeholder.strip().isdigit():
                continue
            if placeholder not in seen:
                seen[placeholder] = len(seen) + 1
        for name, num in seen.items():
            result = result.replace(f"{{{{{name}}}}}", f"{{{{{num}}}}}")
        return result

    def _convert_button_placeholders(self, buttons: list) -> list:
        """
        Convert named → positional placeholders in button URLs and their
        examples.  Each button URL gets independent numbering from {{1}}.
        """
        if not buttons:
            return buttons
        converted = []
        for btn in buttons:
            btn_copy = btn.copy()
            if btn_copy.get("type") == "URL" and btn_copy.get("url"):
                btn_copy["url"] = self._convert_named_to_positional(btn_copy["url"])
                # Also convert example URLs so they stay consistent
                if btn_copy.get("example") and isinstance(btn_copy["example"], list):
                    btn_copy["example"] = [
                        self._convert_named_to_positional(ex) if isinstance(ex, str) else ex
                        for ex in btn_copy["example"]
                    ]
            converted.append(btn_copy)
        return converted

    def _convert_card_placeholders(self, card: dict) -> dict:
        """
        Convert named → positional placeholders in a carousel card's body
        text and button URLs.  Each field is numbered independently from {{1}}.

        Also enriches the card dict with Gupshup-required fields that the
        canonical model does not store:
        - ``headerType`` — defaults to ``IMAGE`` if absent.
        - ``sampleText`` — rendered body text with positional placeholders
          replaced by ``[sample]`` markers (Gupshup requires this).

        Finally, strips ``None`` / internal-only keys so the serialised JSON
        sent to Gupshup contains only the fields it expects.
        """
        import re

        if not card:
            return card
        card_copy = card.copy()

        # Convert named → positional placeholders in body
        if card_copy.get("body") and isinstance(card_copy["body"], str):
            card_copy["body"] = self._convert_named_to_positional(card_copy["body"])

        # Convert named → positional placeholders in button URLs
        if card_copy.get("buttons") and isinstance(card_copy["buttons"], list):
            card_copy["buttons"] = self._convert_button_placeholders(card_copy["buttons"])

        # ── Gupshup enrichment ────────────────────────────────────────
        # headerType: detect from media_handle/exampleMedia, default to IMAGE
        if not card_copy.get("headerType"):
            # Detect video from media_handle / exampleMedia base64 content.
            # Gupshup encodes MIME type in the handle; 'dmlkZW8v' is base64
            # for 'video/' — present in video/mp4, video/3gpp, etc.
            handle = card_copy.get("media_handle") or card_copy.get("exampleMedia") or ""
            if "dmlkZW8v" in handle:
                card_copy["headerType"] = "VIDEO"
            else:
                card_copy["headerType"] = "IMAGE"

        # media_handle → exampleMedia (canonical field name → Gupshup field)
        if card_copy.get("media_handle") and not card_copy.get("exampleMedia"):
            card_copy["exampleMedia"] = card_copy.pop("media_handle")
        elif "media_handle" in card_copy:
            card_copy.pop("media_handle")

        # sampleText: rendered body with placeholders replaced
        if not card_copy.get("sampleText") and card_copy.get("body"):
            card_copy["sampleText"] = re.sub(r"\{\{\d+\}\}", "[sample]", card_copy["body"])

        # ── Strip None values & internal-only keys ────────────────────
        # Gupshup rejects null values in card JSON.  Also remove any
        # internal keys that leaked from the canonical model (e.g.
        # 'card_index', 'id', etc.).
        _GUPSHUP_CARD_KEYS = {
            "headerType",
            "mediaUrl",
            "mediaId",
            "exampleMedia",
            "body",
            "sampleText",
            "buttons",
        }
        card_copy = {k: v for k, v in card_copy.items() if v is not None and k in _GUPSHUP_CARD_KEYS}

        return card_copy

    def _extract_placeholder_mapping(self) -> dict:
        """
        Extract placeholder mappings from all text fields (content, header, buttons, cards).

        Scans for named placeholders like {{customer_name}} and builds a per-field
        mapping of numbered position → original name.

        Returns:
            dict: e.g. {
                "content": {"1": "customer_name", "2": "order_id"},
                "header":  {"1": "store_name"},
                "buttons": {"url_0": {"1": "tracking_id"}}
            }
        """
        import re

        pattern = r"\{\{([^}]+)\}\}"
        mapping = {}

        # Body (content)
        if self.content:
            matches = re.findall(pattern, self.content)
            seen = {}
            for m in matches:
                name = m.strip()
                if name.isdigit():
                    # Numbered placeholder like {{1}} — map position to itself
                    seen[name] = int(name)
                elif name not in seen:
                    seen[name] = len(seen) + 1
            if seen:
                mapping["content"] = {str(num): name for name, num in seen.items()}

        # Header (text headers can have variables)
        if self.header:
            matches = re.findall(pattern, self.header)
            seen = {}
            for m in matches:
                name = m.strip()
                if name.isdigit():
                    seen[name] = int(name)
                elif name not in seen:
                    seen[name] = len(seen) + 1
            if seen:
                mapping["header"] = {str(num): name for name, num in seen.items()}

        # Buttons (URL buttons can have dynamic {{params}})
        if self.buttons and isinstance(self.buttons, list):
            btn_mapping = {}
            for idx, btn in enumerate(self.buttons):
                url = btn.get("url", "") or ""
                matches = re.findall(pattern, url)
                seen = {}
                for m in matches:
                    name = m.strip()
                    if name.isdigit():
                        seen[name] = int(name)
                    elif name not in seen:
                        seen[name] = len(seen) + 1
                if seen:
                    btn_mapping[f"url_{idx}"] = {str(num): name for name, num in seen.items()}
            if btn_mapping:
                mapping["buttons"] = btn_mapping

        # Cards (carousel card bodies and button URLs)
        if self.cards and isinstance(self.cards, list):
            cards_mapping = {}
            for card_idx, card in enumerate(self.cards):
                card_map = {}
                # Card body
                card_body = card.get("body", "") or ""
                matches = re.findall(pattern, card_body)
                seen = {}
                for m in matches:
                    name = m.strip()
                    if name.isdigit():
                        seen[name] = int(name)
                    elif name not in seen:
                        seen[name] = len(seen) + 1
                if seen:
                    card_map["body"] = {str(num): name for name, num in seen.items()}
                # Card button URLs
                card_buttons = card.get("buttons", []) or []
                for btn_idx, btn in enumerate(card_buttons):
                    url = btn.get("url", "") or ""
                    matches = re.findall(pattern, url)
                    seen = {}
                    for m in matches:
                        name = m.strip()
                        if name.isdigit():
                            seen[name] = int(name)
                        elif name not in seen:
                            seen[name] = len(seen) + 1
                    if seen:
                        card_map[f"url_{btn_idx}"] = {str(num): name for name, num in seen.items()}
                if card_map:
                    cards_mapping[str(card_idx)] = card_map
            if cards_mapping:
                mapping["cards"] = cards_mapping

        return mapping

    def save(self, *args, **kwargs):
        """
        Override save to:
        1. Auto-create a TemplateNumber if this is a new template (or has none).
        2. Auto-populate placeholder_mapping from content fields.
        """
        # Auto-create TemplateNumber for new templates or templates missing one
        if not self.number:
            from message_templates.models import TemplateNumber

            self.number = TemplateNumber.objects.create(name=self.element_name or self.name or "")

        mapping = self._extract_placeholder_mapping()
        if mapping:
            self.placeholder_mapping = mapping
        elif self._state.adding:
            # New entry with no named placeholders — set empty dict instead of null
            self.placeholder_mapping = self.placeholder_mapping or {}

        super().save(*args, **kwargs)


class WAMessage(BaseTenantModelForFilterUser):
    """
    Canonical WhatsApp Message.

    Represents both inbound and outbound messages in a unified format.
    Frontend uses this for conversation view.
    """

    filter_by_user_tenant_fk = "wa_app__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Relationships
    wa_app = models.ForeignKey(WAApp, on_delete=models.CASCADE, related_name="messages")
    contact = models.ForeignKey(
        TenantContact, on_delete=models.CASCADE, related_name="wa_messages", blank=True, null=True
    )

    # Message identification
    wa_message_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    gs_message_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        help_text="Secondary message ID — stores the Gupshup UUID (messageId) "
        "when wa_message_id holds the Cloud API wamid, or vice-versa.",
    )
    direction = models.CharField(max_length=10, choices=MessageDirection.choices)
    message_type = models.CharField(max_length=20, choices=MessageType.choices, default=MessageType.TEXT)
    status = models.CharField(max_length=20, choices=MessageStatus.choices, default=MessageStatus.PENDING)

    # Text content
    text = models.TextField(blank=True, null=True)

    # Template content
    template = models.ForeignKey(WATemplate, on_delete=models.SET_NULL, blank=True, null=True, related_name="messages")
    template_params = models.JSONField(blank=True, null=True)

    # Media content
    media_id = models.CharField(
        max_length=255, blank=True, null=True, db_index=True, help_text="META media ID (from upload or webhook)"
    )
    media_url = models.URLField(
        max_length=2048, blank=True, null=True, help_text="Media download URL (expires after 5 min)"
    )
    media_mime_type = models.CharField(max_length=100, blank=True, null=True)
    media_sha256 = models.CharField(max_length=64, blank=True, null=True, help_text="SHA-256 hash of the media file")
    media_file_size = models.BigIntegerField(blank=True, null=True, help_text="File size in bytes")
    media_caption = models.TextField(blank=True, null=True)
    media_filename = models.CharField(max_length=255, blank=True, null=True)

    # Interactive response
    button_payload = models.CharField(max_length=255, blank=True, null=True)
    button_text = models.CharField(max_length=255, blank=True, null=True)

    # Location content
    latitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    location_name = models.CharField(max_length=255, blank=True, null=True)
    location_address = models.TextField(blank=True, null=True)

    # Error tracking
    error_code = models.CharField(max_length=50, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    # Status timestamps
    sent_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    read_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)

    # Cost tracking
    is_billable = models.BooleanField(default=True)
    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    conversation_type = models.CharField(max_length=50, blank=True, null=True)

    # Raw payload for debugging
    raw_payload = models.JSONField(blank=True, null=True)

    # Override name field - not needed for messages
    name = None

    class Meta:
        db_table = "wa_message_v2"
        verbose_name = "WA Message"
        verbose_name_plural = "WA Messages"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wa_app", "contact", "-created_at"]),
            models.Index(fields=["wa_message_id"]),
            models.Index(fields=["status", "direction"]),
        ]

    def __str__(self):
        return f"{self.direction} - {self.message_type} - {self.status}"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "wa_app_id": str(self.wa_app_id),
            "contact_id": str(self.contact_id) if self.contact_id else None,
            "wa_message_id": self.wa_message_id,
            "gs_message_id": self.gs_message_id,
            "direction": self.direction,
            "message_type": self.message_type,
            "status": self.status,
            "text": self.text,
            "template_id": str(self.template_id) if self.template_id else None,
            "template_params": self.template_params,
            "media_id": self.media_id,
            "media_url": self.media_url,
            "media_mime_type": self.media_mime_type,
            "media_sha256": self.media_sha256,
            "media_file_size": self.media_file_size,
            "media_caption": self.media_caption,
            "media_filename": self.media_filename,
            "button_payload": self.button_payload,
            "button_text": self.button_text,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WAWebhookEvent(BaseWebhookDumps):
    """
    Canonical WhatsApp Webhook Event.

    Extends BaseWebhookDumps which provides:
    - payload (JSONField)
    - received_at (auto timestamp)
    - is_processed, processed_at
    - error_message

    Stores raw webhook events from BSP for processing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Relationship
    wa_app = models.ForeignKey(WAApp, on_delete=models.CASCADE, related_name="webhook_events")

    # Event classification
    event_type = models.CharField(max_length=20, choices=WebhookEventType.choices)
    bsp = models.CharField(max_length=20, choices=BSPChoices.choices)

    # Retry tracking
    retry_count = models.IntegerField(default=0)

    # Linked entities (populated after processing)
    message = models.ForeignKey(
        WAMessage, on_delete=models.SET_NULL, blank=True, null=True, related_name="webhook_events"
    )

    class Meta:
        db_table = "wa_webhook_event_v2"
        verbose_name = "WA Webhook Event"
        verbose_name_plural = "WA Webhook Events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_processed", "event_type"]),
            models.Index(fields=["wa_app", "event_type", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} - {'Processed' if self.is_processed else 'Pending'}"


class WASubscription(BaseModel):
    """
    Canonical Webhook Subscription configuration.

    Tracks webhook subscriptions with BSPs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Relationship
    wa_app = models.ForeignKey(WAApp, on_delete=models.CASCADE, related_name="subscriptions")

    # Subscription config
    webhook_url = models.URLField()
    event_types = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=SubscriptionStatus.choices, default=SubscriptionStatus.PENDING)

    # BSP tracking
    bsp_subscription_id = models.CharField(max_length=255, blank=True, null=True)
    verify_token = models.CharField(max_length=255, blank=True, null=True)

    # Error tracking
    error_message = models.TextField(blank=True, null=True)
    last_event_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "wa_subscription_v2"
        verbose_name = "WA Subscription"
        verbose_name_plural = "WA Subscriptions"

    def __str__(self):
        return f"{self.wa_app.name} - {self.status}"


class WAMedia(BaseTenantModelForFilterUser):
    """
    Canonical WhatsApp Media Asset.

    Tracks media files uploaded to META for use in templates and messages.

    Media IDs from uploads expire after 30 days.
    Media IDs from webhooks expire after 7 days.
    Media URLs expire after 5 minutes.

    Use cases:
      - Template header media (IMAGE, VIDEO, DOCUMENT)
      - Outgoing media messages
      - Caching incoming media info from webhooks
    """

    filter_by_user_tenant_fk = "wa_app__tenant__tenant_users__user"

    class MediaSource(models.TextChoices):
        """How this media was acquired."""

        UPLOAD = "UPLOAD", "Uploaded via API"
        WEBHOOK = "WEBHOOK", "Received via webhook"

    class MediaCategory(models.TextChoices):
        """WhatsApp media categories."""

        AUDIO = "AUDIO", "Audio"
        DOCUMENT = "DOCUMENT", "Document"
        IMAGE = "IMAGE", "Image"
        STICKER = "STICKER", "Sticker"
        VIDEO = "VIDEO", "Video"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Relationships
    wa_app = models.ForeignKey(WAApp, on_delete=models.CASCADE, related_name="media_assets")

    # META identifiers
    meta_media_id = models.CharField(
        max_length=255, db_index=True, unique=True, help_text="Media ID returned by META upload or received in webhook"
    )
    phone_number_id = models.CharField(
        max_length=100, blank=True, null=True, help_text="Phone Number ID that uploaded/received this media"
    )

    # Media metadata
    mime_type = models.CharField(max_length=150, help_text="MIME type (e.g., image/jpeg, video/mp4)")
    category = models.CharField(max_length=20, choices=MediaCategory.choices, help_text="WhatsApp media category")
    file_size = models.BigIntegerField(blank=True, null=True, help_text="File size in bytes")
    sha256 = models.CharField(max_length=64, blank=True, null=True, help_text="SHA-256 hash of the file")
    filename = models.CharField(max_length=255, blank=True, null=True, help_text="Original filename")

    # Cached download URL (expires after 5 minutes)
    cached_url = models.URLField(blank=True, null=True, help_text="Cached media URL (valid ~5 min)")
    url_fetched_at = models.DateTimeField(blank=True, null=True, help_text="When the cached URL was last fetched")

    # Source & lifecycle
    source = models.CharField(max_length=10, choices=MediaSource.choices, default=MediaSource.UPLOAD)
    expires_at = models.DateTimeField(
        blank=True, null=True, help_text="When the media ID expires (30 days for upload, 7 days for webhook)"
    )
    is_expired = models.BooleanField(default=False, db_index=True)

    # Usage tracking
    used_in_template = models.ForeignKey(
        WATemplate,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="media_assets",
        help_text="Template this media is used as header handle",
    )
    used_in_message = models.ForeignKey(
        WAMessage,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="media_assets",
        help_text="Message this media is attached to",
    )

    # Override name — not applicable for media
    name = None

    class Meta:
        db_table = "wa_media_v2"
        verbose_name = "WA Media"
        verbose_name_plural = "WA Media"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wa_app", "category", "-created_at"]),
            models.Index(fields=["source", "is_expired"]),
        ]

    def __str__(self):
        return f"{self.category} ({self.mime_type}) — {self.meta_media_id[:20]}..."

    @property
    def is_url_expired(self) -> bool:
        """Check if the cached download URL has expired (5 min window)."""
        from datetime import timedelta

        from django.utils import timezone

        if not self.url_fetched_at:
            return True
        return timezone.now() > self.url_fetched_at + timedelta(minutes=5)

    @property
    def is_media_id_expired(self) -> bool:
        """Check if the META media ID itself has expired."""
        from django.utils import timezone

        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    def set_expiry_from_source(self):
        """
        Set expires_at based on source type.
        Upload = 30 days, Webhook = 7 days.
        """
        from datetime import timedelta

        from django.utils import timezone

        if self.source == self.MediaSource.UPLOAD:
            self.expires_at = timezone.now() + timedelta(days=30)
        elif self.source == self.MediaSource.WEBHOOK:
            self.expires_at = timezone.now() + timedelta(days=7)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "wa_app_id": str(self.wa_app_id),
            "meta_media_id": self.meta_media_id,
            "mime_type": self.mime_type,
            "category": self.category,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "filename": self.filename,
            "source": self.source,
            "is_expired": self.is_expired,
            "is_url_expired": self.is_url_expired,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class WAContacts(TenantContact):
    """
    Stores contact information for WhatsApp integration.
    """

    class Meta:
        verbose_name = "WA Contact"
        verbose_name_plural = "WA Contacts"
        proxy = True

    objects: WAContactsManager = WAContactsManager()


class WABroadcast(Broadcast):
    """
    Stores broadcast information for WhatsApp integration.
    """

    class Meta:
        verbose_name = "WA Broadcast"
        verbose_name_plural = "WA Broadcasts"
        proxy = True

    objects: WABroadcastManager = WABroadcastManager()


# =============================================================================
# RATE CARD MODELS (Issue #188)
# =============================================================================


class MessageTypeChoices(models.TextChoices):
    """WhatsApp message type categories for rate card pricing."""

    MARKETING = "MARKETING", "Marketing"
    UTILITY = "UTILITY", "Utility"
    AUTHENTICATION = "AUTHENTICATION", "Authentication"


class MetaBaseRate(BaseModel):
    """
    Raw Meta-provided per-country WhatsApp messaging rates.

    These are the wholesale rates published by Meta on a monthly cycle.
    Stored in USD (Meta's billing currency). Admin-managed via CSV import
    or Django admin.

    Example:
        IN / MARKETING / $0.0099 / effective 2026-02-01
        US / MARKETING / $0.0250 / effective 2026-02-01
    """

    destination_country = models.CharField(
        max_length=2,
        help_text="ISO 3166-1 alpha-2 country code (e.g. IN, US, BR)",
        db_index=True,
    )
    message_type = models.CharField(
        max_length=20,
        choices=MessageTypeChoices.choices,
        db_index=True,
    )
    rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="Meta base rate in USD",
    )
    effective_from = models.DateField(
        help_text="Start of the Meta pricing window (monthly)",
    )
    effective_to = models.DateField(
        null=True,
        blank=True,
        help_text="End of the pricing window. NULL = currently active.",
    )
    is_current = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this is the active rate for this country/type combo",
    )

    class Meta:
        verbose_name = "Meta Base Rate"
        verbose_name_plural = "Meta Base Rates"
        constraints = [
            models.UniqueConstraint(
                fields=["destination_country", "message_type", "effective_from"],
                name="uq_meta_rate_country_type_effective",
            ),
        ]
        indexes = [
            models.Index(
                fields=["destination_country", "message_type", "is_current"],
                name="idx_meta_rate_current_lookup",
            ),
        ]
        ordering = ["destination_country", "message_type", "-effective_from"]

    def __str__(self):
        return f"{self.destination_country}/{self.message_type} ${self.rate} (from {self.effective_from})"


class RateCardMargin(BaseModel):
    """
    Margin / markup configuration with a fallback hierarchy.

    Margins are resolved from most-specific to least-specific:
        1. tenant + country + message_type   (most specific)
        2. tenant + country
        3. tenant + message_type
        4. tenant                            (tenant-wide)
        5. country + message_type
        6. country
        7. message_type
        8. global default                    (all NULL)

    A single global row with all NULLs serves as the system-wide default.
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="rate_card_margins",
        help_text="NULL = applies globally",
    )
    destination_country = models.CharField(
        max_length=2,
        null=True,
        blank=True,
        help_text="NULL = applies to all countries",
    )
    message_type = models.CharField(
        max_length=20,
        choices=MessageTypeChoices.choices,
        null=True,
        blank=True,
        help_text="NULL = applies to all message types",
    )
    margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text="Margin percentage applied on top of (base_rate × FX). e.g. 15.00 = 15%",
    )

    class Meta:
        verbose_name = "Rate Card Margin"
        verbose_name_plural = "Rate Card Margins"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "destination_country", "message_type"],
                name="uq_margin_tenant_country_type",
            ),
        ]
        ordering = ["tenant", "destination_country", "message_type"]

    def __str__(self):
        parts = []
        if self.tenant:
            parts.append(f"tenant={self.tenant_id}")
        if self.destination_country:
            parts.append(f"country={self.destination_country}")
        if self.message_type:
            parts.append(f"type={self.message_type}")
        scope = ", ".join(parts) if parts else "GLOBAL"
        return f"Margin {self.margin_percent}% ({scope})"

    @property
    def specificity(self) -> int:
        """
        Compute a specificity score for fallback ordering.
        Higher = more specific = higher priority.
        """
        score = 0
        if self.tenant_id is not None:
            score += 4
        if self.destination_country is not None:
            score += 2
        if self.message_type is not None:
            score += 1
        return score


class TenantRateCard(BaseModel):
    """
    Pre-computed, tenant-facing WhatsApp rate card.

    Each row is a single rate visible to the tenant for a specific
    destination_country × message_type combination, expressed in the
    tenant's wallet currency. Rates are monthly-fixed and include
    Meta base rate + FX conversion + margin.

    These rates are **reference only** — actual billing at send time
    recalculates using live FX + the active Meta rate.

    The ``previous_rate`` field enables the "Recent Changes" view
    without needing a separate history table.
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="rate_cards",
    )
    destination_country = models.CharField(
        max_length=2,
        db_index=True,
        help_text="ISO 3166-1 alpha-2 country code",
    )
    message_type = models.CharField(
        max_length=20,
        choices=MessageTypeChoices.choices,
        db_index=True,
    )

    # Pricing breakdown (transparency for admins / debugging)
    meta_base_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="Meta base rate in USD at generation time",
    )
    fx_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="USD → wallet-currency exchange rate used",
    )
    margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text="Margin % applied",
    )

    # Final tenant-facing rate (in wallet currency)
    reference_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="Final rate shown to tenant (wallet currency)",
    )
    wallet_currency = models.CharField(
        max_length=3,
        default="USD",
        help_text="Tenant wallet currency code (ISO 4217)",
    )

    # Period tracking
    effective_from = models.DateField(
        help_text="Start of the rate period (monthly)",
    )
    last_updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this row was last recomputed",
    )

    # Recent-changes support
    previous_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Previous period reference_rate for change detection",
    )

    # Custom override
    is_custom = models.BooleanField(
        default=False,
        help_text="True if this rate was manually set, bypassing formula",
    )

    class Meta:
        verbose_name = "Tenant Rate Card"
        verbose_name_plural = "Tenant Rate Cards"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "destination_country", "message_type", "effective_from"],
                name="uq_tenant_rate_card_entry",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "effective_from"],
                name="idx_rate_card_tenant_period",
            ),
            models.Index(
                fields=["tenant", "destination_country", "message_type"],
                name="idx_rate_card_lookup",
            ),
        ]
        ordering = ["tenant", "destination_country", "message_type"]

    def __str__(self):
        return (
            f"{self.tenant_id}/{self.destination_country}/{self.message_type} "
            f"= {self.reference_rate} {self.wallet_currency}"
        )

    @property
    def rate_changed(self) -> bool:
        """True if reference_rate differs from previous_rate."""
        if self.previous_rate is None:
            return True  # new entry = "changed"
        return self.reference_rate != self.previous_rate

    @property
    def rate_change_percent(self):
        """Percentage change from previous rate, or None."""
        if self.previous_rate is None or self.previous_rate == 0:
            return None
        from decimal import Decimal

        return ((self.reference_rate - self.previous_rate) / self.previous_rate * Decimal("100")).quantize(
            Decimal("0.01")
        )


# =============================================================================
# ORDER & PAYMENT LIFECYCLE
# =============================================================================


class OrderType(models.TextChoices):
    DIGITAL_GOODS = "digital-goods", "Digital Goods"
    PHYSICAL_GOODS = "physical-goods", "Physical Goods"


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    PARTIALLY_SHIPPED = "partially_shipped", "Partially Shipped"
    SHIPPED = "shipped", "Shipped"
    COMPLETED = "completed", "Completed"
    CANCELED = "canceled", "Canceled"


class PaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PENDING = "pending", "Pending"
    CAPTURED = "captured", "Captured"
    FAILED = "failed", "Failed"
    REFUND_PENDING = "refund_pending", "Refund Pending"
    REFUNDED = "refunded", "Refunded"


# Payment status priority — higher number = more authoritative (don't downgrade)
PAYMENT_STATUS_PRIORITY = {
    PaymentStatus.UNPAID: 0,
    PaymentStatus.PENDING: 1,
    PaymentStatus.FAILED: 2,
    PaymentStatus.CAPTURED: 3,
    PaymentStatus.REFUND_PENDING: 4,
    PaymentStatus.REFUNDED: 5,
}

# Allowed order status transitions
ALLOWED_TRANSITIONS = {
    "pending": ["processing", "canceled"],
    "processing": ["partially_shipped", "shipped", "completed", "canceled"],
    "partially_shipped": ["shipped", "completed", "canceled"],
    "shipped": ["completed"],
    "completed": [],
    "canceled": [],
}


class WAOrder(BaseTenantModelForFilterUser):
    """
    Tracks a WhatsApp order through its full lifecycle:
    order_details sent → payment captured → order_status updates → refund.
    """

    filter_by_user_tenant_fk = "wa_app__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ── Relationships ──────────────────────────────────────────────────
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="wa_orders",
    )
    wa_app = models.ForeignKey(
        WAApp,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    contact = models.ForeignKey(
        TenantContact,
        on_delete=models.CASCADE,
        related_name="wa_orders",
        blank=True,
        null=True,
    )
    outgoing_message = models.ForeignKey(
        "WAMessage",
        on_delete=models.SET_NULL,
        related_name="order_from_message",
        blank=True,
        null=True,
        help_text="The order_details WAMessage that created this order.",
    )
    order_status_messages = models.ManyToManyField(
        "WAMessage",
        related_name="order_status_updates",
        blank=True,
        help_text="All order_status WAMessages sent for this order.",
    )

    # ── Order Identification ───────────────────────────────────────────
    reference_id = models.CharField(
        max_length=35,
        db_index=True,
        help_text="Unique per tenant. Alphanumeric + _ . -",
    )
    order_type = models.CharField(
        max_length=20,
        choices=OrderType.choices,
        default=OrderType.PHYSICAL_GOODS,
    )
    currency = models.CharField(max_length=3, default="INR")

    # ── Financial (all values in paisa — offset=100) ───────────────────
    total_amount = models.PositiveBigIntegerField(default=0)
    subtotal = models.PositiveBigIntegerField(default=0)
    tax = models.PositiveBigIntegerField(default=0)
    shipping = models.PositiveBigIntegerField(default=0)
    discount = models.PositiveBigIntegerField(default=0)

    # ── Items Snapshot ─────────────────────────────────────────────────
    items = models.JSONField(default=list, blank=True)

    # ── Payment Gateway ────────────────────────────────────────────────
    payment_gateway = models.CharField(max_length=20, blank=True, default="")
    configuration_name = models.CharField(max_length=60, blank=True, default="")

    # ── Status ─────────────────────────────────────────────────────────
    order_status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )

    # ── Transaction ────────────────────────────────────────────────────
    transaction_id = models.CharField(max_length=255, blank=True, null=True)
    pg_transaction_id = models.CharField(max_length=255, blank=True, null=True)
    payment_method = models.CharField(max_length=50, blank=True, null=True)

    # ── Full Payload Snapshot ──────────────────────────────────────────
    order_details_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full order_details action.parameters snapshot.",
    )

    # ── Timestamps ─────────────────────────────────────────────────────
    payment_captured_at = models.DateTimeField(blank=True, null=True)
    refunded_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "wa_order"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "reference_id"],
                name="uq_wa_order_tenant_reference_id",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "payment_status"], name="idx_wa_order_payment"),
            models.Index(fields=["tenant", "order_status"], name="idx_wa_order_status"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.reference_id} ({self.order_status}/{self.payment_status})"

    def can_transition_to(self, new_status: str) -> bool:
        """Check if the order can transition to the given status."""
        return new_status in ALLOWED_TRANSITIONS.get(self.order_status, [])

    def can_cancel(self) -> bool:
        """Cannot cancel if payment is already captured (must refund first)."""
        return self.payment_status not in (
            PaymentStatus.CAPTURED,
            PaymentStatus.REFUND_PENDING,
        )


class WAPaymentEvent(BaseModel):
    """
    Immutable audit trail for every payment-related webhook event.
    One WAOrder may have many events (e.g., pending → captured → refunded).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    order = models.ForeignKey(
        WAOrder,
        on_delete=models.CASCADE,
        related_name="payment_events",
    )
    webhook_event = models.ForeignKey(
        "WAWebhookEvent",
        on_delete=models.SET_NULL,
        related_name="payment_events",
        blank=True,
        null=True,
    )

    status = models.CharField(max_length=30)
    transaction_id = models.CharField(max_length=255, blank=True, null=True)
    pg_transaction_id = models.CharField(max_length=255, blank=True, null=True)
    transaction_status = models.CharField(max_length=30, blank=True, null=True)
    amount_value = models.PositiveBigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="INR")
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "wa_payment_event"
        ordering = ["-created_at"]

    def __str__(self):
        return f"PaymentEvent {self.status} for {self.order_id}"
