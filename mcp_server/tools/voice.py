"""MCP voice tools (#173).

Seven tools so AI agents can place, monitor, end, and follow up on
voice calls without touching the REST layer directly:

  * ``voice_initiate_call``
  * ``voice_get_call_status``
  * ``voice_list_calls``
  * ``voice_get_recording``
  * ``voice_get_transcription``
  * ``voice_hangup_call``
  * ``voice_trigger_broadcast``

All tools resolve the tenant from the API key (``resolve_tenant``) and
gate on ``TenantVoiceApp.is_enabled`` so a tenant without voice
provisioning gets a clean error instead of a 500. We never honour a
caller-provided ``tenant_id`` — the API key is the only trust anchor.
"""

from __future__ import annotations

from typing import Optional

from mcp_server.auth import resolve_tenant
from mcp_server.server import mcp

# ─────────────────────────────────────────────────────────────────────────────
# Tenant scoping + voice-enabled gate
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_voice_tenant(api_key: str):
    """Return ``(tenant, voice_app)`` or ``(None, error_dict)``.

    Callers check ``if voice_app is None:`` then return the error dict
    unchanged. Keeping the pattern tight per-tool avoids a thrown
    exception leaking provider-side details.
    """
    from tenants.models import TenantVoiceApp

    try:
        tenant, _ = resolve_tenant(api_key)
    except ValueError as exc:
        return None, {"error": str(exc)}

    try:
        voice_app = TenantVoiceApp.objects.get(tenant=tenant)
    except TenantVoiceApp.DoesNotExist:
        return None, {"error": "Voice is not provisioned for this tenant."}

    if not voice_app.is_enabled:
        return None, {"error": "Voice is disabled for this tenant. Enable it on the tenant voice app."}

    return tenant, voice_app


def _serialise_call(call) -> dict:
    return {
        "call_id": str(call.id),
        "provider_call_id": call.provider_call_id,
        "direction": call.direction,
        "status": call.status,
        "from_number": call.from_number,
        "to_number": call.to_number,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "duration_seconds": call.duration_seconds,
        "hangup_cause": call.hangup_cause or None,
        "cost_amount": str(call.cost_amount) if call.cost_amount is not None else None,
        "cost_currency": call.cost_currency or None,
        "cost_source": call.cost_source or None,
        "contact_id": str(call.contact_id) if call.contact_id else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# voice_initiate_call
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool()
def voice_initiate_call(
    api_key: str,
    to_number: str,
    from_number: Optional[str] = None,
    flow_id: Optional[str] = None,
    tts_text: Optional[str] = None,
    provider_config_id: Optional[str] = None,
) -> dict:
    """Place an outbound voice call.

    Exactly one of ``flow_id`` or ``tts_text`` must be provided.

    Args:
        api_key: Your Jina Connect API key.
        to_number: Recipient E.164 number (e.g. +14155550199).
        from_number: Optional sender number; defaults to the first
            ``from_numbers`` entry on the resolved config.
        flow_id: Run this IVR flow on the call (see chat_flow).
        tts_text: One-shot TTS playback; mutually exclusive with flow_id.
        provider_config_id: Override the tenant's default outbound config.
    """
    from uuid import UUID

    from contacts.models import TenantContact
    from voice.constants import CallDirection, CallStatus
    from voice.models import VoiceCall, VoiceProviderConfig

    tenant, voice_app_or_err = _resolve_voice_tenant(api_key)
    if tenant is None:
        return voice_app_or_err

    if bool(flow_id) == bool(tts_text):
        return {"error": "Provide exactly one of flow_id or tts_text."}

    # Resolve outbound provider config.
    config = None
    if provider_config_id:
        try:
            config = VoiceProviderConfig.objects.get(pk=UUID(provider_config_id), tenant=tenant)
        except (VoiceProviderConfig.DoesNotExist, ValueError):
            return {"error": f"Provider config {provider_config_id} not found for this tenant."}
    else:
        if voice_app_or_err.default_outbound_config_id:
            config = voice_app_or_err.default_outbound_config
        if config is None:
            config = VoiceProviderConfig.objects.filter(tenant=tenant, enabled=True).order_by("-priority").first()
    if config is None:
        return {"error": "No active VoiceProviderConfig for this tenant."}

    if from_number is None:
        from_number = (config.from_numbers or [None])[0]
    if not from_number:
        return {"error": f"VoiceProviderConfig {config.id} has no from_numbers configured."}

    contact, _ = TenantContact.objects.get_or_create(
        tenant=tenant,
        phone=to_number,
        defaults={"first_name": to_number, "source": "VOICE"},
    )

    metadata = {}
    if tts_text:
        metadata["static_play"] = {"tts_text": tts_text}

    # Placeholder ``provider_call_id`` must be unique per provider
    # config (unique constraint), so use a fresh UUID rather than the
    # contact id — otherwise concurrent dials to the same contact
    # collide.
    import uuid as _uuid

    call = VoiceCall.objects.create(
        tenant=tenant,
        name=f"mcp-{to_number}",
        provider_config=config,
        provider_call_id=f"pending-{_uuid.uuid4()}",
        direction=CallDirection.OUTBOUND,
        from_number=str(from_number),
        to_number=to_number,
        contact=contact,
        status=CallStatus.QUEUED,
        metadata=metadata,
    )

    from voice.tasks import initiate_call as voice_initiate_call_task

    voice_initiate_call_task.delay(str(call.id))

    return {
        "call_id": str(call.id),
        "status": call.status,
        "provider_call_id": call.provider_call_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# voice_get_call_status
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool()
def voice_get_call_status(api_key: str, call_id: str) -> dict:
    """Return the full ``VoiceCall`` row + its event log.

    Args:
        api_key: Your Jina Connect API key.
        call_id: VoiceCall UUID returned by ``voice_initiate_call``.
    """
    from uuid import UUID

    from voice.models import VoiceCall

    tenant, err = _resolve_voice_tenant(api_key)
    if tenant is None:
        return err

    try:
        call = (
            VoiceCall.objects.select_related("provider_config")
            .prefetch_related("events")
            .get(
                pk=UUID(call_id),
                tenant=tenant,
            )
        )
    except (VoiceCall.DoesNotExist, ValueError):
        return {"error": f"VoiceCall {call_id} not found for this tenant."}

    payload = _serialise_call(call)
    payload["events"] = [
        {
            "sequence": ev.sequence,
            "event_type": ev.event_type,
            "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
        }
        for ev in call.events.all().order_by("sequence")
    ]
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# voice_list_calls
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool()
def voice_list_calls(
    api_key: str,
    contact_id: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    started_at_from: Optional[str] = None,
    started_at_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List voice calls with optional filters.

    Args:
        api_key: Your Jina Connect API key.
        contact_id: Only calls for this TenantContact UUID.
        status: Filter by VoiceCall status (QUEUED / IN_PROGRESS / COMPLETED / …).
        direction: ``outbound`` or ``inbound``.
        started_at_from: ISO timestamp lower bound (inclusive).
        started_at_to: ISO timestamp upper bound (inclusive).
        limit: Page size (default 50, max 200).
        offset: Pagination offset.
    """
    from datetime import datetime
    from uuid import UUID

    from voice.models import VoiceCall

    tenant, err = _resolve_voice_tenant(api_key)
    if tenant is None:
        return err

    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    qs = VoiceCall.objects.filter(tenant=tenant).order_by("-started_at", "-created_at")
    if contact_id:
        try:
            qs = qs.filter(contact_id=UUID(contact_id))
        except ValueError:
            return {"error": f"Invalid contact_id: {contact_id!r}"}
    if status:
        qs = qs.filter(status=status)
    if direction:
        qs = qs.filter(direction=direction.lower())
    if started_at_from:
        try:
            qs = qs.filter(started_at__gte=datetime.fromisoformat(started_at_from))
        except ValueError:
            return {"error": f"Invalid started_at_from: {started_at_from!r}"}
    if started_at_to:
        try:
            qs = qs.filter(started_at__lte=datetime.fromisoformat(started_at_to))
        except ValueError:
            return {"error": f"Invalid started_at_to: {started_at_to!r}"}

    total = qs.count()
    rows = [_serialise_call(call) for call in qs[offset : offset + limit]]
    return {"count": total, "limit": limit, "offset": offset, "calls": rows}


# ─────────────────────────────────────────────────────────────────────────────
# voice_get_recording
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool()
def voice_get_recording(api_key: str, call_id: str, expires_seconds: int = 3600) -> dict:
    """Return a presigned URL for the call's recording.

    Args:
        api_key: Your Jina Connect API key.
        call_id: VoiceCall UUID.
        expires_seconds: Lifetime of the signed URL (default 1 hour).
    """
    from uuid import UUID

    from voice.models import VoiceCall, VoiceRecording
    from voice.recordings import storage

    tenant, err = _resolve_voice_tenant(api_key)
    if tenant is None:
        return err

    try:
        call = VoiceCall.objects.get(pk=UUID(call_id), tenant=tenant)
    except (VoiceCall.DoesNotExist, ValueError):
        return {"error": f"VoiceCall {call_id} not found for this tenant."}

    recording = VoiceRecording.objects.filter(call=call).exclude(storage_url="").order_by("-created_at").first()
    if recording is None:
        return {"call_id": str(call.id), "recording_url": None}

    try:
        url = storage.signed_url(recording.storage_url, expires_seconds=int(expires_seconds))
    except Exception as exc:  # noqa: BLE001 — surface as a tool-level error
        return {"error": f"Failed to sign recording URL: {exc}"}

    return {
        "call_id": str(call.id),
        "recording_id": str(recording.id),
        "recording_url": url,
        "expires_seconds": int(expires_seconds),
        "format": recording.format,
        "duration_seconds": recording.duration_seconds,
    }


# ─────────────────────────────────────────────────────────────────────────────
# voice_get_transcription
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool()
def voice_get_transcription(api_key: str, call_id: str) -> dict:
    """Return the transcription for the call, or null if not ready.

    Args:
        api_key: Your Jina Connect API key.
        call_id: VoiceCall UUID.
    """
    from uuid import UUID

    from voice.models import VoiceCall, VoiceRecording

    tenant, err = _resolve_voice_tenant(api_key)
    if tenant is None:
        return err

    try:
        call = VoiceCall.objects.get(pk=UUID(call_id), tenant=tenant)
    except (VoiceCall.DoesNotExist, ValueError):
        return {"error": f"VoiceCall {call_id} not found for this tenant."}

    recording = VoiceRecording.objects.filter(call=call).exclude(transcription="").order_by("-created_at").first()
    if recording is None:
        return {"call_id": str(call.id), "transcription": None}

    return {
        "call_id": str(call.id),
        "recording_id": str(recording.id),
        "text": recording.transcription,
        "provider": recording.transcription_provider or None,
        "confidence": recording.transcription_confidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# voice_hangup_call
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool()
def voice_hangup_call(api_key: str, call_id: str) -> dict:
    """End an in-progress call.

    Args:
        api_key: Your Jina Connect API key.
        call_id: VoiceCall UUID.
    """
    from uuid import UUID

    from voice.adapters.registry import get_voice_adapter_cls
    from voice.constants import TERMINAL_STATUSES
    from voice.models import VoiceCall

    tenant, err = _resolve_voice_tenant(api_key)
    if tenant is None:
        return err

    try:
        call = VoiceCall.objects.select_related("provider_config").get(pk=UUID(call_id), tenant=tenant)
    except (VoiceCall.DoesNotExist, ValueError):
        return {"error": f"VoiceCall {call_id} not found for this tenant."}

    if call.status in TERMINAL_STATUSES:
        return {"call_id": str(call.id), "status": call.status, "hung_up": False}

    try:
        adapter_cls = get_voice_adapter_cls(call.provider_config.provider)
    except NotImplementedError as exc:
        return {"error": str(exc)}

    adapter = adapter_cls(call.provider_config)
    try:
        adapter.hangup(call.provider_call_id)
    except Exception as exc:  # noqa: BLE001 — surface tool-level
        return {"error": f"Hangup failed: {exc}"}

    return {"call_id": str(call.id), "hung_up": True}


# ─────────────────────────────────────────────────────────────────────────────
# voice_trigger_broadcast
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool()
def voice_trigger_broadcast(api_key: str, broadcast_id: str) -> dict:
    """Dispatch a VOICE-platform broadcast.

    The broadcast must be ``platform=VOICE`` and have recipients
    attached. The dispatcher loop runs asynchronously — this tool
    returns the recipient count and the Celery task id you can poll
    via ``get_broadcast_status``.

    Args:
        api_key: Your Jina Connect API key.
        broadcast_id: Broadcast UUID/ID.
    """
    from broadcast.models import Broadcast, BroadcastMessage, BroadcastPlatformChoices

    tenant, err = _resolve_voice_tenant(api_key)
    if tenant is None:
        return err

    try:
        broadcast = Broadcast.objects.get(pk=broadcast_id, tenant=tenant)
    except Broadcast.DoesNotExist:
        return {"error": f"Broadcast {broadcast_id} not found for this tenant."}
    except ValueError:
        return {"error": f"Invalid broadcast_id: {broadcast_id!r}"}

    if broadcast.platform != BroadcastPlatformChoices.VOICE:
        return {"error": f"Broadcast {broadcast_id} is not a VOICE broadcast (platform={broadcast.platform})."}

    message_ids = list(BroadcastMessage.objects.filter(broadcast=broadcast).values_list("id", flat=True))
    if not message_ids:
        return {"error": "Broadcast has no recipients."}

    from broadcast.tasks import process_broadcast_messages_batch

    task = process_broadcast_messages_batch.delay(message_ids)

    return {
        "broadcast_id": str(broadcast.id),
        "recipient_count": len(message_ids),
        "task_id": task.id,
    }
