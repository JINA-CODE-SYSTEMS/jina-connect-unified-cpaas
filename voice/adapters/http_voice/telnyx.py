"""Telnyx Call Control adapter (#166).

Auth: ``Authorization: Bearer <api_key>``.
Base URL: ``https://api.telnyx.com/v2/``.

Call Control is **command-driven**: every step (play / gather /
record / transfer / hangup) is a separate POST to
``/v2/calls/{call_control_id}/actions/<verb>``. The dialect emitters
produce these (verb, payload) tuples; this adapter runs each one.

Webhook signing: Ed25519 over ``timestamp + "|" + raw_body``,
detached signature in ``Telnyx-Signature-Ed25519`` (base64), timestamp
in ``Telnyx-Timestamp``. Verified with the application's public key
(stored as ``public_key`` in credentials — Telnyx publishes a
per-account public key separate from the API key).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

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


class TelnyxVoiceAdapter(HttpVoiceAdapter):
    """Adapter for Telnyx Call Control."""

    capabilities = Capabilities(
        supports_text=False,
        supports_voice_call=True,
        supports_recording=True,
        supports_dtmf_gather=True,
        supports_speech_gather=True,
        supports_call_transfer=True,
        supports_provider_cost=True,
    )

    API_BASE = "https://api.telnyx.com/v2"

    # Telnyx event_type values per Call Control docs.
    EVENT_TYPE_MAP = {
        "call.initiated": (CallStatus.INITIATING, CallEventType.INITIATED),
        "call.ringing": (CallStatus.RINGING, CallEventType.RINGING),
        "call.answered": (CallStatus.IN_PROGRESS, CallEventType.ANSWERED),
        "call.hangup": (CallStatus.COMPLETED, CallEventType.COMPLETED),
        "call.dtmf.received": (CallStatus.IN_PROGRESS, CallEventType.DTMF),
        "call.speak.ended": (CallStatus.IN_PROGRESS, CallEventType.INITIATED),
        "call.playback.ended": (CallStatus.IN_PROGRESS, CallEventType.INITIATED),
        "call.recording.saved": (CallStatus.IN_PROGRESS, CallEventType.RECORDING_COMPLETED),
        "call.transferred": (CallStatus.IN_PROGRESS, CallEventType.TRANSFERRED),
    }

    # Telnyx ``hangup_cause`` values → canonical.
    HANGUP_CAUSE_MAP = {
        "normal_clearing": HangupCause.NORMAL_CLEARING,
        "user_busy": HangupCause.USER_BUSY,
        "no_user_response": HangupCause.NO_USER_RESPONSE,
        "no_answer": HangupCause.NO_ANSWER,
        "call_rejected": HangupCause.CALL_REJECTED,
        "unallocated_number": HangupCause.NUMBER_UNALLOCATED,
        "network_out_of_order": HangupCause.NETWORK_OUT_OF_ORDER,
        "originator_cancel": HangupCause.NORMAL_CLEARING,
    }

    # ─── Auth ───────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.credentials['api_key']}",
            "Content-Type": "application/json",
        }

    def _action_url(self, call_control_id: str, verb: str) -> str:
        return f"{self.API_BASE}/calls/{call_control_id}/actions/{verb}"

    def _post(self, url: str, payload: dict | None = None) -> requests.Response:
        resp = requests.post(
            url,
            json=payload or {},
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp

    # ─── VoiceAdapter implementations ───────────────────────────────────

    def initiate_call(
        self,
        *,
        from_number: str,
        to_number: str,
        callback_url: str,
        instructions: CallInstructions,
    ) -> ProviderCallHandle:
        body: dict[str, Any] = {
            "to": to_number,
            "from": from_number,
            "connection_id": self.credentials["connection_id"],
            "webhook_url": callback_url,
            "webhook_url_method": "POST",
        }
        outbound_profile = self.credentials.get("outbound_voice_profile_id")
        if outbound_profile:
            body["outbound_voice_profile_id"] = outbound_profile

        resp = self._post(f"{self.API_BASE}/calls", body)
        data = resp.json().get("data", resp.json())
        # Telnyx returns ``call_control_id`` — that's the id we use for
        # every subsequent action. ``call_leg_id`` is for the leg.
        cc_id = data.get("call_control_id") or data.get("id") or ""
        return ProviderCallHandle(provider_call_id=str(cc_id), raw=data)

    def hangup(self, provider_call_id: str) -> None:
        self._post(self._action_url(provider_call_id, "hangup"))

    def play(
        self,
        provider_call_id: str,
        *,
        audio_url: str | None = None,
        tts_text: str | None = None,
        tts_voice: str | None = None,
    ) -> None:
        if audio_url:
            self._post(
                self._action_url(provider_call_id, "playback_start"),
                {"audio_url": audio_url},
            )
        elif tts_text:
            payload: dict[str, Any] = {"payload": tts_text}
            if tts_voice:
                payload["voice"] = tts_voice
            self._post(self._action_url(provider_call_id, "speak"), payload)

    def gather_dtmf(
        self,
        provider_call_id: str,
        *,
        max_digits: int,
        timeout_seconds: int,
        finish_on_key: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "maximum_digits": max_digits,
            "timeout_millis": timeout_seconds * 1000,
        }
        if finish_on_key:
            payload["terminating_digit"] = finish_on_key
        self._post(self._action_url(provider_call_id, "gather"), payload)

    def gather_speech(
        self,
        provider_call_id: str,
        *,
        language: str,
        timeout_seconds: int,
    ) -> None:
        self._post(
            self._action_url(provider_call_id, "transcription_start"),
            {"language": language, "transcription_engine": "B"},
        )

    def transfer(self, provider_call_id: str, *, to_uri: str) -> None:
        self._post(
            self._action_url(provider_call_id, "transfer"),
            {"to": to_uri},
        )

    def start_recording(self, provider_call_id: str) -> str:
        resp = self._post(
            self._action_url(provider_call_id, "record_start"),
            {
                "format": "mp3",
                "channels": "single",
                "play_beep": False,
            },
        )
        data = resp.json().get("data", resp.json())
        # Telnyx returns ``recording_id`` via the ``call.recording.saved``
        # webhook eventually; the action response is just an ack. We
        # return the call control id as a placeholder — the real id lands
        # via the saved-recording webhook.
        return str(data.get("recording_id") or provider_call_id)

    def stop_recording(self, provider_call_id: str, provider_recording_id: str) -> None:
        self._post(self._action_url(provider_call_id, "record_stop"))

    def fetch_recording(self, provider_recording_id: str) -> bytes:
        """Download the recording bytes via Telnyx's recordings resource."""
        resp = requests.get(
            f"{self.API_BASE}/recordings/{provider_recording_id}",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        meta = resp.json().get("data", resp.json())
        url = meta.get("download_url") or meta.get("url")
        if not url:
            raise ValueError(f"Telnyx recording {provider_recording_id} has no download URL")
        audio = self.session.get(url, timeout=30)
        audio.raise_for_status()
        return audio.content

    # ─── Webhook verification + parsing ─────────────────────────────────

    def verify_webhook(self, request) -> bool:
        """Verify Telnyx's Ed25519 detached signature.

        Algorithm (per Telnyx docs):
          1. Read ``Telnyx-Signature-Ed25519`` (base64-encoded) and
             ``Telnyx-Timestamp`` headers.
          2. Build the signed message: ``timestamp + "|" + raw_body``.
          3. Verify with the public key configured at the Telnyx
             application level (``public_key`` in credentials).

        Tolerates a 5-minute clock skew on the timestamp.
        """
        sig_b64 = request.META.get("HTTP_TELNYX_SIGNATURE_ED25519", "")
        timestamp = request.META.get("HTTP_TELNYX_TIMESTAMP", "")
        if not sig_b64 or not timestamp:
            return False

        public_key_b64 = self.credentials.get("public_key", "")
        if not public_key_b64:
            return False

        try:
            sig = base64.b64decode(sig_b64)
            pub_bytes = base64.b64decode(public_key_b64)
        except Exception:  # noqa: BLE001
            return False

        # Skew check (5 minutes).
        try:
            ts_int = int(timestamp)
        except ValueError:
            return False
        import time

        if abs(time.time() - ts_int) > 300:
            return False

        message = f"{timestamp}|".encode() + (request.body or b"")
        try:
            Ed25519PublicKey.from_public_bytes(pub_bytes).verify(sig, message)
        except (InvalidSignature, ValueError):
            return False
        return True

    def parse_webhook(self, request) -> NormalizedCallEvent:
        import json as _json

        try:
            envelope = _json.loads(request.body or b"{}")
        except _json.JSONDecodeError:
            envelope = {}

        # Telnyx wraps every event in ``{"data": {"event_type": ..., "payload": {...}}}``.
        data = envelope.get("data") or envelope
        event_type = data.get("event_type") or ""
        payload = data.get("payload") or {}

        call_control_id = payload.get("call_control_id") or payload.get("call_leg_id") or ""

        canonical_status, canonical_event = self.EVENT_TYPE_MAP.get(event_type, (None, CallEventType.INITIATED))

        hangup_cause: str | None = None
        if event_type == "call.hangup":
            raw_cause = payload.get("hangup_cause") or "normal_clearing"
            hangup_cause = self.HANGUP_CAUSE_MAP.get(raw_cause, HangupCause.UNKNOWN)

        return NormalizedCallEvent(
            provider_call_id=str(call_control_id),
            event_type=canonical_event,
            hangup_cause=hangup_cause,
            payload=envelope,
        )

    def _normalize_status(self, provider_status: str | None) -> str | None:
        """Telnyx event types embed status — translate the same way
        ``parse_webhook`` does, but exposed for the webhook handler to
        stamp on ``VoiceCall.status``."""
        if not provider_status:
            return None
        result = self.EVENT_TYPE_MAP.get(provider_status)
        return result[0] if result else None


register_voice_adapter(VoiceProvider.TELNYX.value, TelnyxVoiceAdapter)
