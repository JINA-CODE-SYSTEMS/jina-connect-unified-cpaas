"""RCS app models - app config, webhook events, and outbound tracking."""

import secrets
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F
from encrypted_model_fields.fields import EncryptedTextField

from abstract.models import BaseWebhookDumps
from contacts.models import TenantContact
from rcs.constants import (
    MESSAGE_TYPE_CHOICES,
    PROVIDER_CHOICES,
    STATUS_CHOICES,
    TRAFFIC_TYPE_CHOICES,
    WEBHOOK_EVENT_TYPES,
)
from tenants.models import Tenant


class RCSApp(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="rcs_apps")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="GOOGLE_RBM")
    provider_credentials = EncryptedTextField(
        blank=True,
        null=True,
        help_text="Provider credentials stored as an encrypted JSON string.",
    )
    agent_id = models.CharField(max_length=100, help_text="Google RBM agent ID or Meta phone number ID")
    agent_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    daily_limit = models.IntegerField(default=10000)
    messages_sent_today = models.IntegerField(default=0)

    webhook_url = models.URLField(max_length=512, blank=True)
    webhook_client_token = models.CharField(max_length=64, blank=True, unique=True)

    sms_fallback_enabled = models.BooleanField(default=True)
    sms_fallback_app = models.ForeignKey(
        "sms.SMSApp",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rcs_fallback_apps",
    )

    price_per_message = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    price_per_rich_message = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tenant", "provider", "agent_id")
        verbose_name = "RCS App"
        verbose_name_plural = "RCS Apps"

    def __str__(self):
        return f"{self.provider} - {self.agent_name or self.agent_id}"

    def save(self, *args, **kwargs):
        if not self.webhook_client_token:
            self.webhook_client_token = secrets.token_urlsafe(48)[:64]
        super().save(*args, **kwargs)
        # Set webhook_url AFTER super().save() so self.pk is available on first create
        base = getattr(settings, "DEFAULT_WEBHOOK_BASE_URL", "")
        if base and self.pk and not self.webhook_url:
            self.webhook_url = f"{base.rstrip('/')}/rcs/v1/webhooks/{self.pk}/"
            RCSApp.objects.filter(pk=self.pk).update(webhook_url=self.webhook_url)

    def increment_daily_counter(self) -> bool:
        """Atomically increment counter only if under daily limit (mirrors SMSApp pattern)."""
        updated = RCSApp.objects.filter(
            pk=self.pk,
            messages_sent_today__lt=F("daily_limit"),
        ).update(messages_sent_today=F("messages_sent_today") + 1)
        return updated > 0


class RCSWebhookEvent(BaseWebhookDumps):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    rcs_app = models.ForeignKey(RCSApp, on_delete=models.CASCADE, related_name="webhook_events")
    event_type = models.CharField(max_length=30, choices=WEBHOOK_EVENT_TYPES, default="UNKNOWN")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    sender_phone = models.CharField(max_length=20, blank=True)
    provider_message_id = models.CharField(max_length=120, blank=True)
    retry_count = models.IntegerField(default=0)

    class Meta:
        unique_together = ("rcs_app", "provider_message_id", "event_type")
        indexes = [
            models.Index(fields=["rcs_app", "provider_message_id"]),
            models.Index(fields=["is_processed", "created_at"]),
        ]


class RCSOutboundMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    rcs_app = models.ForeignKey(RCSApp, on_delete=models.CASCADE, related_name="outbound_messages")
    contact = models.ForeignKey(
        TenantContact,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rcs_messages",
    )

    to_phone = models.CharField(max_length=20)
    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPE_CHOICES, default="TEXT")
    message_content = models.JSONField(blank=True, null=True)
    suggestions = models.JSONField(blank=True, null=True)

    provider_message_id = models.CharField(max_length=120, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")

    cost = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    traffic_type = models.CharField(max_length=30, choices=TRAFFIC_TYPE_CHOICES, default="TRANSACTION")

    request_payload = models.JSONField(blank=True, null=True)
    response_payload = models.JSONField(blank=True, null=True)
    error_code = models.CharField(max_length=30, blank=True)
    error_message = models.TextField(blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    fallback_sms = models.ForeignKey(
        "sms.SMSOutboundMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rcs_fallback_messages",
    )
    inbox_message = models.ForeignKey(
        "team_inbox.Messages",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rcs_outbound_messages",
    )
    broadcast_message = models.ForeignKey(
        "broadcast.BroadcastMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rcs_outbound_messages",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.to_phone} ({self.status})"


class RCSTemplate(models.Model):
    """Stored RCS message template for reuse across broadcasts (#119)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="rcs_templates")
    rcs_app = models.ForeignKey(RCSApp, on_delete=models.CASCADE, related_name="templates")
    name = models.CharField(max_length=255)
    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPE_CHOICES, default="TEXT")
    body_text = models.TextField(blank=True, help_text="Template body text (supports {{placeholders}})")
    suggestions = models.JSONField(default=list, blank=True, help_text="Default suggestion chips")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tenant", "name")
        verbose_name = "RCS Template"
        verbose_name_plural = "RCS Templates"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.message_type})"


class RCSTemplateCard(models.Model):
    """Individual card within a carousel/rich-card RCS template (#119)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(RCSTemplate, on_delete=models.CASCADE, related_name="cards")
    order = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True, max_length=2000)
    media_url = models.URLField(max_length=512, blank=True)
    media_height = models.CharField(max_length=10, default="MEDIUM")
    suggestions = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["order"]
        verbose_name = "RCS Template Card"

    def __str__(self):
        return f"Card {self.order}: {self.title[:50]}"
