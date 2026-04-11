"""
Abstract broadcast models that can be extended by platform-specific apps (WhatsApp, Telegram, etc.)
"""

import logging

from abstract.models import BaseTenantModelForFilterUser

logger = logging.getLogger(__name__)
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from djmoney.models.fields import MoneyField
from moneyed import Decimal
from simple_history.models import HistoricalRecords

from contacts.models import TenantContact
from message_templates.models import TemplateNumber
from tenants.models import Tenant

User = get_user_model()


class BroadcastStatusChoices(models.TextChoices):
    """Common broadcast status choices for all platforms"""

    DRAFT = "DRAFT", "Draft"  # do not send messages, I am still working on it
    SCHEDULED = "SCHEDULED", "Scheduled"  # ready to be sent at scheduled_time
    QUEUED = "QUEUED", "Queued"  # if broadcast is yet to be tasked. Once tasked, it is marked as scheduled.
    SENDING = "SENDING", "Sending"  # currently being sent
    SENT = "SENT", "Sent"  # all messages sent successfully
    PARTIALLY_SENT = "PARTIALLY_SENT", "Partially Sent"  # some messages sent successfully, others failed
    FAILED = "FAILED", "Failed"  # all messages failed
    CANCELLED = "CANCELLED", "Cancelled"  # broadcast cancelled by user


class BroadcastUIStatusChoices(models.TextChoices):
    """UI-friendly status choices for display purposes"""

    DRAFT = "DRAFT", "Draft"
    SCHEDULED = "SCHEDULED", "Scheduled"
    ONGOING = "ONGOING", "Ongoing"  # Maps from SENDING
    COMPLETED = "COMPLETED", "Completed"  # Maps from SENT, PARTIALLY_SENT
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


# Mapping from internal status to UI status
BROADCAST_STATUS_TO_UI_STATUS = {
    BroadcastStatusChoices.DRAFT: BroadcastUIStatusChoices.DRAFT,
    BroadcastStatusChoices.SCHEDULED: BroadcastUIStatusChoices.SCHEDULED,
    BroadcastStatusChoices.QUEUED: BroadcastUIStatusChoices.SCHEDULED,  # QUEUED shows as Scheduled in UI
    BroadcastStatusChoices.SENDING: BroadcastUIStatusChoices.ONGOING,
    BroadcastStatusChoices.SENT: BroadcastUIStatusChoices.COMPLETED,
    BroadcastStatusChoices.PARTIALLY_SENT: BroadcastUIStatusChoices.COMPLETED,
    BroadcastStatusChoices.FAILED: BroadcastUIStatusChoices.FAILED,
    BroadcastStatusChoices.CANCELLED: BroadcastUIStatusChoices.CANCELLED,
}

# Reverse mapping: UI status to list of internal statuses
UI_STATUS_TO_BROADCAST_STATUSES = {
    BroadcastUIStatusChoices.DRAFT: [BroadcastStatusChoices.DRAFT],
    BroadcastUIStatusChoices.SCHEDULED: [BroadcastStatusChoices.SCHEDULED, BroadcastStatusChoices.QUEUED],
    BroadcastUIStatusChoices.ONGOING: [BroadcastStatusChoices.SENDING],
    BroadcastUIStatusChoices.COMPLETED: [BroadcastStatusChoices.SENT, BroadcastStatusChoices.PARTIALLY_SENT],
    BroadcastUIStatusChoices.FAILED: [BroadcastStatusChoices.FAILED],
    BroadcastUIStatusChoices.CANCELLED: [BroadcastStatusChoices.CANCELLED],
}


class MessageStatusChoices(models.TextChoices):
    """Common message delivery status choices"""

    PENDING = "PENDING", "Pending"  # Added for chunked processing
    QUEUED = "QUEUED", "Queued"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent Successfully"
    DELIVERED = "DELIVERED", "Delivered"
    READ = "READ", "Read"
    FAILED = "FAILED", "Failed"
    BLOCKED = "BLOCKED", "Blocked"


class BroadcastPlatformChoices(models.TextChoices):
    WHATSAPP = "WHATSAPP", "WhatsApp"
    TELEGRAM = "TELEGRAM", "Telegram"
    SMS = "SMS", "SMS"


class Broadcast(BaseTenantModelForFilterUser):
    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    # Reserved variables available for all broadcast messages
    RESERVED_VARS = [
        {"key": "first_name", "description": "First name of the contact"},
        {"key": "last_name", "description": "Last name of the contact"},
        {"key": "full_name", "description": "Full name of the contact"},
        {"key": "contact_name", "description": 'Contact name (full name or "Customer" if empty)'},
        {"key": "phone", "description": "Phone number of the contact"},
        {"key": "email", "description": "Email address of the contact"},
        {"key": "company_name", "description": "Name of the tenant/company"},
        {"key": "tenant_name", "description": "Name of the tenant (alias for company_name)"},
        {"key": "today_date", "description": 'Current date in format "November 05, 2025"'},
        {"key": "current_time", "description": 'Current time in format "02:30 PM"'},
        {"key": "current_year", "description": "Current year as string"},
        {"key": "broadcast_name", "description": "Name of the broadcast campaign"},
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="broadcasts")
    name = models.CharField(max_length=255, help_text="Broadcast campaign name")
    recipients = models.ManyToManyField(TenantContact, related_name="broadcasts")

    scheduled_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=BroadcastStatusChoices.choices, default=BroadcastStatusChoices.DRAFT
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="created_broadcasts")
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="updated_broadcasts")
    platform = models.CharField(
        max_length=20, choices=BroadcastPlatformChoices.choices, default=BroadcastPlatformChoices.WHATSAPP
    )
    task_id = models.CharField(max_length=255, blank=True, null=True, editable=False)
    template_number = models.ForeignKey(TemplateNumber, on_delete=models.CASCADE, null=True, blank=True)
    placeholder_data = models.JSONField(default=dict, blank=True)
    media_overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Override template media at send time. "
            'Format: {"header": <TenantMedia id>} for single-media templates, '
            '{"cards": {"0": <TenantMedia id>, "1": <TenantMedia id>, ...}} for carousels.'
        ),
    )
    reason_for_cancellation = models.TextField(blank=True, null=True)

    # Credit management fields
    credit_deducted = models.BooleanField(
        default=False, help_text="Whether credits have been deducted for this broadcast"
    )
    refund_processed = models.BooleanField(
        default=False, help_text="Whether refund has been processed for failed messages"
    )
    initial_cost = MoneyField(
        max_digits=10,
        decimal_places=2,
        default_currency="USD",
        null=True,
        blank=True,
        help_text="Initial cost deducted for the broadcast",
    )
    refund_amount = MoneyField(
        max_digits=10,
        decimal_places=2,
        default_currency="USD",
        null=True,
        blank=True,
        help_text="Amount refunded for failed messages",
    )

    # History tracking
    history = HistoricalRecords()

    @property
    def is_marketing_broadcast(self):
        """Determine if the broadcast is marketing type based on template number"""
        if self.template_number and self.template_number.gupshup_template:
            template = self.template_number.gupshup_template
            from wa.models import TemplateCategory

            return template.category == TemplateCategory.MARKETING
        return False

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    @classmethod
    def get_reserved_keywords(cls):
        """
        Get all available reserved keywords/variables for broadcast templates.
        Returns cls.RESERVED_VARS — the single source of truth.
        """
        return cls.RESERVED_VARS

    @property
    def green_signal_stages(self):
        """
        Define the stages where the broadcast is considered to have a green signal
        for proceeding to the next step.
        """
        return [BroadcastStatusChoices.SCHEDULED, BroadcastStatusChoices.QUEUED]

    @property
    def allowed_status_for_update(self):
        """
        Define the allowed statuses for updating the broadcast.
        Broadcasts in these statuses can be updated.
        """
        return [
            BroadcastStatusChoices.DRAFT,
            BroadcastStatusChoices.SCHEDULED,
            BroadcastStatusChoices.QUEUED,
            BroadcastStatusChoices.CANCELLED,
        ]

    @property
    def allowed_status_for_creation(self):
        """
        Define the allowed statuses for creating the broadcast.
        Only broadcasts in these statuses can be created.
        """
        return [BroadcastStatusChoices.DRAFT, BroadcastStatusChoices.QUEUED]

    @property
    def good_to_send(self):
        """
        Check if the broadcast is ready to be sent
        Conditions:
        - Status must be SCHEDULED or QUEUED
        - Must have at least one recipient
        - Must have a template number assigned
        """
        if self.status not in self.green_signal_stages:
            return False
        if self.pk is None:
            return False
        if not self.recipients.exists():
            return False
        if not self.template_number:
            return False
        return True

    @property
    def in_the_past(self):
        """Check if the scheduled_time is in the past"""
        if self.scheduled_time:
            return self.scheduled_time < timezone.now()
        return False

    @property
    def ui_status(self):
        """
        Get UI-friendly status for display purposes.

        Mapping:
        - DRAFT -> Draft
        - SCHEDULED, QUEUED -> Scheduled
        - SENDING -> Ongoing
        - SENT, PARTIALLY_SENT -> Completed
        - FAILED -> Failed
        - CANCELLED -> Cancelled
        """
        return BROADCAST_STATUS_TO_UI_STATUS.get(self.status, self.status)

    @property
    def ui_status_display(self):
        """Get the human-readable label for ui_status"""
        ui_status = self.ui_status
        if hasattr(ui_status, "label"):
            return ui_status.label
        return str(ui_status)

    @property
    def threshold_time_from_now(self):
        """Get the threshold time (5 minutes from now) for scheduling validation"""
        from datetime import timedelta

        from django.utils import timezone

        return timezone.now() + timedelta(minutes=settings.BROADCAST_CANCELLATION_TIME_LIMIT_IN_MINUTES)

    @property
    def _default_scheduled_time(self):
        """Helper to get scheduled_time or current time if not set"""
        return self.threshold_time_from_now + timezone.timedelta(seconds=5)

    def assign_now(self):
        """
        Assign scheduled_time to threshold time from now if good_to_send and scheduled_time is None
        """
        if self.good_to_send and self.scheduled_time is None:
            self.scheduled_time = self._default_scheduled_time

    def get_message_price(self):
        """
        Get the price per message based on platform and template category.
        Routes to platform-specific pricing methods.
        """
        if self.platform == BroadcastPlatformChoices.WHATSAPP:
            return self._get_whatsapp_message_price()
        elif self.platform == BroadcastPlatformChoices.SMS:
            return self._get_sms_message_price()
        elif self.platform == BroadcastPlatformChoices.TELEGRAM:
            return self._get_telegram_message_price()
        else:
            return Decimal("0")

    def _get_whatsapp_message_price(self) -> Decimal:
        """
        Get WhatsApp message price based on template category.
        """
        if not self.template_number or not self.template_number.gupshup_template:
            return Decimal("0")

        template = self.template_number.gupshup_template
        wa_app = template.wa_app

        from wa.models import TemplateCategory

        # Map category to price field (Money objects)
        category_price_map = {
            TemplateCategory.AUTHENTICATION: wa_app.authentication_message_price.amount,
            TemplateCategory.MARKETING: wa_app.marketing_message_price.amount,
            TemplateCategory.UTILITY: wa_app.utility_message_price.amount,
        }

        price = category_price_map.get(template.category, None)
        if price is None:
            return Decimal("0")
        return price

    def _get_sms_message_price(self):
        """
        Get SMS message price.
        TODO: Implement SMS pricing logic when SMS support is added.
        """
        # Placeholder for future SMS pricing
        # Example: return Decimal(self.tenant.sms_price_per_message)
        return Decimal("0")

    def _get_telegram_message_price(self):
        """
        Get Telegram message price.
        TODO: Implement Telegram pricing logic when Telegram support is added.
        """
        # Placeholder for future Telegram pricing
        # Example: return Decimal(self.tenant.telegram_price_per_message)
        return Decimal("0")

    def calculate_initial_cost(self):
        """
        Calculate the initial cost based on total recipients and price per message.
        Works for all platforms (WhatsApp, SMS, Telegram).

        For WhatsApp, attempts per-country pricing via RateCardService first,
        then falls back to the flat wa_app rate.
        """
        if self.pk is None:
            return 0

        recipient_count = self.recipients.count()
        if recipient_count == 0:
            return 0

        # Try per-country pricing for WhatsApp
        if self.platform == BroadcastPlatformChoices.WHATSAPP:
            per_country_cost = self._calculate_whatsapp_cost_per_country()
            if per_country_cost is not None:
                return per_country_cost

        # Fallback: flat rate
        price_per_message = self.get_message_price()
        if price_per_message == 0:
            return 0

        return recipient_count * price_per_message

    def _calculate_whatsapp_cost_per_country(self):
        """
        Calculate WhatsApp broadcast cost using per-country rates.

        Groups recipients by destination country (derived from phone number),
        looks up the send-time rate for each country via RateCardService,
        and sums up the costs.

        Returns:
            Decimal total cost, or None if rate-card data is unavailable.
        """
        from decimal import Decimal

        import phonenumbers

        try:
            from wa.models import MetaBaseRate
            from wa.services.rate_card_service import RateCardService
        except ImportError:
            return None

        # Quick check: are there any base rates at all?
        if not MetaBaseRate.objects.filter(is_current=True).exists():
            return None

        # Determine message type from template
        if not self.template_number or not self.template_number.gupshup_template:
            return None
        template = self.template_number.gupshup_template
        message_type = template.category  # MARKETING / UTILITY / AUTHENTICATION

        tenant = self.tenant
        svc = RateCardService(tenant)

        # Group recipients by country
        country_counts = {}  # {country_code: count}
        phones = self.recipients.values_list("phone", flat=True)

        for phone_str in phones.iterator():
            try:
                parsed = phonenumbers.parse(str(phone_str))
                country = phonenumbers.region_code_for_number(parsed)  # e.g. "IN"
                if country:
                    country_counts[country] = country_counts.get(country, 0) + 1
                else:
                    country_counts["__unknown__"] = country_counts.get("__unknown__", 0) + 1
            except phonenumbers.NumberParseException:
                country_counts["__unknown__"] = country_counts.get("__unknown__", 0) + 1

        # Calculate cost per country
        total_cost = Decimal("0")
        flat_price = self._get_whatsapp_message_price()  # fallback per-msg

        for country, count in country_counts.items():
            if country == "__unknown__":
                # Unknown country → use flat rate
                total_cost += Decimal(str(flat_price)) * count
            else:
                rate = svc.get_send_time_rate(country, message_type)
                if rate is not None:
                    total_cost += rate * count
                else:
                    # No rate card entry → flat rate fallback
                    total_cost += Decimal(str(flat_price)) * count

        return total_cost

    def get_failed_message_count(self):
        """
        Get the count of failed messages (FAILED + BLOCKED).
        """
        if hasattr(self, "broadcasts"):
            if not self.broadcasts.exists():
                return self.recipients.count()
            return self.broadcasts.filter(
                status__in=[MessageStatusChoices.FAILED, MessageStatusChoices.BLOCKED]
            ).count()
        else:
            # we cancelled before sending any messages
            return self.recipients.count()

    def calculate_refund_amount(self) -> Decimal:
        """
        Calculate refund amount based on failed messages.
        """
        failed_count = self.get_failed_message_count()
        price_per_message = self.get_message_price()

        return failed_count * price_per_message

    def should_apply_credit_deduction(self):
        """
        Check if credit deduction should be applied for this broadcast.

        This method can be used to:
        - Disable credits for demo/test broadcasts
        - Implement free broadcasts for specific scenarios
        - Add tenant-specific pricing rules

        Currently applies to all WhatsApp templates with valid pricing.
        Returns False for platforms without pricing configured (SMS, Telegram).
        """
        # Check if this is a demo/test broadcast (future enhancement)
        # if hasattr(self, 'is_demo') and self.is_demo:
        #     return False

        # Check if tenant has special pricing rules (future enhancement)
        # if self.tenant.has_free_broadcasts:
        #     return False

        # Standard logic: apply credits if there's a price configured
        price = self.get_message_price()
        return price > 0.0

    def save(self, *args, **kwargs):
        self.assign_now()
        if self.status in self.green_signal_stages:
            self.reason_for_cancellation = None
        super().save(*args, **kwargs)


class BroadcastMessage(BaseTenantModelForFilterUser):
    filter_by_user_tenant_fk = "broadcast__tenant__tenant_users__user"
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name="broadcasts")
    contact = models.ForeignKey(TenantContact, on_delete=models.CASCADE, related_name="contacts")
    status = models.CharField(max_length=20, choices=MessageStatusChoices.choices, default=MessageStatusChoices.PENDING)
    response = models.TextField(blank=True, null=True)  # store raw API response or error message
    retry_count = models.PositiveIntegerField(default=0)
    webhook_response = models.JSONField(blank=True, null=True)
    message_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    task_id = models.CharField(max_length=255, blank=True, null=True, editable=False)

    # Timestamp fields for status tracking (populated from webhook responses)
    sent_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when message was sent")
    delivered_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when message was delivered")
    read_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when message was read")
    failed_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when message failed")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["broadcast", "contact"], name="unique_broadcast_contact")]
        indexes = [
            models.Index(fields=["broadcast", "status"]),
            models.Index(fields=["contact", "status"]),
        ]

    # objects = BroadcastMessageStatusManager()

    def __str__(self):
        return f"Broadcast {self.broadcast.id} to {self.contact} - {self.status}"

    @property
    def rendered_content(self):
        """
        Render the message content with placeholders replaced by actual data.

        Placeholder resolution order:
        1. Dynamic placeholders from broadcast.placeholder_data (user-provided)
        2. Reserved variables (contact fields, tenant info, date/time) - these OVERRIDE dynamic

        Reserved vars take precedence because they are contact-specific and should
        always reflect the actual contact data, not user-provided overrides.

        Supports both {{ name }} (with spaces) and {{name}} (without spaces) formats.
        """
        template_number = self.broadcast.template_number
        if self.broadcast.platform == BroadcastPlatformChoices.WHATSAPP:
            content = template_number.gupshup_template.content
        else:
            raise NotImplementedError("Rendering not implemented for this platform {}".format(self.broadcast.platform))

        import re

        # Get reserved variables for this contact
        reserved_vars = self._get_contact_reserved_vars()

        # Merge: placeholder_data first, then reserved vars override
        # Reserved vars take precedence - contact-specific data should not be overridden
        final_data = {**self.broadcast.placeholder_data, **reserved_vars}

        # Replace all placeholders using regex to handle both formats:
        # {{ name }} and {{name}}
        def replace_placeholder(match):
            key = match.group(1).strip()
            value = final_data.get(key, match.group(0))  # Keep original if not found
            return str(value) if value else ""

        # Pattern matches {{ key }}, {{key}}, {{ key}}, {key }} etc.
        placeholder_pattern = r"\{\{\s*(\w+)\s*\}\}"
        rendered_content = re.sub(placeholder_pattern, replace_placeholder, content)

        return rendered_content

    def _get_contact_reserved_vars(self):
        """Get reserved variables for this contact and broadcast"""
        reserved_vars = {}

        # Get basic contact fields from RESERVED_VARS
        _reserved_vars = TenantContact.RESERVED_VARS
        for var in _reserved_vars:
            key = var["key"]
            if hasattr(self.contact, key):
                value = getattr(self.contact, key, "")
                # Handle callable properties like full_name
                if callable(value):
                    value = value()
                reserved_vars[key] = str(value) if value else ""

        # Contact-specific variables
        if hasattr(self, "contact") and self.contact:
            # Basic contact info
            contact_name = self.contact.full_name.strip() if hasattr(self.contact, "full_name") else ""
            if not contact_name:
                contact_name = "Customer"

            reserved_vars.update(
                {
                    "name": contact_name,  # Alias for contact_name
                    "contact_name": contact_name,
                    "phone": str(getattr(self.contact, "phone", "")),
                    "email": getattr(self.contact, "email", ""),
                }
            )

        # Company/Tenant variables
        if self.broadcast.tenant:
            reserved_vars.update(
                {
                    "company_name": self.broadcast.tenant.name or "Company",
                    "tenant_name": self.broadcast.tenant.name or "Company",
                }
            )

        # Date/time variables
        from django.utils import timezone

        now = timezone.localtime()
        reserved_vars.update(
            {
                "today_date": now.strftime("%B %d, %Y"),
                "current_time": now.strftime("%I:%M %p"),
                "current_year": str(now.year),
                "broadcast_name": self.broadcast.name,
            }
        )

        return reserved_vars

    @property
    def payload(self):
        """Generate payload for sending the broadcast message via API"""
        if self.broadcast.platform == BroadcastPlatformChoices.WHATSAPP:
            return self._wa_payload
        else:
            raise NotImplementedError(
                "Payload generation not implemented for platform {}".format(self.broadcast.platform)
            )

    def _build_template_components(self):
        """
        Build the components array for WhatsApp template message.
        Maps placeholder_data from broadcast to the numbered placeholders in the template.

        Returns:
            list: Array of component dictionaries
        """
        from wa.models import TemplateType, WATemplate

        if not self.broadcast.template_number:
            return []

        template: WATemplate = self.broadcast.template_number.gupshup_template
        placeholder_mapping = template.placeholder_mapping

        # Runtime fallback: if placeholder_mapping is empty but the template
        # has placeholders (e.g. {{1}}), re-extract and cache it.
        # This handles templates saved before numbered-placeholder support.
        if not placeholder_mapping:
            placeholder_mapping = template._extract_placeholder_mapping()
            if placeholder_mapping:
                # Persist so we don't re-extract every time
                template.placeholder_mapping = placeholder_mapping
                template.save(update_fields=["placeholder_mapping"])

        components = []
        placeholder_data = self.broadcast.placeholder_data

        # Get reserved variables to fill missing placeholders
        reserved_vars = self._get_contact_reserved_vars()

        # Merge placeholder_data with reserved_vars
        all_data = {**reserved_vars, **placeholder_data}

        # Handle media header component based on template type
        media_header = self._build_media_header_component(template)
        if media_header:
            components.append(media_header)

        # Process text placeholders only if placeholder_mapping exists.
        # IMPORTANT: Do NOT early-return for CAROUSEL templates — even without
        # text placeholders, carousel cards still need header media components.
        from wa.models import TemplateType as _TT

        if not placeholder_mapping:
            # Auto-detect body variables from template content when
            # placeholder_mapping is empty (e.g. synced templates where
            # mapping was never populated).  This builds a synthetic
            # mapping so the parameter-building loop below works.
            import re

            body_text = template.content or ""
            body_vars = re.findall(r"\{\{(\d+)\}\}", body_text)
            if body_vars:
                placeholder_mapping = {"content": {v: v for v in sorted(set(body_vars), key=int)}}

            # Even without placeholder_mapping, check for copy_code/OTP buttons
            if template.buttons:
                copy_code_components = self._build_copy_code_button_components(template.buttons, all_data)
                components.extend(copy_code_components)
            # For non-carousel templates with still-empty mapping, return early.
            # For CAROUSEL, fall through to the card-building section below.
            if not placeholder_mapping and template.template_type != _TT.CAROUSEL:
                return components

        # Map field names to component types
        field_to_component_type = {"header": "header", "content": "body", "footer": "footer"}

        # Process text fields (header, content/body, footer)
        for field, component_type in field_to_component_type.items():
            if field in placeholder_mapping:
                mapping = placeholder_mapping[field]
                parameters = []

                # Build parameters array based on the mapping
                # mapping is like {"1": "name", "2": "phone"}
                # For numbered templates (synced from WhatsApp), mapping is
                # {"1": "1", "2": "2"} — position maps to itself.
                for number in sorted(mapping.keys(), key=int):
                    placeholder_name = mapping[number]
                    value = all_data.get(placeholder_name, "")
                    # Never send literal {{name}} — WhatsApp rejects curly
                    # braces in parameter values with error #132012.
                    if not value:
                        if placeholder_name.isdigit():
                            # Numbered placeholder (e.g. {{1}}) — use contact
                            # name as a sensible default for the first param,
                            # empty dash for the rest
                            if placeholder_name == "1":
                                value = all_data.get("name", "") or all_data.get("first_name", "-")
                            else:
                                value = "-"
                        else:
                            # Named placeholder — use human-readable form
                            value = placeholder_name.replace("_", " ").title()

                    param = {"type": "text", "text": str(value)}
                    # META Cloud API requires parameter_name for NAMED-format templates.
                    # Named placeholders have non-digit keys (e.g. "first_name"),
                    # while positional ones are digits (e.g. "1").
                    if not placeholder_name.isdigit():
                        param["parameter_name"] = placeholder_name
                    parameters.append(param)

                if parameters:
                    components.append({"type": component_type, "parameters": parameters})

        # Process buttons with placeholder mappings (e.g., dynamic URL params)
        if "buttons" in placeholder_mapping:
            button_components = self._build_button_components(
                placeholder_mapping["buttons"], template.buttons, all_data
            )
            components.extend(button_components)

        # Process copy_code / OTP buttons (authentication templates)
        # These don't appear in placeholder_mapping but still need components
        if template.buttons:
            copy_code_components = self._build_copy_code_button_components(template.buttons, all_data)
            components.extend(copy_code_components)

        # Process cards (carousel templates)
        # For CAROUSEL templates, we ALWAYS build card components (even without
        # text placeholders) because each card header requires a media parameter.
        if template.template_type == TemplateType.CAROUSEL and template.cards:
            card_placeholder_mapping = (placeholder_mapping or {}).get("cards", {})
            card_components = self._build_card_components(
                card_placeholder_mapping,
                template.cards,
                all_data,
                template,
            )
            if card_components:
                # WhatsApp Cloud API requires a top-level CAROUSEL wrapper
                components.append(
                    {
                        "type": "CAROUSEL",
                        "cards": card_components,
                    }
                )
        elif "cards" in (placeholder_mapping or {}):
            # Non-carousel template that somehow has card placeholders (shouldn't happen)
            card_components = self._build_card_components(
                placeholder_mapping["cards"],
                template.cards,
                all_data,
                template,
            )
            components.extend(card_components)

        return components

    def _build_button_components(self, button_mappings, template_buttons, all_data):
        """
        Build button components from placeholder mappings.

        For URL buttons whose template URL base matches our tracking domain,
        the suffix parameter is replaced with a tracked short code so clicks
        route through our redirect endpoint.

        Args:
            button_mappings: List of button mapping dicts with button_index and url_mapping
            template_buttons: List of button definitions from template
            all_data: Combined placeholder and reserved variable data

        Returns:
            list: List of button component dictionaries
        """
        components = []

        if not button_mappings or not template_buttons:
            return components

        # ── URL tracking: create tracked URLs and get short-code overrides ──
        tracked_code_map = {}  # {button_index: short_code}
        try:
            from broadcast.url_tracker.service import create_tracked_urls_for_message

            tracked_code_map = create_tracked_urls_for_message(self)
        except Exception as e:
            logger.warning(f"URL tracking skipped for message {self.id}: {e}")

        for button_info in button_mappings:
            button_index = button_info["button_index"]
            url_mapping = button_info["url_mapping"]

            if button_index >= len(template_buttons):
                continue

            button = template_buttons[button_index]

            # Determine button sub_type
            button_type = button.get("type", "")
            if button_type == "URL":
                sub_type = "url"
            elif button_type == "QUICK_REPLY":
                sub_type = "quick_reply"
            else:
                continue

            # Build parameters for URL buttons
            parameters = []

            if button_type == "URL" and button_index in tracked_code_map:
                # ── Tracking-enabled URL button ──
                # Replace ALL suffix parameters with the single short code.
                # Template URL: https://our-server/r/{{1}} → param = short_code
                short_code = tracked_code_map[button_index]
                parameters.append({"type": "text", "text": short_code})
            else:
                # ── Standard parameter resolution ──
                for number in sorted(url_mapping.keys(), key=int):
                    placeholder_name = url_mapping[number]
                    value = all_data.get(placeholder_name, f"{{{{{placeholder_name}}}}}")

                    parameters.append({"type": "text", "text": str(value)})

            if parameters:
                components.append(
                    {"type": "button", "sub_type": sub_type, "index": str(button_index), "parameters": parameters}
                )

        return components

    def _build_copy_code_button_components(self, template_buttons, all_data):
        """
        Build copy_code / OTP button components for authentication templates.

        Authentication templates can have OTP buttons with otp_type COPY_CODE.
        These buttons require a component with sub_type "copy_code" and a
        coupon_code parameter containing the OTP value.

        Args:
            template_buttons: List of button definitions from template
            all_data: Combined placeholder and reserved variable data

        Returns:
            list: List of copy_code button component dictionaries
        """
        components = []

        if not template_buttons:
            return components

        for idx, button in enumerate(template_buttons):
            button_type = button.get("type", "")
            otp_type = button.get("otp_type", "")

            is_otp_copy_code = button_type == "OTP" and otp_type == "COPY_CODE"
            is_marketing_copy_code = button_type == "COPY_CODE"

            if is_otp_copy_code or is_marketing_copy_code:
                coupon_value = (
                    all_data.get("coupon_code")
                    or all_data.get("otp_code")
                    or all_data.get("code")
                    or all_data.get("otp")
                    or ""
                )

                # Fallback to the template's example value
                if not coupon_value:
                    example = button.get("example", [])
                    if example:
                        coupon_value = example[0]

                if coupon_value:
                    components.append(
                        {
                            "type": "button",
                            "sub_type": "copy_code",
                            "index": str(idx),
                            "parameters": [{"type": "coupon_code", "coupon_code": str(coupon_value)}],
                        }
                    )

        return components

    def _build_card_components(self, card_mappings, template_cards, all_data, template=None):
        """
        Build card (carousel) components from placeholder mappings.

        Each card MUST have:
        - header: Media parameter (VIDEO or IMAGE link) — REQUIRED by WhatsApp
        - body: Text placeholders (if any)
        - buttons: URL button placeholders (if any)

        Args:
            card_mappings: Dict mapping card index (str) → field mappings,
                           e.g. {"0": {"body": {"1": "name"}}, "1": {...}}
                           OR an empty dict if no text placeholders exist.
            template_cards: List of card definitions from WATemplate.cards
            all_data: Combined placeholder and reserved variable data
            template: WATemplate instance (needed to get card media)

        Returns:
            list: List of carousel card component dictionaries for the
                  CAROUSEL.cards array in the WhatsApp Cloud API payload.
        """
        components = []

        if not template_cards:
            return components

        # Get card media mapping: {card_index: TenantMedia}
        # Primary: use get_card_media_by_index() which relies on card_index field.
        # Fallback: if card_index is NULL on TenantMedia records (e.g. legacy or
        # synced templates), assign by position in the M2M queryset.
        card_media_map = {}
        if template:
            card_media_map = template.get_card_media_by_index()

            # Fallback: if the index-based map is empty but card_media exists,
            # assign TenantMedia to cards by position (order_by created_at).
            if not card_media_map:
                all_card_media = list(template.card_media.all().order_by("card_index", "created_at"))
                if all_card_media:
                    for idx, tm in enumerate(all_card_media):
                        card_media_map[idx] = tm

        # card_mappings is a dict like {"0": {...}, "1": {...}} from
        # _extract_placeholder_mapping, NOT a list. We iterate over ALL
        # template cards (not just ones with text placeholders) because
        # every card needs at least a header media component.
        for card_index, template_card in enumerate(template_cards):
            card_mapping = card_mappings.get(str(card_index), {}) if isinstance(card_mappings, dict) else {}

            card_component = {"card_index": card_index, "components": []}

            # ── Card HEADER (media) — REQUIRED ──────────────────────
            # WhatsApp requires each carousel card to have a header
            # component with the media parameter (image or video link).
            #
            # Strategy: trust headerType first (works for 95% of cases),
            # but allow the actual TenantMedia file extension to override
            # it when it positively contradicts (e.g. headerType says
            # IMAGE but file is .mp4).  When headerType is empty, fall
            # back to base64 → file ext → example_url detection.
            VIDEO_EXTENSIONS = (".mp4", ".3gp", ".webm", ".mov")
            header_format = (template_card.get("headerType") or "").upper()
            if header_format == "VIDEO":
                media_type = "video"
            elif header_format == "IMAGE":
                media_type = "image"
            else:
                # headerType is null/empty — detect from multiple sources.
                media_type = "image"  # default

                # 1. Check media_handle for base64-encoded "video/" prefix
                handle_str = template_card.get("media_handle") or ""
                if "dmlkZW8v" in handle_str:
                    media_type = "video"
                else:
                    # 2. Fall back to file extension from local card media
                    tenant_media_for_detect = card_media_map.get(card_index)
                    if tenant_media_for_detect and tenant_media_for_detect.media:
                        fname = (tenant_media_for_detect.media.name or "").lower()
                        if fname.endswith(VIDEO_EXTENSIONS):
                            media_type = "video"

                    # 3. Fall back to example_media_url extension
                    if media_type == "image":
                        example_url = (template_card.get("example_media_url") or "").lower().split("?")[0]
                        if example_url.endswith(VIDEO_EXTENSIONS):
                            media_type = "video"

            # Override: if headerType is present but the actual uploaded
            # TenantMedia file contradicts it, trust the file (handles
            # stale headerType from Gupshup sync where metadata says
            # IMAGE but the actual media is a video).
            if header_format:
                tenant_media_for_override = card_media_map.get(card_index)
                if tenant_media_for_override and tenant_media_for_override.media:
                    fname = (tenant_media_for_override.media.name or "").lower()
                    if fname.endswith(VIDEO_EXTENSIONS) and media_type != "video":
                        media_type = "video"
                    elif fname.endswith((".png", ".jpg", ".jpeg", ".webp")) and media_type != "image":
                        media_type = "image"

            # Get media URL from TenantMedia (card_media M2M).
            # Check for card-level media override from broadcast.media_overrides
            card_override_id = (self.broadcast.media_overrides or {}).get("cards", {}).get(str(card_index))
            logger.info(
                f"Card {card_index}: headerType={header_format or 'NULL'}, "
                f"detected media_type={media_type}, override_id={card_override_id}"
            )
            if card_override_id:
                try:
                    from tenants.models import TenantMedia as TM

                    override_tm = TM.objects.get(pk=card_override_id)
                    if override_tm.media:
                        media_url = self._get_absolute_media_url(override_tm.media)
                        # Use the template's headerType-based media_type as the
                        # primary type.  Only override it when the file extension
                        # *positively* indicates a different media category.
                        # WhatsApp enforces all carousel cards share the same
                        # header media type, so the template headerType is the
                        # most reliable source.
                        override_media_type = media_type  # from headerType
                        override_fname = (override_tm.media.name or "").lower()
                        if override_fname.endswith((".mp4", ".3gp", ".webm", ".mov")):
                            override_media_type = "video"
                        elif override_fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
                            override_media_type = "image"
                        # else: keep media_type from headerType detection
                        card_component["components"].append(
                            {
                                "type": "header",
                                "parameters": [{"type": override_media_type, override_media_type: {"link": media_url}}],
                            }
                        )
                except TM.DoesNotExist:
                    logger.warning(
                        f"media_overrides card {card_index} TenantMedia id={card_override_id} "
                        f"not found — falling back to template card media."
                    )
                    card_override_id = None  # fall through to default

            # Default: use template's card media if no override was applied
            if not card_override_id:
                media_url = None
                tenant_media = card_media_map.get(card_index)
                if tenant_media and tenant_media.media:
                    media_url = self._get_absolute_media_url(tenant_media.media)

                # Fallback: use card-level example_media_url (Meta CDN preview)
                if not media_url:
                    card_example_url = template_card.get("example_media_url")
                    if card_example_url:
                        media_url = card_example_url
                        logger.info(f"Card {card_index}: no TenantMedia, using example_media_url fallback")

                if media_url:
                    card_component["components"].append(
                        {"type": "header", "parameters": [{"type": media_type, media_type: {"link": media_url}}]}
                    )
                else:
                    logger.warning(
                        f"Card {card_index}: no media source found "
                        f"(no TenantMedia, no override, no example_media_url). "
                        f"WhatsApp will reject this send."
                    )

            # ── Card BODY (text placeholders) ───────────────────────
            if "body" in card_mapping:
                body_mapping = card_mapping["body"]
                body_parameters = []

                for number in sorted(body_mapping.keys(), key=int):
                    placeholder_name = body_mapping[number]
                    value = all_data.get(placeholder_name, f"{{{{{placeholder_name}}}}}")

                    body_parameters.append({"type": "text", "text": str(value)})

                if body_parameters:
                    card_component["components"].append({"type": "body", "parameters": body_parameters})

            # ── Card BUTTONS (URL placeholders) ─────────────────────
            if "buttons" in card_mapping:
                card_buttons = template_card.get("buttons", [])
                button_components = self._build_button_components(card_mapping["buttons"], card_buttons, all_data)
                card_component["components"].extend(button_components)

            # Always add card — even if only header (media is required)
            if card_component["components"]:
                components.append(card_component)

        return components

    def _build_media_header_component(self, template):
        """
        Build media header component based on template type.

        Supports: IMAGE, DOCUMENT, VIDEO, AUDIO
        Returns None for TEXT or unsupported types.

        Args:
            template: WATemplate instance

        Returns:
            dict: Header component with media parameters, or None
        """
        from wa.models import TemplateType

        # Media type configuration mapping
        # Format: template_type -> (parameter_type, extra_fields_func)
        MEDIA_TYPE_CONFIG = {
            TemplateType.IMAGE: {"param_type": "image", "extra_fields": lambda media: {}},
            TemplateType.DOCUMENT: {
                "param_type": "document",
                "extra_fields": lambda media: {"filename": media.name.split("/")[-1] if media.name else "document.pdf"},
            },
            TemplateType.VIDEO: {"param_type": "video", "extra_fields": lambda media: {}},
            # Add more media types here as needed:
            # TemplateType.AUDIO: {
            #     'param_type': 'audio',
            #     'extra_fields': lambda media: {}
            # },
        }

        # Check if template type requires media header
        if template.template_type not in MEDIA_TYPE_CONFIG:
            return None

        config = MEDIA_TYPE_CONFIG[template.template_type]
        param_type = config["param_type"]

        # Resolve media reference.
        # Priority:
        #   0. media_overrides    — user-uploaded replacement at broadcast time
        #   1. tenant_media       — locally uploaded file, served from our domain
        #   2. example_media_url  — Meta CDN preview link (may expire / 403)
        #
        # NOTE: media_handle (Meta upload handle) is intentionally NOT used here.
        # It is only valid for template creation (header_handle in example field),
        # NOT for sending template messages. Meta rejects {"id": handle} in the
        # send payload — only {"link": url} is accepted.
        media_param = None

        # 0. Check for media override on the parent broadcast
        override_media_id = (self.broadcast.media_overrides or {}).get("header")
        if override_media_id:
            try:
                from tenants.models import TenantMedia

                override_tm = TenantMedia.objects.get(pk=override_media_id)
                if override_tm.media:
                    media_url = self._get_absolute_media_url(override_tm.media)
                    media_param = {"link": media_url, **config["extra_fields"](override_tm.media)}
            except TenantMedia.DoesNotExist:
                logger.warning(
                    f"media_overrides header TenantMedia id={override_media_id} "
                    f"not found — falling back to template media."
                )

        # 1. tenant_media — locally uploaded file
        if not media_param and template.tenant_media and template.tenant_media.media:
            media_url = self._get_absolute_media_url(template.tenant_media.media)
            media_param = {"link": media_url, **config["extra_fields"](template.tenant_media.media)}

        # 2. example_media_url — Meta CDN preview link (may expire)
        if not media_param and template.example_media_url:
            media_param = {"link": template.example_media_url}

        if not media_param:
            logger.warning(
                f"Template '{template.element_name}' (type={template.template_type}) "
                f"has no tenant_media or example_media_url — "
                f"cannot build header component. WhatsApp will reject this send."
            )
            return None

        return {"type": "header", "parameters": [{"type": param_type, param_type: media_param}]}

    def _get_absolute_media_url(self, media_file):
        """
        Get absolute URL for a media file.

        Args:
            media_file: Django FileField/ImageField

        Returns:
            str: Absolute URL to the media file
        """
        media_url = media_file.url

        # Ensure it's an absolute URL
        if not media_url.startswith("http"):
            from django.contrib.sites.models import Site

            domain = Site.objects.get_current().domain
            media_url = f"https://{domain}{media_url}"

        return media_url

    @property
    def _wa_payload(self):
        """Generate WhatsApp Cloud API payload (works for both Gupshup and META Direct)."""
        payload = {
            "to": str(self.contact.phone),
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
        }
        if self.broadcast.template_number:
            from wa.models import TemplateType, WATemplate

            template: WATemplate = self.broadcast.template_number.gupshup_template

            # Build template payload
            template_payload = {"name": template.element_name, "language": {"code": template.language_code}}

            # Add components if there are placeholders, copy_code/OTP buttons,
            # media headers, or carousel cards (carousel ALWAYS needs components
            # even without text placeholders — each card requires a header media param).
            has_copy_code_buttons = template.buttons and any(
                btn.get("type") == "OTP" or btn.get("type", "").lower() == "copy_code"
                for btn in (template.buttons or [])
            )
            is_carousel = template.template_type == TemplateType.CAROUSEL
            has_media_header = template.template_type in (
                TemplateType.IMAGE,
                TemplateType.VIDEO,
                TemplateType.DOCUMENT,
            )
            # Also check if the template body/header contain {{...}} placeholders
            # even when placeholder_mapping hasn't been populated yet.
            has_content_placeholders = ("{{" in (template.content or "")) or ("{{" in (template.header or ""))
            if (
                template.placeholder_mapping
                or has_copy_code_buttons
                or is_carousel
                or has_media_header
                or has_content_placeholders
            ):
                components = self._build_template_components()
                if components:
                    template_payload["components"] = components
            payload["type"] = "template"
            payload["template"] = template_payload

        return payload
