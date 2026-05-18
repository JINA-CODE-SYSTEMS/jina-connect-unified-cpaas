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

from django.db import IntegrityError, transaction
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

    def validate(self, attrs):
        """Run per-provider credential schema validation at the API boundary.

        Without this, a PATCH with a credentials payload that doesn't
        match the chosen provider's schema sneaks through the
        serializer and only fails when the adapter is first used.
        Pre-validating here turns the failure into a 400 right at the
        write call. (#179 review)
        """
        from voice.adapters.credentials import validate_credentials
        from voice.exceptions import VoiceCredentialError

        creds = attrs.get("credentials")
        # ``provider`` may be absent on a PATCH; fall back to the
        # instance's existing value.
        provider = attrs.get("provider") or (self.instance.provider if self.instance else None)
        if creds and provider:
            try:
                validate_credentials(provider, creds)
            except VoiceCredentialError as exc:
                raise serializers.ValidationError({"credentials": str(exc)}) from exc
        return super().validate(attrs)

    # ── B3 (#183): transactional default-flag mutex ───────────────────────
    # The model has partial unique constraints on (tenant) conditioned on
    # is_default_outbound=True / is_default_inbound=True. Naive save() on
    # a row that flips a flag to True while another row in the same tenant
    # already holds it would raise IntegrityError. We flip the existing
    # holder off in the same transaction so the API does the safe thing
    # by default — the alternative (two PATCHes from the frontend) has a
    # race window between the demote and the promote.
    _DEFAULT_FLAGS = ("is_default_outbound", "is_default_inbound")

    def _demote_existing_defaults(self, tenant, validated_data, *, exclude_pk=None) -> None:
        for flag in self._DEFAULT_FLAGS:
            if not validated_data.get(flag):
                continue
            qs = VoiceProviderConfig.objects.filter(tenant=tenant, **{flag: True})
            if exclude_pk is not None:
                qs = qs.exclude(pk=exclude_pk)
            qs.update(**{flag: False})

    @staticmethod
    def _raise_default_race(flag: str) -> None:
        # Read-committed isolation lets two concurrent transactions
        # both see "no current default" during their respective
        # ``_demote_existing_defaults`` and proceed to flip themselves
        # to True — the partial unique constraint then 500s the second
        # commit. Translate to a 400 so the caller can retry rather
        # than seeing an opaque server error. (#185 review)
        raise serializers.ValidationError({flag: "Another default was promoted concurrently; please retry."})

    def create(self, validated_data):
        tenant = validated_data.get("tenant")
        try:
            with transaction.atomic():
                if tenant is not None:
                    self._demote_existing_defaults(tenant, validated_data)
                return super().create(validated_data)
        except IntegrityError:
            flag = "is_default_outbound" if validated_data.get("is_default_outbound") else "is_default_inbound"
            self._raise_default_race(flag)

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                self._demote_existing_defaults(instance.tenant, validated_data, exclude_pk=instance.pk)
                return super().update(instance, validated_data)
        except IntegrityError:
            flag = "is_default_outbound" if validated_data.get("is_default_outbound") else "is_default_inbound"
            self._raise_default_race(flag)


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
