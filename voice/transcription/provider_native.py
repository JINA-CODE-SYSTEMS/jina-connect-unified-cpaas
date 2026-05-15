"""Provider-native transcription shim (#169).

Some providers (Twilio Transcription, Plivo Speech-to-Text, Exotel) hand
back a transcript on the recording webhook itself; in that case running
a second pass through Deepgram / Whisper is wasted spend.

The shim is a placeholder — actual extraction lives on the per-provider
recording webhooks, which write directly to
``VoiceRecording.transcription`` when the payload carries one. This
backend's ``transcribe`` simply raises ``TranscriptionError`` so the task
layer knows to surface a "no transcript" log line instead of attempting
a real HTTP call. We keep the registration so tenants can pick
``provider_native`` in settings and skip third-party transcription
entirely.
"""

from __future__ import annotations

import logging

from voice.transcription.base import (
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionResult,
    register_transcription_backend,
)

logger = logging.getLogger(__name__)


class ProviderNativeProvider(TranscriptionProvider):
    name = "provider_native"

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        raise TranscriptionError(
            "provider_native transcription is webhook-driven; the recording "
            "webhook should write the transcript directly when the provider "
            "supplies one."
        )


register_transcription_backend("provider_native", ProviderNativeProvider)
