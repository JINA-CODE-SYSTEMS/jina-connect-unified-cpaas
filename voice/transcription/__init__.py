"""Voice transcription package (#169).

Importing this module registers the three shipped backends:

  * ``deepgram``        — Deepgram Listen REST endpoint
  * ``whisper``         — OpenAI Whisper (audio transcriptions API)
  * ``provider_native`` — best-effort delegation to the source provider

Use ``voice.transcription.base.get_transcription_provider(name)`` to
look one up.
"""

from __future__ import annotations

from voice.transcription import deepgram, provider_native, whisper  # noqa: F401

__all__ = ["deepgram", "whisper", "provider_native"]
