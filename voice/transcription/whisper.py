"""OpenAI Whisper transcription backend (#169).

POSTs the audio as multipart ``file=`` to
``https://api.openai.com/v1/audio/transcriptions`` (``whisper-1``).

The response includes ``text`` and (with ``response_format=verbose_json``)
a per-segment ``avg_logprob``; we average those to a single confidence
proxy, since the API doesn't expose a top-level confidence score.
"""

from __future__ import annotations

import logging
import math

import requests
from django.conf import settings

from voice.transcription.base import (
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionResult,
    register_transcription_backend,
)

logger = logging.getLogger(__name__)

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-1"
DEFAULT_TIMEOUT = 60


class WhisperProvider(TranscriptionProvider):
    name = "whisper"

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        api_key = getattr(settings, "OPENAI_API_KEY", "") or ""
        if not api_key:
            raise TranscriptionError("OPENAI_API_KEY is not configured")

        data: dict[str, str] = {
            "model": DEFAULT_MODEL,
            "response_format": "verbose_json",
        }
        if language:
            # Whisper wants the 2-letter ISO code (en, hi). Strip
            # anything past the dash so "en-IN" still works.
            data["language"] = language.split("-")[0].lower()

        try:
            resp = requests.post(
                WHISPER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.mp3", audio_bytes, "audio/mpeg")},
                data=data,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise TranscriptionError(f"whisper request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise TranscriptionError(f"whisper returned {resp.status_code}: {resp.text[:200]}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise TranscriptionError("whisper returned non-JSON body") from exc

        text = payload.get("text", "") or ""
        detected = payload.get("language") or language or ""
        confidence = _avg_logprob_to_confidence(payload.get("segments") or [])

        return TranscriptionResult(
            text=text,
            language=detected,
            confidence=confidence,
            provider=self.name,
            raw=payload,
        )


def _avg_logprob_to_confidence(segments: list[dict]) -> float:
    """Average the per-segment avg_logprob into a 0..1 confidence proxy.

    Whisper segments expose ``avg_logprob`` (negative log-probability).
    We average them and squash through ``exp(x)`` to get a 0..1 number.
    With no segments we conservatively return 0.0 rather than 1.0.
    """
    logprobs: list[float] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        lp = seg.get("avg_logprob")
        if isinstance(lp, (int, float)):
            logprobs.append(float(lp))
    if not logprobs:
        return 0.0
    avg = sum(logprobs) / len(logprobs)
    try:
        return max(0.0, min(1.0, math.exp(avg)))
    except OverflowError:
        return 0.0


register_transcription_backend("whisper", WhisperProvider)
