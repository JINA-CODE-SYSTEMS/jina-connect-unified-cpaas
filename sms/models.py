"""SMS app models - app config, webhook events, and outbound tracking."""

import secrets
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F
from encrypted_model_fields.fields import EncryptedTextField

from abstract.models import BaseWebhookDumps
from contacts.models import TenantContact
from sms.constants import OUTBOUND_STATUS_CHOICES, PROVIDER_CHOICES, WEBHOOK_EVENT_TYPES
from tenants.models import Tenant


class SMSApp(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="sms_apps")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="TWILIO")
    provider_credentials = EncryptedTextField(
        blank=True,
        null=True,
        help_text="Provider credentials (API keys, auth tokens) stored as an encrypted JSON string.",
    )
    sender_id = models.CharField(max_length=20, help_text="Sender ID, short code, or sending number")
    is_active = models.BooleanField(default=True)
    daily_limit = models.IntegerField(default=10000)
    messages_sent_today = models.IntegerField(default=0)

    webhook_secret = models.CharField(max_length=64, blank=True, unique=True)
    webhook_url = models.URLField(max_length=512, blank=True)
    dlr_webhook_url = models.URLField(max_length=512, blank=True)

    dlt_entity_id = models.CharField(max_length=30, blank=True, null=True)
    dlt_template_id = models.CharField(max_length=30, blank=True, null=True)

    price_per_sms = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    price_per_sms_international = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Provider failover (#104) — if this app fails, try fallback_app
    fallback_app = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fallback_for",
        help_text="Fallback SMS app to use if sending via this app fails",
    )

    class Meta:
        unique_together = ("tenant", "provider", "sender_id")
        verbose_name = "SMS App"
        verbose_name_plural = "SMS Apps"

    def __str__(self):
        return f"{self.provider} - {self.sender_id}"

    def save(self, *args, **kwargs):
        if not self.webhook_secret:
            self.webhook_secret = secrets.token_urlsafe(48)[:64]
        if not self.webhook_url:
            base = getattr(settings, "DEFAULT_WEBHOOK_BASE_URL", "http://localhost:8000")
            self.webhook_url = f"{base.rstrip('/')}/sms/v1/webhooks/{self.id}/inbound/"
        if not self.dlr_webhook_url:
            base = getattr(settings, "DEFAULT_WEBHOOK_BASE_URL", "http://localhost:8000")
            self.dlr_webhook_url = f"{base.rstrip('/')}/sms/v1/webhooks/{self.id}/dlr/"
        super().save(*args, **kwargs)

    def increment_daily_counter(self) -> bool:
        updated = SMSApp.objects.filter(pk=self.pk, messages_sent_today__lt=F("daily_limit")).update(
            messages_sent_today=F("messages_sent_today") + 1
        )
        return updated > 0


class SMSWebhookEvent(BaseWebhookDumps):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    sms_app = models.ForeignKey(SMSApp, on_delete=models.CASCADE, related_name="webhook_events")
    event_type = models.CharField(max_length=20, choices=WEBHOOK_EVENT_TYPES, default="UNKNOWN")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    from_number = models.CharField(max_length=32, blank=True)
    to_number = models.CharField(max_length=32, blank=True)
    provider_message_id = models.CharField(max_length=120, blank=True)
    retry_count = models.IntegerField(default=0)

    class Meta:
        unique_together = ("sms_app", "provider_message_id", "event_type")
        indexes = [
            models.Index(fields=["sms_app", "provider_message_id"]),
            models.Index(fields=["is_processed", "created_at"]),
        ]


class SMSOutboundMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    sms_app = models.ForeignKey(SMSApp, on_delete=models.CASCADE, related_name="outbound_messages")
    contact = models.ForeignKey(
        TenantContact, null=True, blank=True, on_delete=models.SET_NULL, related_name="sms_messages"
    )

    to_number = models.CharField(max_length=32)
    from_number = models.CharField(max_length=32, blank=True)
    message_text = models.TextField()
    segment_count = models.IntegerField(default=1)

    provider_message_id = models.CharField(max_length=120, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=OUTBOUND_STATUS_CHOICES, default="PENDING")

    cost = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    provider_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)

    request_payload = models.JSONField(blank=True, null=True)
    response_payload = models.JSONField(blank=True, null=True)
    error_code = models.CharField(max_length=32, blank=True)
    error_message = models.TextField(blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    inbox_message = models.ForeignKey(
        "team_inbox.Messages",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_outbound_messages",
    )
    broadcast_message = models.ForeignKey(
        "broadcast.BroadcastMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_outbound_messages",
    )

    # Which provider actually sent this message (#104)
    provider_used = models.CharField(
        max_length=20,
        choices=PROVIDER_CHOICES,
        blank=True,
        help_text="Provider that actually sent this message (may differ from sms_app.provider after failover)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.to_number} ({self.status})"
