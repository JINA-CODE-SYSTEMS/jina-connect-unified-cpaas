"""Transcription provider interface (#169).

Three backends ship: Deepgram, OpenAI Whisper, and a provider-native
shim that delegates to the call's source provider (Twilio Transcription
/ Plivo STT / etc.). Selection is per-tenant via the
``VOICE_TRANSCRIPTION_PROVIDER`` setting.

The interface is intentionally narrow — ``transcribe(audio_bytes,
language)`` returns a ``TranscriptionResult``. The task layer
(``voice.transcription.tasks``) handles fetching audio from S3 and
persisting the result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TranscriptionResult:
    """The output of one transcription run."""

    text: str
    language: str
    confidence: float
    provider: str
    raw: dict[str, Any]


class TranscriptionError(Exception):
    """Raised when a backend can't produce a usable transcription."""


class TranscriptionProvider(ABC):
    """Abstract transcription backend.

    ``language`` is BCP-47 (e.g. ``en-IN``, ``hi-IN``). Providers that
    auto-detect should accept an empty / None language and surface what
    they picked in ``TranscriptionResult.language``.
    """

    name: str = ""

    @abstractmethod
    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Return a ``TranscriptionResult``. Raises ``TranscriptionError``."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Backend registry — populated by ``transcription/__init__.py`` so callers
# can do ``get_transcription_provider("deepgram")`` without importing every
# backend module.
# ─────────────────────────────────────────────────────────────────────────────


_BACKENDS: dict[str, type[TranscriptionProvider]] = {}


def register_transcription_backend(name: str, cls: type[TranscriptionProvider]) -> None:
    """Register a backend class under ``name``.

    Idempotent: re-registering the same class is a no-op (autoreload /
    worker-fork friendly). Re-registering a different class raises.
    """
    existing = _BACKENDS.get(name)
    if existing is not None:
        if existing is cls:
            return
        raise ValueError(
            f"Transcription backend {name!r} already registered as "
            f"{existing.__name__}; refusing to overwrite with {cls.__name__}"
        )
    _BACKENDS[name] = cls


def get_transcription_provider(name: str) -> TranscriptionProvider:
    """Instantiate the registered backend named ``name``.

    Raises ``KeyError`` when no backend matches — the task layer
    surfaces this as a logged warning so the recording lands without
    a transcript instead of crashing the worker.
    """
    cls = _BACKENDS.get(name)
    if cls is None:
        raise KeyError(f"Unknown transcription backend {name!r}. Registered: {sorted(_BACKENDS)}")
    return cls()


def _reset_transcription_backend_registry() -> None:
    """Test hook — clear all registrations."""
    _BACKENDS.clear()
