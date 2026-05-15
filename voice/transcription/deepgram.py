"""Deepgram transcription backend (#169).

Posts raw audio bytes to ``https://api.deepgram.com/v1/listen`` and
maps the ``results.channels[0].alternatives[0]`` payload to a
``TranscriptionResult``.

Auth is the ``Token <DEEPGRAM_API_KEY>`` header. The language hint is
forwarded as the ``language`` query param when provided; otherwise we
let Deepgram auto-detect.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from voice.transcription.base import (
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionResult,
    register_transcription_backend,
)

logger = logging.getLogger(__name__)

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
DEFAULT_TIMEOUT = 30


class DeepgramProvider(TranscriptionProvider):
    name = "deepgram"

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        api_key = getattr(settings, "DEEPGRAM_API_KEY", "") or ""
        if not api_key:
            raise TranscriptionError("DEEPGRAM_API_KEY is not configured")

        params: dict[str, str] = {"smart_format": "true"}
        if language:
            params["language"] = language
        else:
            params["detect_language"] = "true"

        try:
            resp = requests.post(
                DEEPGRAM_URL,
                params=params,
                data=audio_bytes,
                headers={
                    "Authorization": f"Token {api_key}",
                    "Content-Type": "audio/*",
                },
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise TranscriptionError(f"deepgram request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise TranscriptionError(f"deepgram returned {resp.status_code}: {resp.text[:200]}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise TranscriptionError("deepgram returned non-JSON body") from exc

        try:
            channel = payload["results"]["channels"][0]
            alt = channel["alternatives"][0]
            text = alt.get("transcript", "") or ""
            confidence = float(alt.get("confidence", 0.0) or 0.0)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise TranscriptionError(f"deepgram payload missing transcript: {exc}") from exc

        detected = channel.get("detected_language") or language or ""

        return TranscriptionResult(
            text=text,
            language=detected,
            confidence=confidence,
            provider=self.name,
            raw=payload,
        )


register_transcription_backend("deepgram", DeepgramProvider)
