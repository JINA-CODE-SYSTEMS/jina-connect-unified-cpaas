"""Exotel Voice API adapter (#167).

Auth: HTTP Basic (``api_key`` / ``api_token``).
Base URL: ``https://{subdomain}/v1/Accounts/{sid}/`` where subdomain
is ``api.exotel.com`` (US) or ``api.in.exotel.com`` (India).

Exotel doesn't sign incoming webhooks — verification relies on either
IP allowlisting at the edge (production) or a path-embedded token
configured via ``inbound_webhook_token`` in credentials. The webhook
handler enforces the latter.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

import requests

from voice.adapters.base import (
    CallInstructions,
    NormalizedCallEvent,
    ProviderCallHandle,
)
from voice.adapters.http_voice.base import HttpVoiceAdapter
from voice.adapters.registry import register_voice_adapter
from voice.constants import (
    CallEventType,
    CallStatus,
    HangupCause,
    VoiceProvider,
)
from wa.adapters.channel_base import Capabilities

logger = logging.getLogger(__name__)


class ExotelVoiceAdapter(HttpVoiceAdapter):
    """Adapter for Exotel Voice API."""

    capabilities = Capabilities(
        supports_text=False,
        supports_voice_call=True,
        supports_recording=True,
        supports_dtmf_gather=True,
        supports_speech_gather=False,  # no native speech recognition
        supports_call_transfer=True,
        supports_provider_cost=True,
    )

    DEFAULT_SUBDOMAIN = "api.exotel.com"

    STATUS_MAP = {
        "queued": CallStatus.QUEUED,
        "in-progress": CallStatus.IN_PROGRESS,
        "completed": CallStatus.COMPLETED,
        "failed": CallStatus.FAILED,
        "busy": CallStatus.FAILED,
        "no-answer": CallStatus.FAILED,
        "canceled": CallStatus.CANCELED,
    }

    STATUS_TO_HANGUP_CAUSE = {
        "completed": HangupCause.NORMAL_CLEARING,
        "busy": HangupCause.USER_BUSY,
        "no-answer": HangupCause.NO_ANSWER,
        "failed": HangupCause.NORMAL_TEMPORARY_FAILURE,
        "canceled": HangupCause.NORMAL_CLEARING,
    }

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _auth(self) -> tuple[str, str]:
        return (self.credentials["api_key"], self.credentials["api_token"])

    def _account_base(self) -> str:
        subdomain = self.credentials.get("subdomain") or self.DEFAULT_SUBDOMAIN
        sid = self.credentials["sid"]
        return f"https://{subdomain}/v1/Accounts/{sid}"

    # ─── VoiceAdapter implementations ───────────────────────────────────

    def initiate_call(
        self,
        *,
        from_number: str,
        to_number: str,
        callback_url: str,
        instructions: CallInstructions,
    ) -> ProviderCallHandle:
        """Place an outbound call via ``POST /v1/Accounts/{sid}/Calls/connect``.

        Exotel uses form-urlencoded bodies (not JSON). The
        ``CallerId`` is the verified DID and ``From`` is the dialed
        number — opposite to Twilio/Plivo. We expose the conventional
        ``from_number`` / ``to_number`` and remap here.
        """
        data = {
            "From": to_number,  # who to dial
            "CallerId": from_number,  # which DID to show
            "Url": callback_url,
            "StatusCallback": callback_url.rstrip("/") + "/../status/",
        }
        resp = requests.post(
            f"{self._account_base()}/Calls/connect.json",
            data=urlencode(data),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=self._auth(),
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        call = body.get("Call") or {}
        return ProviderCallHandle(provider_call_id=call.get("Sid", ""), raw=body)

    def hangup(self, provider_call_id: str) -> None:
        # Exotel doesn't expose a clean live-hangup endpoint; the
        # common pattern is to POST a hangup TwiML to the call's URL
        # by re-fetching. For this PR we surface a clear error since
        # Exotel deployments typically end calls naturally via TwiML
        # or call timeout.
        raise NotImplementedError(
            "Exotel does not expose a programmatic hangup endpoint; use TwiML <Hangup/> in the answer response instead."
        )

    def play(
        self,
        provider_call_id: str,
        *,
        audio_url: str | None = None,
        tts_text: str | None = None,
        tts_voice: str | None = None,
    ) -> None:
        """Exotel doesn't support mid-call play injection — all media
        is controlled via the answer XML. No-op + log."""
        logger.info(
            "[ExotelVoiceAdapter.play] mid-call play not supported (audio_url=%s, tts_text=%s)",
            bool(audio_url),
            bool(tts_text),
        )

    def gather_dtmf(
        self,
        provider_call_id: str,
        *,
        max_digits: int,
        timeout_seconds: int,
        finish_on_key: str | None = None,
    ) -> None:
        """Exotel gathers DTMF declaratively via <Gather> in the answer
        XML; no live REST poke. Full IVR loop lands with #168."""
        logger.info(
            "[ExotelVoiceAdapter.gather_dtmf] call=%s max_digits=%d",
            provider_call_id,
            max_digits,
        )

    def gather_speech(
        self,
        provider_call_id: str,
        *,
        language: str,
        timeout_seconds: int,
    ) -> None:
        raise NotImplementedError(
            "Exotel does not provide native speech recognition; use <Record> + transcription (#169) instead."
        )

    def transfer(self, provider_call_id: str, *, to_uri: str) -> None:
        """Same constraint as hangup: transfers happen via ExoML <Dial>
        in the answer response, not via REST. No-op + log."""
        logger.info(
            "[ExotelVoiceAdapter.transfer] call=%s to=%s — transfers happen via ExoML <Dial>",
            provider_call_id,
            to_uri,
        )

    def start_recording(self, provider_call_id: str) -> str:
        """Recordings start declaratively via <Record> in the answer
        XML, not via REST. Returns a deterministic name so the
        recording-status webhook can correlate."""
        return f"call-{provider_call_id}"

    def stop_recording(self, provider_call_id: str, provider_recording_id: str) -> None:
        # Exotel recordings stop on silence / maxLength / endOnKey;
        # no REST stop endpoint.
        return

    def fetch_recording(self, provider_recording_id: str) -> bytes:
        """Exotel recordings live at a CDN URL surfaced via the
        recording-status webhook. Callers pass the recording URL here
        (we store it on ``VoiceRecording.provider_recording_id`` so
        the download task can refetch).
        """
        if not (provider_recording_id.startswith("http://") or provider_recording_id.startswith("https://")):
            # If we got a SID, fetch metadata first.
            meta = requests.get(
                f"{self._account_base()}/Calls/{provider_recording_id}.json",
                auth=self._auth(),
                timeout=15,
            )
            meta.raise_for_status()
            data = meta.json()
            url = (data.get("Call") or {}).get("RecordingUrl", "")
            if not url:
                raise ValueError(f"Exotel call {provider_recording_id} has no RecordingUrl")
            provider_recording_id = url
        audio = self.session.get(provider_recording_id, auth=self._auth(), timeout=30)
        audio.raise_for_status()
        return audio.content

    # ─── Webhook verification + parsing ─────────────────────────────────

    def verify_webhook(self, request) -> bool:
        """Path-token verification.

        Exotel doesn't sign webhooks. Instead the URL itself carries a
        per-tenant secret: ``/voice/v1/webhooks/exotel/<config_uuid>/...?token=<token>``
        (or in the path itself, matched in URL routing). The webhook
        handler hands us the token via ``request.GET["token"]`` /
        ``request.POST["token"]`` and we compare it to
        ``inbound_webhook_token`` from credentials. If no token is
        configured, accept (deployment relies on IP allowlisting).
        """
        expected = self.provider_config.inbound_webhook_token
        if not expected:
            return True

        provided = request.GET.get("token") or request.POST.get("token") or ""
        import hmac as _hmac

        return _hmac.compare_digest(provided, expected)

    def parse_webhook(self, request) -> NormalizedCallEvent:
        # Exotel posts form-encoded bodies. Capture both POST + GET so
        # status callbacks (sometimes GET-style) and passthru POSTs all work.
        params = {**request.GET.dict(), **request.POST.dict()}
        call_sid = params.get("CallSid", "")
        exotel_status = params.get("Status") or params.get("CallStatus") or ""
        canonical_status = self._normalize_status(exotel_status)

        hangup_cause: str | None
        if canonical_status in (
            CallStatus.COMPLETED,
            CallStatus.FAILED,
            CallStatus.CANCELED,
        ):
            hangup_cause = self.STATUS_TO_HANGUP_CAUSE.get(exotel_status, HangupCause.UNKNOWN)
        else:
            hangup_cause = None

        event_type_map = {
            CallStatus.QUEUED: CallEventType.INITIATED,
            CallStatus.IN_PROGRESS: CallEventType.ANSWERED,
            CallStatus.COMPLETED: CallEventType.COMPLETED,
            CallStatus.FAILED: CallEventType.FAILED,
            CallStatus.CANCELED: CallEventType.FAILED,
        }
        event_type = event_type_map.get(canonical_status, CallEventType.INITIATED)

        return NormalizedCallEvent(
            provider_call_id=call_sid,
            event_type=event_type,
            hangup_cause=hangup_cause,
            payload=params,
        )


register_voice_adapter(VoiceProvider.EXOTEL.value, ExotelVoiceAdapter)
