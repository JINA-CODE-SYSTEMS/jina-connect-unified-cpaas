from django.contrib import admin

from sms.models import SMSApp, SMSOutboundMessage, SMSWebhookEvent


@admin.register(SMSApp)
class SMSAppAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "provider",
        "sender_id",
        "is_active",
        "messages_sent_today",
        "daily_limit",
        "created_at",
    )
    list_filter = ("provider", "is_active")
    search_fields = ("sender_id", "tenant__name")
    readonly_fields = ("webhook_url", "dlr_webhook_url", "webhook_secret", "messages_sent_today")


@admin.register(SMSOutboundMessage)
class SMSOutboundMessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "to_number",
        "status",
        "segment_count",
        "cost",
        "created_at",
    )
    list_filter = ("status", "sms_app__provider")
    search_fields = ("to_number", "provider_message_id")


@admin.register(SMSWebhookEvent)
class SMSWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "provider", "from_number", "to_number", "is_processed", "created_at")
    list_filter = ("event_type", "provider", "is_processed")
    search_fields = ("provider_message_id", "from_number", "to_number")
