"""Voice adapter abstract base class + shared dataclasses.

Every voice adapter — SIP (via Asterisk ARI), Twilio, Plivo, Vonage,
Telnyx, Exotel — implements ``VoiceAdapter``. The channel registry
(``jina_connect.channel_registry``) hands these out to callers based on
``PlatformChoices.VOICE`` and the tenant's default ``VoiceProviderConfig``.

The text-channel methods inherited from ``BaseChannelAdapter`` are not
meaningful for voice. Subclasses inherit no-op stubs that raise
``NotImplementedError`` with a clear message, and the ``capabilities``
dataclass advertises ``supports_text=False`` so callers don't try.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from jina_connect.platform_choices import PlatformChoices
from voice.constants import CallEventType
from wa.adapters.channel_base import BaseChannelAdapter, Capabilities

# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses passed between callers and adapters
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlayInstruction:
    """A single play step — either pre-recorded audio or TTS.

    Exactly one of ``audio_url`` or ``tts_text`` must be set. Validation
    happens at construction time at the call sites (and via the
    ``voice.play`` node validator from #168).
    """

    audio_url: str | None = None
    tts_text: str | None = None
    tts_voice: str | None = None
    tts_language: str | None = None


@dataclass(frozen=True)
class CallInstructions:
    """What the adapter should do when the call answers.

    Exactly one of ``flow_id``, ``static_play``, or ``forward_to`` is set.
    The adapter dispatches accordingly: flow → run IVR; static_play →
    play once then hang up; forward_to → connect to another URI.
    """

    flow_id: UUID | None = None
    static_play: PlayInstruction | None = None
    forward_to: str | None = None


@dataclass(frozen=True)
class ProviderCallHandle:
    """Handle returned by ``initiate_call`` once the provider accepts the call.

    ``provider_call_id`` is the upstream identifier (Twilio CallSid,
    Plivo CallUUID, SIP Call-ID). ``raw`` carries the full provider
    response so callers can persist it on ``VoiceCall.metadata``.
    """

    provider_call_id: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class NormalizedCallEvent:
    """Adapter-agnostic event shape produced by ``parse_webhook``.

    Adapters translate their provider-native event format (TwiML,
    Plivo XML, NCCO, Telnyx JSON, Exotel XML, Asterisk ARI) into this
    so downstream code (state machine, signals, billing) never has to
    care about the source.
    """

    provider_call_id: str
    event_type: CallEventType
    hangup_cause: str | None
    payload: dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# VoiceAdapter ABC
# ─────────────────────────────────────────────────────────────────────────────


class VoiceAdapter(BaseChannelAdapter):
    """Abstract base for every voice provider adapter.

    Subclasses MUST implement every abstract method below plus
    ``parse_webhook``. They override ``capabilities`` to declare which
    features they support so callers don't try, e.g., DTMF gather on a
    SIP trunk that lacks DTMF support.
    """

    platform = PlatformChoices.VOICE
    capabilities = Capabilities(
        supports_text=False,
        supports_voice_call=True,
    )

    # ── Text-channel methods inherited from BaseChannelAdapter ─────────
    # Voice doesn't send text in the messaging sense. We provide concrete
    # ``raise NotImplementedError`` overrides so subclasses don't have to,
    # and so abstract-method instantiation guards on the text side don't
    # bite voice adapters that only override the voice methods below.

    def send_text(self, chat_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("Voice adapters do not implement send_text.")

    def send_media(
        self,
        chat_id: str,
        media_type: str,
        media_url: str,
        caption: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError("Voice adapters do not implement send_media.")

    def send_keyboard(
        self,
        chat_id: str,
        text: str,
        keyboard: list,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError("Voice adapters do not implement send_keyboard.")

    # ── Voice-specific abstract surface ────────────────────────────────

    @abstractmethod
    def initiate_call(
        self,
        *,
        from_number: str,
        to_number: str,
        callback_url: str,
        instructions: CallInstructions,
    ) -> ProviderCallHandle:
        """Place an outbound call. Returns once the provider accepts it,
        not when the callee answers."""
        ...

    @abstractmethod
    def hangup(self, provider_call_id: str) -> None:
        """End an in-progress call."""
        ...

    @abstractmethod
    def play(
        self,
        provider_call_id: str,
        *,
        audio_url: str | None = None,
        tts_text: str | None = None,
        tts_voice: str | None = None,
    ) -> None:
        """Play audio (pre-recorded URL or TTS) on a live call."""
        ...

    @abstractmethod
    def gather_dtmf(
        self,
        provider_call_id: str,
        *,
        max_digits: int,
        timeout_seconds: int,
        finish_on_key: str | None = None,
    ) -> None:
        """Collect DTMF digits from the caller."""
        ...

    @abstractmethod
    def gather_speech(
        self,
        provider_call_id: str,
        *,
        language: str,
        timeout_seconds: int,
    ) -> None:
        """Collect speech input from the caller, where supported."""
        ...

    @abstractmethod
    def transfer(self, provider_call_id: str, *, to_uri: str) -> None:
        """Transfer the call to another URI (SIP REFER or provider-native)."""
        ...

    @abstractmethod
    def start_recording(self, provider_call_id: str) -> str:
        """Start recording a live call. Returns the provider's recording id."""
        ...

    @abstractmethod
    def stop_recording(self, provider_call_id: str, provider_recording_id: str) -> None:
        """Stop a previously started recording."""
        ...

    @abstractmethod
    def fetch_recording(self, provider_recording_id: str) -> bytes:
        """Download recording audio bytes from the provider."""
        ...

    @abstractmethod
    def parse_webhook(self, request) -> NormalizedCallEvent:
        """Parse an incoming provider webhook into a normalised event."""
        ...
