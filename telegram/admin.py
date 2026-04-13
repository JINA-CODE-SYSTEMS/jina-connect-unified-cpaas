"""
Telegram Django Admin registration.
"""
from django.contrib import admin

from telegram.models import TelegramBotApp, TelegramOutboundMessage, TelegramWebhookEvent


@admin.register(TelegramBotApp)
class TelegramBotAppAdmin(admin.ModelAdmin):
    list_display = ("bot_username", "tenant", "is_active", "daily_limit", "messages_sent_today", "created_at")
    list_filter = ("is_active", "tenant")
    readonly_fields = ("id", "webhook_url", "webhook_secret", "bot_user_id", "created_at", "updated_at")
    search_fields = ("bot_username", "tenant__name")


@admin.register(TelegramWebhookEvent)
class TelegramWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("update_id", "event_type", "is_processed", "retry_count", "created_at")
    list_filter = ("event_type", "is_processed")
    readonly_fields = ("id", "payload", "processed_at", "created_at")
    search_fields = ("update_id",)


@admin.register(TelegramOutboundMessage)
class TelegramOutboundMessageAdmin(admin.ModelAdmin):
    list_display = ("chat_id", "message_type", "status", "provider_message_id", "sent_at", "created_at")
    list_filter = ("status", "message_type")
    readonly_fields = ("id", "request_payload", "created_at", "updated_at")
    search_fields = ("chat_id", "provider_message_id")
