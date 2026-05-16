"""DRF viewsets for the voice channel (#174).

Tenant scoping: every queryset filters by
``tenant__tenant_users__user=request.user``. Cross-tenant access
returns 404 (not 403) so the API doesn't leak whether a given UUID
exists in another tenant. ``perform_create`` stamps the requesting
user's tenant onto the row.

Custom actions:

  * ``POST /api/v1/voice/calls/initiate/`` — queues an outbound call.
  * ``POST /api/v1/voice/calls/{id}/hangup/`` — ends a live call.
  * ``GET  /api/v1/voice/recordings/{id}/download/`` — fresh signed URL
    with a caller-specified TTL.
  * ``POST /api/v1/voice/templates/{id}/preview/`` — TTS-preview stub
    (text returned; full audio synth lives behind the TTS service that
    ships with the transcription work).
"""

from __future__ import annotations

import uuid

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

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
from voice.permissions import IsAuthenticated, IsVoiceAdmin, IsVoiceEnabledForTenant
from voice.serializers import (
    RecordingConsentSerializer,
    TenantVoiceAppSerializer,
    VoiceCallEventSerializer,
    VoiceCallSerializer,
    VoiceProviderConfigSerializer,
    VoiceRateCardSerializer,
    VoiceRecordingSerializer,
    VoiceTemplateSerializer,
)


def _user_tenant(request):
    """Return the requesting user's *first* tenant.

    Multi-tenant users hit the API on behalf of one tenant at a time —
    the current convention across the codebase is "first tenant
    association wins" via ``user_tenants.first()``. The viewsets that
    write rows use this; reads filter on the membership graph anyway.
    """
    tenant_user = request.user.user_tenants.first()
    if not tenant_user:
        raise PermissionDenied("User has no associated tenant.")
    return tenant_user.tenant


# ─────────────────────────────────────────────────────────────────────────────
# Provider configs (admin only)
# ─────────────────────────────────────────────────────────────────────────────


class VoiceProviderConfigViewSet(viewsets.ModelViewSet):
    serializer_class = VoiceProviderConfigSerializer
    permission_classes = [IsAuthenticated, IsVoiceEnabledForTenant, IsVoiceAdmin]

    def get_queryset(self):
        return (
            VoiceProviderConfig.objects.filter(tenant__tenant_users__user=self.request.user)
            .order_by("-priority", "-created_at")
            .distinct()
        )

    def perform_create(self, serializer):
        serializer.save(tenant=_user_tenant(self.request))


# ─────────────────────────────────────────────────────────────────────────────
# Calls — read-only + initiate / hangup actions
# ─────────────────────────────────────────────────────────────────────────────


class VoiceCallViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = VoiceCallSerializer
    permission_classes = [IsAuthenticated, IsVoiceEnabledForTenant]

    def get_queryset(self):
        qs = (
            VoiceCall.objects.filter(tenant__tenant_users__user=self.request.user)
            .select_related("provider_config", "contact")
            .order_by("-started_at", "-created_at")
            .distinct()
        )
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("direction"):
            qs = qs.filter(direction=params["direction"].lower())
        if params.get("contact_id"):
            qs = qs.filter(contact_id=params["contact_id"])
        if params.get("started_at_from"):
            qs = qs.filter(started_at__gte=params["started_at_from"])
        if params.get("started_at_to"):
            qs = qs.filter(started_at__lte=params["started_at_to"])
        return qs

    @action(detail=False, methods=["post"], url_path="initiate")
    def initiate(self, request):
        """Queue an outbound call.

        Body: ``{"to_number": "+...", "tts_text": "..."}`` or
        ``{"to_number": "+...", "flow_id": "<uuid>"}``. Optional
        ``provider_config_id`` overrides the tenant default; optional
        ``from_number`` overrides the config's first DID.
        """
        from contacts.models import TenantContact
        from voice.constants import CallDirection, CallStatus

        tenant = _user_tenant(request)
        try:
            voice_app = TenantVoiceApp.objects.get(tenant=tenant)
        except TenantVoiceApp.DoesNotExist:
            return Response({"error": "Voice not provisioned."}, status=status.HTTP_400_BAD_REQUEST)

        to_number = (request.data.get("to_number") or "").strip()
        tts_text = request.data.get("tts_text") or None
        flow_id = request.data.get("flow_id") or None
        provider_config_id = request.data.get("provider_config_id")
        from_number = request.data.get("from_number")

        if not to_number:
            return Response({"error": "to_number is required."}, status=status.HTTP_400_BAD_REQUEST)
        if bool(flow_id) == bool(tts_text):
            return Response(
                {"error": "Provide exactly one of flow_id or tts_text."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        config = None
        if provider_config_id:
            try:
                config = VoiceProviderConfig.objects.get(pk=provider_config_id, tenant=tenant)
            except (VoiceProviderConfig.DoesNotExist, ValueError):
                return Response(
                    {"error": "Provider config not found for this tenant."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            if voice_app.default_outbound_config_id:
                config = voice_app.default_outbound_config
            if config is None:
                config = VoiceProviderConfig.objects.filter(tenant=tenant, enabled=True).order_by("-priority").first()
        if config is None:
            return Response(
                {"error": "No active VoiceProviderConfig for this tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if from_number is None:
            from_number = (config.from_numbers or [None])[0]
        if not from_number:
            return Response(
                {"error": "VoiceProviderConfig has no from_numbers configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contact, _ = TenantContact.objects.get_or_create(
            tenant=tenant,
            phone=to_number,
            defaults={"first_name": to_number, "source": "VOICE"},
        )

        metadata = {}
        if tts_text:
            metadata["static_play"] = {"tts_text": tts_text}

        # ``provider_call_id`` carries a placeholder until the adapter
        # replaces it with the real upstream SID. The (provider_config,
        # provider_call_id) unique constraint means concurrent dials to
        # the *same contact* collide if we use the contact id — burn
        # a UUID so each placeholder is unique.
        call = VoiceCall.objects.create(
            tenant=tenant,
            name=f"rest-{to_number}",
            provider_config=config,
            provider_call_id=f"pending-{uuid.uuid4()}",
            direction=CallDirection.OUTBOUND,
            from_number=str(from_number),
            to_number=to_number,
            contact=contact,
            status=CallStatus.QUEUED,
            metadata=metadata,
        )

        from voice.tasks import initiate_call as voice_initiate_call_task

        voice_initiate_call_task.delay(str(call.id))

        return Response(VoiceCallSerializer(call).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="hangup")
    def hangup(self, request, pk=None):
        """End an in-progress call via the resolved adapter."""
        from voice.adapters.registry import get_voice_adapter_cls
        from voice.constants import TERMINAL_STATUSES

        call = self.get_object()
        if call.status in TERMINAL_STATUSES:
            return Response({"call_id": str(call.id), "hung_up": False, "status": call.status})

        try:
            adapter_cls = get_voice_adapter_cls(call.provider_config.provider)
        except NotImplementedError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        adapter = adapter_cls(call.provider_config)
        try:
            adapter.hangup(call.provider_call_id)
        except Exception as exc:  # noqa: BLE001 — surface as 502
            return Response({"error": f"Hangup failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({"call_id": str(call.id), "hung_up": True})


class VoiceCallEventViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = VoiceCallEventSerializer
    permission_classes = [IsAuthenticated, IsVoiceEnabledForTenant]

    def get_queryset(self):
        qs = (
            VoiceCallEvent.objects.filter(call__tenant__tenant_users__user=self.request.user)
            .order_by("call", "sequence")
            .distinct()
        )
        call_id = self.request.query_params.get("call_id")
        if call_id:
            qs = qs.filter(call_id=call_id)
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# Templates
# ─────────────────────────────────────────────────────────────────────────────


class VoiceTemplateViewSet(viewsets.ModelViewSet):
    serializer_class = VoiceTemplateSerializer
    permission_classes = [IsAuthenticated, IsVoiceEnabledForTenant]

    def get_queryset(self):
        return (
            VoiceTemplate.objects.filter(tenant__tenant_users__user=self.request.user)
            .order_by("-created_at")
            .distinct()
        )

    def perform_create(self, serializer):
        serializer.save(tenant=_user_tenant(self.request))

    @action(detail=True, methods=["post"], url_path="preview")
    def preview(self, request, pk=None):
        """Return what the rendered TTS body would look like for this template.

        Full audio synthesis lives behind the TTS service; for now we
        return the rendered text + voice/language so callers can sanity
        check before placing a call.
        """
        from voice.fallback import render_template

        template = self.get_object()
        variables = request.data.get("variables") or {}
        rendered = render_template(template.tts_text or "", variables)
        return Response(
            {
                "rendered_text": rendered,
                "tts_voice": template.tts_voice or None,
                "tts_language": template.tts_language or None,
                "audio_url": template.audio_url or None,
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# Recordings
# ─────────────────────────────────────────────────────────────────────────────


class VoiceRecordingViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = VoiceRecordingSerializer
    permission_classes = [IsAuthenticated, IsVoiceEnabledForTenant]

    def get_queryset(self):
        qs = (
            VoiceRecording.objects.filter(call__tenant__tenant_users__user=self.request.user)
            .order_by("-created_at")
            .distinct()
        )
        call_id = self.request.query_params.get("call_id")
        if call_id:
            qs = qs.filter(call_id=call_id)
        return qs

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        """Return a fresh signed URL with a caller-specified TTL.

        Query param: ``expires_seconds`` (default 3600, capped 86400).
        """
        from voice.recordings import storage

        recording = self.get_object()
        if not recording.storage_url:
            return Response({"error": "Recording has no stored audio."}, status=status.HTTP_404_NOT_FOUND)

        try:
            expires = int(request.query_params.get("expires_seconds") or 3600)
        except (TypeError, ValueError):
            expires = 3600
        expires = max(60, min(expires, 86400))

        try:
            url = storage.signed_url(recording.storage_url, expires_seconds=expires)
        except Exception as exc:  # noqa: BLE001 — surface as 502
            return Response({"error": f"Failed to sign URL: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({"recording_url": url, "expires_seconds": expires})


# ─────────────────────────────────────────────────────────────────────────────
# Rate cards (admin only)
# ─────────────────────────────────────────────────────────────────────────────


class VoiceRateCardViewSet(viewsets.ModelViewSet):
    serializer_class = VoiceRateCardSerializer
    permission_classes = [IsAuthenticated, IsVoiceEnabledForTenant, IsVoiceAdmin]

    def get_queryset(self):
        return (
            VoiceRateCard.objects.filter(provider_config__tenant__tenant_users__user=self.request.user)
            .select_related("provider_config")
            .order_by("provider_config", "-valid_from")
            .distinct()
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tenant voice app (one row per tenant — read + update only)
# ─────────────────────────────────────────────────────────────────────────────


class TenantVoiceAppViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = TenantVoiceAppSerializer
    permission_classes = [IsAuthenticated, IsVoiceEnabledForTenant]

    def get_queryset(self):
        return TenantVoiceApp.objects.filter(tenant__tenant_users__user=self.request.user).distinct()


# ─────────────────────────────────────────────────────────────────────────────
# Recording consent
# ─────────────────────────────────────────────────────────────────────────────


class RecordingConsentViewSet(viewsets.ModelViewSet):
    serializer_class = RecordingConsentSerializer
    permission_classes = [IsAuthenticated, IsVoiceEnabledForTenant]

    def get_queryset(self):
        return (
            RecordingConsent.objects.filter(tenant__tenant_users__user=self.request.user)
            .order_by("-created_at")
            .distinct()
        )

    def perform_create(self, serializer):
        serializer.save(tenant=_user_tenant(self.request))
