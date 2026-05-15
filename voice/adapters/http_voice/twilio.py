"""Twilio Voice adapter (#160).

Uses Twilio's REST API directly via ``requests`` (no SDK dependency,
matching the pattern from ``sms/providers/twilio_provider.py``).

Webhook signature verification implements Twilio's documented HMAC-SHA1
algorithm: HMAC of the full URL with all POST params concatenated in
sorted order, base64-encoded, compared to the ``X-Twilio-Signature``
header.

The adapter is self-registering: importing this module registers it in
``voice.adapters.registry`` under ``provider="twilio"``. ``voice/apps.py``
ensures the import happens at app startup.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

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
from voice.ivr.dialects import twiml
from wa.adapters.channel_base import Capabilities

logger = logging.getLogger(__name__)


class TwilioVoiceAdapter(HttpVoiceAdapter):
    """Adapter for Twilio Voice (the HTTP API, not Elastic SIP)."""

    capabilities = Capabilities(
        supports_text=False,
        supports_voice_call=True,
        supports_recording=True,
        supports_dtmf_gather=True,
        supports_speech_gather=True,
        supports_call_transfer=True,
        supports_provider_cost=True,
    )

    API_BASE = "https://api.twilio.com/2010-04-01/Accounts"

    # ─── Status / hangup-cause normalisation ────────────────────────────

    STATUS_MAP = {
        "queued": CallStatus.QUEUED,
        "initiated": CallStatus.INITIATING,
        "ringing": CallStatus.RINGING,
        "in-progress": CallStatus.IN_PROGRESS,
        "completed": CallStatus.COMPLETED,
        "failed": CallStatus.FAILED,
        "busy": CallStatus.FAILED,
        "no-answer": CallStatus.FAILED,
        "canceled": CallStatus.CANCELED,
    }

    # Twilio reports hangup outcome via the final ``CallStatus`` field
    # rather than a separate cause, so we derive ``HangupCause`` from
    # the terminal status.
    STATUS_TO_HANGUP_CAUSE = {
        "completed": HangupCause.NORMAL_CLEARING,
        "busy": HangupCause.USER_BUSY,
        "no-answer": HangupCause.NO_ANSWER,
        "failed": HangupCause.NORMAL_TEMPORARY_FAILURE,
        "canceled": HangupCause.NORMAL_CLEARING,
    }

    # ─── Auth helpers ───────────────────────────────────────────────────

    def _auth(self) -> tuple[str, str]:
        return (
            self.credentials["account_sid"],
            self.credentials["auth_token"],
        )

    def _account_url(self, suffix: str) -> str:
        sid = self.credentials["account_sid"]
        return f"{self.API_BASE}/{sid}/{suffix}"

    # ─── VoiceAdapter implementations ───────────────────────────────────

    def initiate_call(
        self,
        *,
        from_number: str,
        to_number: str,
        callback_url: str,
        instructions: CallInstructions,
    ) -> ProviderCallHandle:
        # Twilio requires either a ``Url`` (TwiML BIN / external answer URL)
        # or a ``Twiml`` (inline TwiML) parameter. We use ``Url`` and let
        # our answer webhook produce the per-call TwiML so the same code
        # path serves both static plays and IVR flows.
        data = {
            "From": from_number,
            "To": to_number,
            "Url": callback_url,
            "StatusCallback": callback_url + "/../call-status/",
            "StatusCallbackMethod": "POST",
        }
        if instructions.flow_id is not None:
            data["StatusCallbackEvent"] = "initiated ringing answered completed"
        resp = self._request(
            "POST",
            self._account_url("Calls.json"),
            data=data,
            auth=self._auth(),
        )
        resp.raise_for_status()
        body = resp.json()
        return ProviderCallHandle(provider_call_id=body["sid"], raw=body)

    def hangup(self, provider_call_id: str) -> None:
        resp = self._request(
            "POST",
            self._account_url(f"Calls/{provider_call_id}.json"),
            data={"Status": "completed"},
            auth=self._auth(),
        )
        resp.raise_for_status()

    def play(
        self,
        provider_call_id: str,
        *,
        audio_url: str | None = None,
        tts_text: str | None = None,
        tts_voice: str | None = None,
    ) -> None:
        """Update the live call with a fresh TwiML snippet that plays the
        requested audio / TTS. Twilio swaps in the new instructions on
        the next response cycle."""
        twiml_str = twiml.assemble(
            [
                twiml.play(
                    {"audio_url": audio_url, "tts_text": tts_text, "tts_voice": tts_voice},
                    context={},
                )
            ]
        )
        resp = self._request(
            "POST",
            self._account_url(f"Calls/{provider_call_id}.json"),
            data={"Twiml": twiml_str},
            auth=self._auth(),
        )
        resp.raise_for_status()

    def gather_dtmf(
        self,
        provider_call_id: str,
        *,
        max_digits: int,
        timeout_seconds: int,
        finish_on_key: str | None = None,
    ) -> None:
        """Issue a gather by overlaying TwiML on the live call.

        The gather webhook then receives the digits and routes the next
        step. Implementation details for in-flight gather mid-call land
        with the IVR PR (#168); for #160 this is a static building block.
        """
        body = twiml.gather_dtmf(
            {
                "max_digits": max_digits,
                "timeout_seconds": timeout_seconds,
                "finish_on_key": finish_on_key,
            },
            context={},
        )
        twiml_str = twiml.assemble([body])
        resp = self._request(
            "POST",
            self._account_url(f"Calls/{provider_call_id}.json"),
            data={"Twiml": twiml_str},
            auth=self._auth(),
        )
        resp.raise_for_status()

    def gather_speech(
        self,
        provider_call_id: str,
        *,
        language: str,
        timeout_seconds: int,
    ) -> None:
        body = twiml.gather_speech(
            {"language": language, "timeout_seconds": timeout_seconds},
            context={},
        )
        twiml_str = twiml.assemble([body])
        resp = self._request(
            "POST",
            self._account_url(f"Calls/{provider_call_id}.json"),
            data={"Twiml": twiml_str},
            auth=self._auth(),
        )
        resp.raise_for_status()

    def transfer(self, provider_call_id: str, *, to_uri: str) -> None:
        twiml_str = twiml.assemble([twiml.transfer({"to_uri": to_uri}, context={})])
        resp = self._request(
            "POST",
            self._account_url(f"Calls/{provider_call_id}.json"),
            data={"Twiml": twiml_str},
            auth=self._auth(),
        )
        resp.raise_for_status()

    def start_recording(self, provider_call_id: str) -> str:
        resp = self._request(
            "POST",
            self._account_url(f"Calls/{provider_call_id}/Recordings.json"),
            data={"RecordingStatusCallback": ""},  # status webhook wires this
            auth=self._auth(),
        )
        resp.raise_for_status()
        return resp.json()["sid"]

    def stop_recording(self, provider_call_id: str, provider_recording_id: str) -> None:
        resp = self._request(
            "POST",
            self._account_url(f"Calls/{provider_call_id}/Recordings/{provider_recording_id}.json"),
            data={"Status": "stopped"},
            auth=self._auth(),
        )
        resp.raise_for_status()

    def fetch_recording(self, provider_recording_id: str) -> bytes:
        url = self._account_url(f"Recordings/{provider_recording_id}.mp3")
        resp = self._request("GET", url, auth=self._auth())
        resp.raise_for_status()
        return resp.content

    # ─── Webhook signature + parsing ────────────────────────────────────

    def verify_webhook(self, request) -> bool:
        """Twilio HMAC-SHA1 of URL + sorted-param-concat.

        Algorithm (per Twilio docs):
          1. Take the full URL the request was sent to.
          2. Concatenate every POST parameter as ``key + value`` in
             *alphabetically sorted* order.
          3. HMAC-SHA1 the result with the account auth_token.
          4. Base64-encode.
          5. Compare to ``X-Twilio-Signature`` (constant-time).
        """
        signature = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        if not signature:
            return False
        auth_token = self.credentials.get("auth_token", "")
        if not auth_token:
            return False

        url = request.build_absolute_uri()
        params = sorted(request.POST.items())
        data = url + "".join(k + v for k, v in params)
        expected = base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, request) -> NormalizedCallEvent:
        post = request.POST
        call_sid = post.get("CallSid", "")
        twilio_status = post.get("CallStatus", "")
        canonical_status = self._normalize_status(twilio_status)
        # Twilio doesn't send a separate hangup-cause; derive from the
        # terminal CallStatus.
        hangup_cause: str | None
        if canonical_status in (CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELED):
            hangup_cause = self.STATUS_TO_HANGUP_CAUSE.get(twilio_status, HangupCause.UNKNOWN)
        else:
            hangup_cause = None

        # Map Twilio's status onto our CallEventType.
        event_type_map = {
            CallStatus.INITIATING: CallEventType.INITIATED,
            CallStatus.RINGING: CallEventType.RINGING,
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
            payload=dict(post.items()),
        )

    def get_dialect(self):
        """TwiML dialect — TwiML XML response on the answer webhook."""
        from voice.ivr.dialects import twiml as _twiml_module

        return _twiml_module


# Self-register at import time. ``voice/apps.py:VoiceConfig.ready`` triggers
# this via ``register_channel`` setting up the chain — but the safer pattern
# is for each adapter module to register itself unconditionally on import,
# so any code that ``import voice.adapters.http_voice.twilio`` picks it up.
register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)
