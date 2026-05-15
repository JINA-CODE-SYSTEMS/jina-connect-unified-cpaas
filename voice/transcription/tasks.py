"""Transcription Celery tasks (#169).

Hooks into the ``recording_created`` signal from
``voice.recordings.tasks``: when a recording lands in S3 the task layer
fetches the audio (or accepts it inline from the signal), runs the
configured backend, and updates the ``VoiceRecording`` row.

The configured backend comes from ``settings.VOICE_TRANSCRIPTION_PROVIDER``
(default ``deepgram``). Missing API keys or backend failures are logged
and swallowed — the recording is still usable without a transcript.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import shared_task
from django.conf import settings
from django.dispatch import receiver

from voice.recordings import storage
from voice.recordings.tasks import recording_created
from voice.transcription.base import (
    TranscriptionError,
    get_transcription_provider,
)

logger = logging.getLogger(__name__)


def _backend_name() -> str:
    return getattr(settings, "VOICE_TRANSCRIPTION_PROVIDER", "deepgram") or "deepgram"


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def transcribe_recording(self, recording_id: str, language: str | None = None) -> None:
    """Transcribe the audio for ``recording_id`` using the configured backend.

    Idempotent: if the row already has a transcript we no-op so a re-run
    doesn't overwrite a hand-edited one.
    """
    from voice.models import VoiceRecording

    try:
        recording = VoiceRecording.objects.select_related("call", "tenant").get(pk=UUID(recording_id))
    except VoiceRecording.DoesNotExist:
        logger.warning("[voice.transcription] recording %s not found", recording_id)
        return

    if recording.transcription:
        # Provider-native recordings already populate this on the webhook;
        # nothing to do.
        return

    backend_name = _backend_name()
    try:
        provider = get_transcription_provider(backend_name)
    except KeyError:
        logger.warning(
            "[voice.transcription] unknown backend %r — skipping recording %s",
            backend_name,
            recording_id,
        )
        return

    # Fetch the audio. ``storage_url`` is the bucket key, not a URL.
    try:
        audio_bytes = storage.fetch(recording.storage_url)
    except Exception as exc:  # noqa: BLE001 — retry transient S3 errors
        logger.warning("[voice.transcription] fetch failed for %s: %s", recording_id, exc)
        raise self.retry(exc=exc)

    try:
        result = provider.transcribe(audio_bytes, language=language)
    except TranscriptionError as exc:
        logger.info(
            "[voice.transcription] %s backend %s failed: %s",
            recording_id,
            backend_name,
            exc,
        )
        return

    recording.transcription = result.text
    recording.transcription_provider = result.provider
    recording.transcription_confidence = result.confidence
    recording.save(
        update_fields=[
            "transcription",
            "transcription_provider",
            "transcription_confidence",
            "updated_at",
        ]
    )


@receiver(recording_created)
def _on_recording_created(sender: Any, recording, audio_bytes: bytes | None = None, **kwargs) -> None:
    """Fan out a recording into the transcription task.

    Provider-native flows can skip this by not configuring a backend or
    by stamping ``transcription`` before saving — the task short-circuits
    on either signal.
    """
    if not getattr(settings, "VOICE_TRANSCRIPTION_PROVIDER", ""):
        return
    if recording.transcription:
        return
    language = recording.call.metadata.get("language") if recording.call else None
    transcribe_recording.delay(str(recording.id), language)
