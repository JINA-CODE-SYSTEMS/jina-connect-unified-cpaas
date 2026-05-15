"""Django-admin registrations for voice models.

Kept intentionally minimal in this PR — the full admin (with credential
masking, signed-URL playback, etc.) lands in PR #174 alongside the REST
API surface.
"""

from __future__ import annotations

from django.contrib import admin

from voice.models import (
    VoiceCall,
    VoiceCallEvent,
    VoiceProviderConfig,
    VoiceRateCard,
    VoiceRecording,
    VoiceTemplate,
)


@admin.register(VoiceProviderConfig)
class VoiceProviderConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "provider", "vendor_label", "enabled", "priority")
    list_filter = ("provider", "enabled")
    search_fields = ("vendor_label", "tenant__name")
    # ``credentials`` is encrypted at rest; readonly here so it isn't
    # leaked into admin list/detail. Editing should happen via a
    # dedicated form action added in #174.
    readonly_fields = ("credentials",)


@admin.register(VoiceCall)
class VoiceCallAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "direction",
        "from_number",
        "to_number",
        "status",
        "duration_seconds",
        "started_at",
    )
    list_filter = ("direction", "status")
    search_fields = ("provider_call_id", "from_number", "to_number")
    raw_id_fields = ("contact", "parent_call", "flow_session", "broadcast")


@admin.register(VoiceCallEvent)
class VoiceCallEventAdmin(admin.ModelAdmin):
    list_display = ("call", "sequence", "event_type", "occurred_at")
    list_filter = ("event_type",)
    raw_id_fields = ("call",)
    readonly_fields = ("call", "event_type", "payload", "occurred_at", "sequence")


@admin.register(VoiceTemplate)
class VoiceTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "name", "template_kind", "tts_language")
    list_filter = ("template_kind",)
    search_fields = ("name",)


@admin.register(VoiceRecording)
class VoiceRecordingAdmin(admin.ModelAdmin):
    list_display = ("id", "call", "duration_seconds", "format", "retention_expires_at")
    raw_id_fields = ("call",)


@admin.register(VoiceRateCard)
class VoiceRateCardAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "provider_config",
        "destination_prefix",
        "rate_per_minute",
        "currency",
        "valid_from",
        "valid_to",
    )
    list_filter = ("currency",)
    search_fields = ("destination_prefix",)
