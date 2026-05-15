"""Voice recording Celery tasks (#161).

Two tasks:

  * ``download_recording`` — pulls the recording from the provider once
    Twilio's ``recording-status=completed`` webhook fires. Stores to S3,
    creates a ``VoiceRecording`` row, fires ``recording.created`` so
    transcription (#169) can hook in.

  * ``enforce_retention`` — daily sweep that hard-deletes
    ``VoiceRecording`` rows past ``retention_expires_at``. Scheduled via
    Celery beat so the platform doesn't accumulate audio forever.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

import django.dispatch
from celery import shared_task
from django.utils import timezone

from voice.recordings import storage

logger = logging.getLogger(__name__)


# Fires after a ``VoiceRecording`` row is persisted + the audio sits in
# S3. Transcription (#169) and any future analytics hook here.
recording_created = django.dispatch.Signal()


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_recording(self, call_id: str, provider_recording_id: str) -> None:
    """Download the recording from the provider, upload to S3, persist row.

    Queued by the provider's recording-status webhook handler once the
    provider says the recording is ready. Idempotent: a re-run with the
    same ``provider_recording_id`` no-ops once the row exists.
    """
    from voice.models import VoiceCall, VoiceRecording

    try:
        call = VoiceCall.objects.select_related("provider_config", "tenant").get(pk=UUID(call_id))
    except VoiceCall.DoesNotExist:
        logger.warning("[voice.recordings.download_recording] call %s not found", call_id)
        return

    # Idempotent — a re-fired webhook shouldn't double-store.
    if VoiceRecording.objects.filter(call=call, provider_recording_id=provider_recording_id).exists():
        return

    from voice.adapters.registry import get_voice_adapter_cls

    adapter_cls = get_voice_adapter_cls(call.provider_config.provider)
    adapter = adapter_cls(call.provider_config)

    try:
        audio_bytes = adapter.fetch_recording(provider_recording_id)
    except Exception as exc:  # noqa: BLE001 — retry transient provider errors
        logger.warning(
            "[voice.recordings.download_recording] fetch failed for %s/%s: %s",
            call_id,
            provider_recording_id,
            exc,
        )
        raise self.retry(exc=exc)

    # Default to mp3 — Twilio recordings are mp3-served; the per-provider
    # format ends up encoded in the bytes regardless.
    fmt = "mp3"
    storage_key = storage.make_storage_key(call.tenant_id, call.id, provider_recording_id, fmt)
    storage.upload(storage_key, audio_bytes, content_type="audio/mpeg")

    # Retention window: configured at the tenant level on TenantVoiceApp;
    # falls back to a sensible 90-day default if voice isn't fully
    # provisioned yet.
    retention_days = _resolve_retention_days(call.tenant_id)
    expires_at = timezone.now() + timedelta(days=retention_days)

    recording = VoiceRecording.objects.create(
        call=call,
        name=f"recording-{provider_recording_id}",
        provider_recording_id=provider_recording_id,
        storage_url=storage_key,
        duration_seconds=call.duration_seconds or 0,
        size_bytes=len(audio_bytes),
        format=fmt,
        retention_expires_at=expires_at,
    )

    # Mirror onto the call so admin / inbox queries don't have to JOIN.
    call.recording_url = storage_key
    call.recording_duration_seconds = call.duration_seconds
    call.save(update_fields=["recording_url", "recording_duration_seconds", "updated_at"])

    recording_created.send(sender=VoiceRecording, recording=recording, audio_bytes=audio_bytes)


def _resolve_retention_days(tenant_id) -> int:
    """Look up the tenant's recording-retention setting; fall back to 90."""
    from tenants.models import TenantVoiceApp

    try:
        app = TenantVoiceApp.objects.get(tenant_id=tenant_id)
    except TenantVoiceApp.DoesNotExist:
        return 90
    return app.recording_retention_days


# ─────────────────────────────────────────────────────────────────────────────
# Retention sweep
# ─────────────────────────────────────────────────────────────────────────────


@shared_task
def enforce_retention(batch_size: int = 500) -> int:
    """Hard-delete recordings whose retention window has expired.

    Returns the number of recordings deleted. Designed to be safe to
    run repeatedly; processes in ``batch_size`` chunks so a backlog
    doesn't lock the table or balloon worker memory.

    Wire via Celery beat once per day at a low-traffic hour.
    """
    from voice.models import VoiceRecording

    now = timezone.now()
    qs = VoiceRecording.objects.filter(retention_expires_at__lt=now).order_by("retention_expires_at")

    deleted_count = 0
    while True:
        batch = list(qs[:batch_size])
        if not batch:
            break
        for rec in batch:
            if rec.storage_url:
                storage.delete(rec.storage_url)
            rec.delete()
            deleted_count += 1

    if deleted_count:
        logger.info(
            "[voice.recordings.enforce_retention] hard-deleted %d recording(s)",
            deleted_count,
        )
    return deleted_count
