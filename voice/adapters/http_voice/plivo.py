"""Plivo Voice adapter (#164).

Uses Plivo's REST API directly via ``requests`` — same pattern as the
Twilio adapter (no SDK dependency).

Auth: HTTP Basic with ``auth_id`` + ``auth_token``.
Base URL: ``https://api.plivo.com/v1/Account/{auth_id}/``

Webhook signature: ``X-Plivo-Signature-V3-Nonce`` + ``X-Plivo-Signature-V3``
header (HMAC-SHA256 of nonce + URL + sorted POST params, base64-encoded).
Plivo bumped from V2 (HMAC-SHA1, similar to Twilio) to V3 in 2021;
we implement V3 since it's what all new accounts use.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

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


class PlivoVoiceAdapter(HttpVoiceAdapter):
    """Adapter for Plivo Voice."""

    capabilities = Capabilities(
        supports_text=False,
        supports_voice_call=True,
        supports_recording=True,
        supports_dtmf_gather=True,
        supports_speech_gather=True,
        supports_call_transfer=True,
        supports_provider_cost=True,
    )

    API_BASE = "https://api.plivo.com/v1/Account"

    # ─── Status / hangup-cause normalisation ────────────────────────────
    # Plivo CallStatus values per their docs.
    STATUS_MAP = {
        "queued": CallStatus.QUEUED,
        "ringing": CallStatus.RINGING,
        "in-progress": CallStatus.IN_PROGRESS,
        "completed": CallStatus.COMPLETED,
        "failed": CallStatus.FAILED,
        "busy": CallStatus.FAILED,
        "no-answer": CallStatus.FAILED,
        "rejected": CallStatus.FAILED,
        "canceled": CallStatus.CANCELED,
    }

    STATUS_TO_HANGUP_CAUSE = {
        "completed": HangupCause.NORMAL_CLEARING,
        "busy": HangupCause.USER_BUSY,
        "no-answer": HangupCause.NO_ANSWER,
        "rejected": HangupCause.CALL_REJECTED,
        "failed": HangupCause.NORMAL_TEMPORARY_FAILURE,
        "canceled": HangupCause.NORMAL_CLEARING,
    }

    # ─── Auth helpers ───────────────────────────────────────────────────

    def _auth(self) -> tuple[str, str]:
        return (self.credentials["auth_id"], self.credentials["auth_token"])

    def _account_url(self, suffix: str) -> str:
        auth_id = self.credentials["auth_id"]
        return f"{self.API_BASE}/{auth_id}/{suffix}"

    # ─── VoiceAdapter implementations ───────────────────────────────────

    def initiate_call(
        self,
        *,
        from_number: str,
        to_number: str,
        callback_url: str,
        instructions: CallInstructions,
    ) -> ProviderCallHandle:
        """Place an outbound call via ``POST /v1/Account/{id}/Call/``.

        ``answer_url`` is Plivo's equivalent of Twilio's ``Url`` —
        Plivo fetches PLIVO XML from there when the call is answered.
        """
        data = {
            "from": from_number,
            "to": to_number,
            "answer_url": callback_url,
            "answer_method": "POST",
            "hangup_url": callback_url.rstrip("/") + "/../call-status/",
            "hangup_method": "POST",
        }
        resp = self._request(
            "POST",
            self._account_url("Call/"),
            json=data,
            auth=self._auth(),
        )
        resp.raise_for_status()
        body = resp.json()
        # Plivo returns the CallUUID in ``request_uuid`` (call hasn't been
        # placed yet at API-return time — it's queued).
        call_uuid = body.get("request_uuid") or body.get("api_id") or ""
        return ProviderCallHandle(provider_call_id=call_uuid, raw=body)

    def hangup(self, provider_call_id: str) -> None:
        resp = self._request(
            "DELETE",
            self._account_url(f"Call/{provider_call_id}/"),
            auth=self._auth(),
        )
        # 204 No Content on success.
        if resp.status_code not in (200, 204):
            resp.raise_for_status()

    def play(
        self,
        provider_call_id: str,
        *,
        audio_url: str | None = None,
        tts_text: str | None = None,
        tts_voice: str | None = None,
    ) -> None:
        if audio_url:
            resp = self._request(
                "POST",
                self._account_url(f"Call/{provider_call_id}/Play/"),
                json={"urls": audio_url},
                auth=self._auth(),
            )
        elif tts_text:
            payload: dict[str, Any] = {"text": tts_text}
            if tts_voice:
                payload["voice"] = tts_voice
            resp = self._request(
                "POST",
                self._account_url(f"Call/{provider_call_id}/Speak/"),
                json=payload,
                auth=self._auth(),
            )
        else:
            return
        resp.raise_for_status()

    def gather_dtmf(
        self,
        provider_call_id: str,
        *,
        max_digits: int,
        timeout_seconds: int,
        finish_on_key: str | None = None,
    ) -> None:
        """Plivo's mid-call DTMF gather is initiated by returning fresh
        XML from the call's answer URL on the next leg. The full IVR
        loop lands with #168; for #164 this method is a no-op that
        logs intent (matching the SIP adapter's pattern)."""
        logger.info(
            "[PlivoVoiceAdapter.gather_dtmf] call=%s max_digits=%d timeout=%d",
            provider_call_id,
            max_digits,
            timeout_seconds,
        )

    def gather_speech(
        self,
        provider_call_id: str,
        *,
        language: str,
        timeout_seconds: int,
    ) -> None:
        logger.info(
            "[PlivoVoiceAdapter.gather_speech] call=%s language=%s timeout=%d",
            provider_call_id,
            language,
            timeout_seconds,
        )

    def transfer(self, provider_call_id: str, *, to_uri: str) -> None:
        """Transfer via Plivo's ``Transfer`` action on the live call."""
        resp = self._request(
            "POST",
            self._account_url(f"Call/{provider_call_id}/"),
            json={"legs": "aleg", "aleg_url": to_uri},
            auth=self._auth(),
        )
        resp.raise_for_status()

    def start_recording(self, provider_call_id: str) -> str:
        resp = self._request(
            "POST",
            self._account_url(f"Call/{provider_call_id}/Record/"),
            auth=self._auth(),
        )
        resp.raise_for_status()
        body = resp.json()
        # Plivo returns ``recording_id`` (+ ``url`` once available).
        return body.get("recording_id") or body.get("api_id") or ""

    def stop_recording(self, provider_call_id: str, provider_recording_id: str) -> None:
        resp = self._request(
            "DELETE",
            self._account_url(f"Call/{provider_call_id}/Record/"),
            auth=self._auth(),
        )
        if resp.status_code not in (200, 204):
            resp.raise_for_status()

    def fetch_recording(self, provider_recording_id: str) -> bytes:
        """Download the recording bytes.

        Plivo serves recordings from a CDN URL surfaced via the
        Recording resource. Two-step: GET metadata → GET the file.
        """
        meta = self._request(
            "GET",
            self._account_url(f"Recording/{provider_recording_id}/"),
            auth=self._auth(),
        )
        meta.raise_for_status()
        recording_url = meta.json().get("recording_url")
        if not recording_url:
            raise ValueError(f"Plivo recording {provider_recording_id} has no recording_url yet")
        # CDN download — no auth (signed URL).
        audio = self.session.get(recording_url, timeout=30)
        audio.raise_for_status()
        return audio.content

    # ─── Webhook signature + parsing ────────────────────────────────────

    def verify_webhook(self, request) -> bool:
        """Plivo Signature V3.

        Algorithm:
          1. nonce = ``X-Plivo-Signature-V3-Nonce``
          2. uri = full request URL
          3. Build signing string = nonce + uri + ``&``-joined sorted
             ``key=value`` params (URL-encoded the same way Plivo's
             SDK does — for POST forms we use the POST data as-is).
          4. HMAC-SHA256 with auth_token, base64-encoded.
          5. constant-time compare against ``X-Plivo-Signature-V3``.
        """
        sig = request.META.get("HTTP_X_PLIVO_SIGNATURE_V3", "")
        nonce = request.META.get("HTTP_X_PLIVO_SIGNATURE_V3_NONCE", "")
        if not sig or not nonce:
            return False
        auth_token = self.credentials.get("auth_token", "")
        if not auth_token:
            return False

        # Proxy-aware canonical URL — same TLS-termination concern as
        # Twilio. See ``HttpVoiceAdapter._canonical_request_url``.
        url = self._canonical_request_url(request)
        params = sorted(request.POST.items())
        # Plivo's signing string: nonce + uri + concatenated params
        # (key.value pairs, separated by . — matching Plivo SDK behavior).
        data = nonce + url
        for k, v in params:
            data += k + v
        expected = base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha256).digest()).decode()
        return hmac.compare_digest(expected, sig)

    def parse_webhook(self, request) -> NormalizedCallEvent:
        post = request.POST
        # Plivo identifies the call via ``CallUUID``.
        call_uuid = post.get("CallUUID", "")
        plivo_status = post.get("CallStatus", "")
        canonical_status = self._normalize_status(plivo_status)

        hangup_cause: str | None
        if canonical_status in (
            CallStatus.COMPLETED,
            CallStatus.FAILED,
            CallStatus.CANCELED,
        ):
            # Plivo provides ``HangupCause`` separately too — prefer it
            # when present; fall back to status-derived mapping.
            raw_cause = post.get("HangupCause", "") or plivo_status
            hangup_cause = self.STATUS_TO_HANGUP_CAUSE.get(raw_cause, HangupCause.UNKNOWN)
        else:
            hangup_cause = None

        event_type_map = {
            CallStatus.QUEUED: CallEventType.INITIATED,
            CallStatus.RINGING: CallEventType.RINGING,
            CallStatus.IN_PROGRESS: CallEventType.ANSWERED,
            CallStatus.COMPLETED: CallEventType.COMPLETED,
            CallStatus.FAILED: CallEventType.FAILED,
            CallStatus.CANCELED: CallEventType.FAILED,
        }
        event_type = event_type_map.get(canonical_status, CallEventType.INITIATED)

        return NormalizedCallEvent(
            provider_call_id=call_uuid,
            event_type=event_type,
            hangup_cause=hangup_cause,
            payload=dict(post.items()),
        )

    def get_dialect(self):
        from voice.ivr.dialects import plivo_xml as _plivo_module

        return _plivo_module


# Self-register at import time.
register_voice_adapter(VoiceProvider.PLIVO.value, PlivoVoiceAdapter)
