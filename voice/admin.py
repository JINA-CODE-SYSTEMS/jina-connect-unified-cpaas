"""Django-admin registrations for voice models (#174).

Goals:
  * Credentials never appear in any admin list / detail view — the
    encrypted field is excluded outright; admins use a dedicated
    update form when they need to rotate credentials.
  * Recording detail shows a playable audio widget via signed URL —
    the bucket key never crosses the wire.
  * Rate cards inline on the provider config so a support agent can
    see the rates that will apply when they look at a call.
  * Append-only models (``VoiceCallEvent``) are read-only.
"""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from voice.models import (
    RecordingConsent,
    VoiceCall,
    VoiceCallEvent,
    VoiceProviderConfig,
    VoiceRateCard,
    VoiceRecording,
    VoiceTemplate,
)


class VoiceRateCardInline(admin.TabularInline):
    model = VoiceRateCard
    extra = 0
    fields = (
        "destination_prefix",
        "rate_per_minute",
        "currency",
        "billing_increment_seconds",
        "valid_from",
        "valid_to",
    )


@admin.register(VoiceProviderConfig)
class VoiceProviderConfigAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "provider",
        "vendor_label",
        "enabled",
        "priority",
        "fallback_sms_enabled",
        "credentials_set",
    )
    list_filter = ("provider", "enabled", "fallback_sms_enabled")
    search_fields = ("vendor_label", "tenant__name")
    raw_id_fields = ("tenant", "fallback_sms_config")
    inlines = (VoiceRateCardInline,)
    # ``credentials`` is encrypted at rest. Excluding it entirely from
    # the admin form means it cannot leak into list/detail/history —
    # rotation goes through the REST API or a separate management
    # command, where access can be audited per-action.
    exclude = ("credentials",)

    @admin.display(boolean=True, description="Credentials set")
    def credentials_set(self, obj) -> bool:
        return bool(obj.credentials)


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
        "cost_amount",
        "cost_source",
    )
    list_filter = ("direction", "status", "cost_source")
    search_fields = ("provider_call_id", "from_number", "to_number")
    raw_id_fields = ("contact", "parent_call", "flow_session", "broadcast", "team_inbox_message")
    readonly_fields = (
        "provider_call_id",
        "started_at",
        "ended_at",
        "duration_seconds",
        "hangup_cause",
        "recording_url",
        "recording_duration_seconds",
        "cost_amount",
        "cost_currency",
        "cost_source",
        "metadata",
    )


@admin.register(VoiceCallEvent)
class VoiceCallEventAdmin(admin.ModelAdmin):
    """Append-only — every field is read-only in admin."""

    list_display = ("call", "sequence", "event_type", "occurred_at")
    list_filter = ("event_type",)
    raw_id_fields = ("call",)
    readonly_fields = ("call", "event_type", "payload", "occurred_at", "sequence")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(VoiceTemplate)
class VoiceTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "name", "template_kind", "tts_language")
    list_filter = ("template_kind",)
    search_fields = ("name",)
    raw_id_fields = ("tenant",)


@admin.register(VoiceRecording)
class VoiceRecordingAdmin(admin.ModelAdmin):
    """Recording listing + playable audio widget.

    The ``audio_player`` field on the detail view emits an
    ``<audio src="…signed url…">`` so support staff can play the
    recording inline. We never serve the raw bucket key.
    """

    list_display = ("id", "call", "duration_seconds", "format", "retention_expires_at")
    raw_id_fields = ("call",)
    readonly_fields = (
        "provider_recording_id",
        "storage_url",
        "duration_seconds",
        "size_bytes",
        "format",
        "transcription",
        "transcription_provider",
        "transcription_confidence",
        "retention_expires_at",
        "audio_player",
    )

    def audio_player(self, obj):
        if not obj or not obj.storage_url:
            return "—"
        try:
            from voice.recordings import storage

            url = storage.signed_url(obj.storage_url, expires_seconds=600)
        except Exception:  # noqa: BLE001
            return "(unable to sign URL)"
        return format_html('<audio src="{}" controls preload="none"></audio>', url)

    audio_player.short_description = "Playback (10 min URL)"


@admin.register(VoiceRateCard)
class VoiceRateCardAdmin(admin.ModelAdmin):
    """Standalone rate-card admin — also inlined on the provider
    config so support agents can scan rates without context-switching."""

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
    raw_id_fields = ("provider_config",)


@admin.register(RecordingConsent)
class RecordingConsentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "contact",
        "consent_given",
        "consent_method",
        "consent_timestamp",
    )
    list_filter = ("consent_given", "consent_method")
    raw_id_fields = ("tenant", "contact")
