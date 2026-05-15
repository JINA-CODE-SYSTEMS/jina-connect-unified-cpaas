"""DRF serializers for the voice channel (#174).

Conventions match ``sms/serializers.py`` and ``wa/`` —
``ModelSerializer`` with explicit read-only fields, encrypted-credential
field is write-only and never round-trips back to the client. Where a
storage key would otherwise be exposed (recordings), we surface a
short-lived presigned URL via ``voice.recordings.storage.signed_url``
instead.
"""

from __future__ import annotations

import json

from rest_framework import serializers

from tenants.models import TenantVoiceApp
from voice.models import (
    RecordingConsent,
    VoiceCall,
    VoiceCallEvent,
    VoiceProviderConfig,
    VoiceRateCard,
    VoiceRecording,
    VoiceTemplate,
)

# ─────────────────────────────────────────────────────────────────────────────
# VoiceProviderConfig
# ─────────────────────────────────────────────────────────────────────────────


class VoiceProviderConfigSerializer(serializers.ModelSerializer):
    """Provider connection. ``credentials`` is write-only and stored
    encrypted; reads see ``credentials_set`` (bool) instead so admins
    can verify configuration without leaking the secret."""

    credentials = serializers.JSONField(write_only=True, required=False, allow_null=True)
    credentials_set = serializers.SerializerMethodField()

    class Meta:
        model = VoiceProviderConfig
        fields = "__all__"
        read_only_fields = ["id", "tenant", "created_at", "updated_at"]

    def get_credentials_set(self, obj) -> bool:
        return bool(obj.credentials)

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)
        creds = ret.get("credentials")
        if isinstance(creds, dict):
            ret["credentials"] = json.dumps(creds)
        elif creds is None and "credentials" in ret:
            ret["credentials"] = None
        return ret


# ─────────────────────────────────────────────────────────────────────────────
# Calls + events
# ─────────────────────────────────────────────────────────────────────────────


class VoiceCallEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceCallEvent
        fields = ("id", "sequence", "event_type", "payload", "occurred_at")
        read_only_fields = fields


class VoiceCallSerializer(serializers.ModelSerializer):
    """Read-only-from-the-API call row plus its recent events."""

    recent_events = serializers.SerializerMethodField()

    class Meta:
        model = VoiceCall
        fields = "__all__"
        read_only_fields = (
            "id",
            "tenant",
            "provider_call_id",
            "status",
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
            "team_inbox_message",
            "created_at",
            "updated_at",
        )

    def get_recent_events(self, obj) -> list[dict]:
        events = obj.events.all().order_by("-sequence")[:20]
        return VoiceCallEventSerializer(reversed(list(events)), many=True).data


# ─────────────────────────────────────────────────────────────────────────────
# Templates
# ─────────────────────────────────────────────────────────────────────────────


class VoiceTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceTemplate
        fields = "__all__"
        read_only_fields = ("id", "tenant", "created_at", "updated_at")


# ─────────────────────────────────────────────────────────────────────────────
# Recordings — never expose raw storage key
# ─────────────────────────────────────────────────────────────────────────────


class VoiceRecordingSerializer(serializers.ModelSerializer):
    """Recording row. ``storage_url`` on the wire is a fresh presigned
    GET URL, not the bucket key. The TTL is fixed (1h) — callers that
    need a custom TTL should use the ``download`` action."""

    storage_url = serializers.SerializerMethodField()

    class Meta:
        model = VoiceRecording
        fields = "__all__"
        # Recordings are produced by the system, never written via REST.
        read_only_fields = (
            "id",
            "call",
            "provider_recording_id",
            "duration_seconds",
            "size_bytes",
            "format",
            "transcription",
            "transcription_provider",
            "transcription_confidence",
            "retention_expires_at",
            "created_at",
            "updated_at",
        )

    def get_storage_url(self, obj) -> str | None:
        if not obj.storage_url:
            return None
        from voice.recordings import storage

        try:
            return storage.signed_url(obj.storage_url)
        except Exception:  # noqa: BLE001 — surface as null rather than 500
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Rate cards
# ─────────────────────────────────────────────────────────────────────────────


class VoiceRateCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceRateCard
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")


# ─────────────────────────────────────────────────────────────────────────────
# Tenant voice app + recording consent
# ─────────────────────────────────────────────────────────────────────────────


class TenantVoiceAppSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantVoiceApp
        fields = "__all__"
        read_only_fields = ("id", "tenant", "created_at", "updated_at")


class RecordingConsentSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecordingConsent
        fields = "__all__"
        read_only_fields = ("id", "tenant", "created_at", "updated_at")
