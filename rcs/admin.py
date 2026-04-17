from django.contrib import admin

from rcs.models import RCSApp, RCSOutboundMessage, RCSTemplate, RCSTemplateCard, RCSWebhookEvent


@admin.register(RCSApp)
class RCSAppAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "provider",
        "agent_id",
        "agent_name",
        "is_active",
        "messages_sent_today",
        "daily_limit",
        "created_at",
    )
    list_filter = ("provider", "is_active")
    search_fields = ("agent_id", "agent_name", "tenant__name")
    readonly_fields = ("webhook_url", "webhook_client_token", "messages_sent_today")


@admin.register(RCSOutboundMessage)
class RCSOutboundMessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "to_phone",
        "message_type",
        "status",
        "cost",
        "created_at",
    )
    list_filter = ("status", "message_type", "rcs_app__provider")
    search_fields = ("to_phone", "provider_message_id")


@admin.register(RCSWebhookEvent)
class RCSWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "provider", "sender_phone", "is_processed", "created_at")
    list_filter = ("event_type", "provider", "is_processed")
    search_fields = ("provider_message_id", "sender_phone")


@admin.register(RCSTemplate)
class RCSTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "name", "message_type", "is_active", "created_at")
    list_filter = ("message_type", "is_active")
    search_fields = ("name", "tenant__name")


@admin.register(RCSTemplateCard)
class RCSTemplateCardAdmin(admin.ModelAdmin):
    list_display = ("id", "template", "order", "title")
    search_fields = ("title", "template__name")
