"""
Telegram app models — bot configuration, webhook events, outbound messages.
"""
import secrets
import uuid

from django.conf import settings
from django.db import models

from abstract.models import BaseWebhookDumps
from contacts.models import TenantContact
from telegram.constants import (
    EVENT_TYPE_CHOICES,
    MESSAGE_TYPE_CHOICES,
    OUTBOUND_STATUS_CHOICES,
)
from tenants.models import Tenant


class TelegramBotApp(models.Model):
    """
    Stores a Telegram Bot API configuration for a tenant.

    Each tenant can have one or more bots. The bot_token is stored using
    django-encrypted-model-fields so that it is encrypted at rest.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="telegram_bots"
    )
    bot_token = models.CharField(
        max_length=255,
        help_text="Telegram Bot API token (stored encrypted at rest via FIELD_ENCRYPTION_KEY).",
    )
    bot_username = models.CharField(
        max_length=255, blank=True, help_text="Bot @username (without @)"
    )
    bot_user_id = models.BigIntegerField(
        null=True, blank=True, help_text="Telegram's numeric user ID for this bot"
    )
    webhook_secret = models.CharField(
        max_length=64,
        blank=True,
        help_text="Random secret for X-Telegram-Bot-Api-Secret-Token validation",
    )
    webhook_url = models.URLField(
        max_length=512, blank=True, help_text="Auto-generated webhook URL"
    )
    is_active = models.BooleanField(default=True)
    daily_limit = models.IntegerField(default=1000, help_text="Daily message limit")
    messages_sent_today = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tenant", "bot_user_id")
        verbose_name = "Telegram Bot App"
        verbose_name_plural = "Telegram Bot Apps"

    def __str__(self):
        return f"@{self.bot_username}" if self.bot_username else str(self.id)

    def save(self, *args, **kwargs):
        if not self.webhook_secret:
            self.webhook_secret = secrets.token_urlsafe(48)[:64]
        if not self.webhook_url:
            base = getattr(settings, "DEFAULT_WEBHOOK_BASE_URL", "http://localhost:8000")
            self.webhook_url = f"{base.rstrip('/')}/telegram/v1/webhooks/{self.id}/"
        super().save(*args, **kwargs)

    @property
    def masked_token(self):
        """Return token with only last 4 chars visible — safe for logging."""
        if not self.bot_token:
            return ""
        return f"***{self.bot_token[-4:]}"


class TelegramWebhookEvent(BaseWebhookDumps):
    """
    Raw Telegram Update persisted for idempotent, async processing.

    Inherits payload, is_processed, processed_at, error_message, created_at,
    updated_at, is_active from BaseWebhookDumps.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    bot_app = models.ForeignKey(
        TelegramBotApp,
        on_delete=models.CASCADE,
        related_name="webhook_events",
    )
    update_id = models.BigIntegerField(help_text="Telegram update_id")
    event_type = models.CharField(
        max_length=30, choices=EVENT_TYPE_CHOICES, default="UNKNOWN"
    )
    retry_count = models.IntegerField(default=0)

    class Meta:
        unique_together = ("bot_app", "update_id")
        indexes = [
            models.Index(fields=["bot_app", "update_id"]),
            models.Index(fields=["is_processed", "created_at"]),
        ]
        verbose_name = "Telegram Webhook Event"
        verbose_name_plural = "Telegram Webhook Events"

    def __str__(self):
        return f"Event {self.update_id} ({self.event_type})"


class TelegramOutboundMessage(models.Model):
    """
    Tracks every outbound message sent via the Telegram Bot API.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    bot_app = models.ForeignKey(
        TelegramBotApp, on_delete=models.CASCADE, related_name="outbound_messages"
    )
    contact = models.ForeignKey(
        TenantContact, on_delete=models.CASCADE, related_name="telegram_outbound"
    )
    chat_id = models.BigIntegerField(help_text="Target Telegram chat ID")
    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPE_CHOICES)
    request_payload = models.JSONField(
        blank=True, null=True, help_text="What was sent to Telegram"
    )
    provider_message_id = models.BigIntegerField(
        null=True, blank=True, help_text="message_id from Telegram response"
    )
    status = models.CharField(
        max_length=20, choices=OUTBOUND_STATUS_CHOICES, default="PENDING"
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)
    inbox_message = models.ForeignKey(
        "team_inbox.Messages",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_outbound",
        help_text="Link to team inbox timeline entry",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram Outbound Message"
        verbose_name_plural = "Telegram Outbound Messages"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Outbound {self.message_type} → {self.chat_id} ({self.status})"
