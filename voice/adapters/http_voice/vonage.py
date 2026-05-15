"""Vonage Voice API adapter (#165).

Auth: short-lived JWT signed with the application's RSA private key
(``RS256``). Each REST call carries a freshly minted JWT in the
``Authorization: Bearer <jwt>`` header.

Webhook verification: two paths.

  * If the tenant's credentials include ``signature_secret``, inbound
    webhooks are HMAC-SHA256 signed (modern Vonage applications). We
    verify against the ``Authorization: Bearer <signed-jwt>`` header
    using that secret.
  * Otherwise we accept the request (deployments without signed webhooks
    must rely on IP allowlisting at the edge).

NCCO docs: https://developer.vonage.com/en/voice/voice-api/ncco-reference
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import jwt
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


# How long a minted JWT stays valid. Vonage docs recommend ≤24h; 5min
# keeps the window tight without forcing a mint per request.
JWT_TTL_SECONDS = 300


class VonageVoiceAdapter(HttpVoiceAdapter):
    """Adapter for Vonage Voice API."""

    capabilities = Capabilities(
        supports_text=False,
        supports_voice_call=True,
        supports_recording=True,
        supports_dtmf_gather=True,
        supports_speech_gather=True,
        supports_call_transfer=True,
        supports_conference=True,
        supports_provider_cost=True,
    )

    API_BASE = "https://api.nexmo.com/v1"

    # Vonage ``status`` field values per the Voice API event docs.
    STATUS_MAP = {
        "started": CallStatus.INITIATING,
        "ringing": CallStatus.RINGING,
        "answered": CallStatus.IN_PROGRESS,
        "busy": CallStatus.FAILED,
        "cancelled": CallStatus.CANCELED,
        "unanswered": CallStatus.FAILED,
        "rejected": CallStatus.FAILED,
        "failed": CallStatus.FAILED,
        "timeout": CallStatus.FAILED,
        "completed": CallStatus.COMPLETED,
    }

    STATUS_TO_HANGUP_CAUSE = {
        "completed": HangupCause.NORMAL_CLEARING,
        "busy": HangupCause.USER_BUSY,
        "unanswered": HangupCause.NO_ANSWER,
        "rejected": HangupCause.CALL_REJECTED,
        "cancelled": HangupCause.NORMAL_CLEARING,
        "failed": HangupCause.NORMAL_TEMPORARY_FAILURE,
        "timeout": HangupCause.NO_USER_RESPONSE,
    }

    # ─── Auth helpers ───────────────────────────────────────────────────

    def _mint_jwt(self) -> str:
        """Mint a fresh JWT for outbound REST calls.

        Vonage requires ``application_id``, ``iat`` (issued at),
        ``jti`` (unique id) and an ``exp`` claim, all signed RS256
        with the application's private key.
        """
        now = int(time.time())
        payload = {
            "application_id": self.credentials["application_id"],
            "iat": now,
            "exp": now + JWT_TTL_SECONDS,
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(
            payload,
            self.credentials["private_key_pem"],
            algorithm="RS256",
        )

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._mint_jwt()}"}

    # ─── VoiceAdapter implementations ───────────────────────────────────

    def initiate_call(
        self,
        *,
        from_number: str,
        to_number: str,
        callback_url: str,
        instructions: CallInstructions,
    ) -> ProviderCallHandle:
        """Place an outbound call via ``POST /v1/calls``.

        ``answer_url`` is where Vonage fetches the NCCO; ``event_url``
        is where it POSTs lifecycle events.
        """
        body = {
            "to": [{"type": "phone", "number": to_number}],
            "from": {"type": "phone", "number": from_number},
            "answer_url": [callback_url],
            "answer_method": "POST",
            "event_url": [callback_url.rstrip("/") + "/../event/"],
            "event_method": "POST",
        }
        resp = requests.post(
            f"{self.API_BASE}/calls",
            json=body,
            headers=self._auth_header(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return ProviderCallHandle(provider_call_id=data["uuid"], raw=data)

    def hangup(self, provider_call_id: str) -> None:
        # Vonage hangup = PUT /calls/{uuid} with action=hangup.
        resp = requests.put(
            f"{self.API_BASE}/calls/{provider_call_id}",
            json={"action": "hangup"},
            headers=self._auth_header(),
            timeout=15,
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
        if audio_url:
            url = f"{self.API_BASE}/calls/{provider_call_id}/stream"
            payload: dict[str, Any] = {"stream_url": [audio_url]}
        elif tts_text:
            url = f"{self.API_BASE}/calls/{provider_call_id}/talk"
            payload = {"text": tts_text}
            if tts_voice:
                payload["voice_name"] = tts_voice
        else:
            return
        resp = requests.put(url, json=payload, headers=self._auth_header(), timeout=15)
        resp.raise_for_status()

    def gather_dtmf(
        self,
        provider_call_id: str,
        *,
        max_digits: int,
        timeout_seconds: int,
        finish_on_key: str | None = None,
    ) -> None:
        """Mid-call DTMF on Vonage is delivered via an NCCO ``input``
        action returned from the answer webhook; there's no live
        REST poke. Full IVR loop arrives with #168; this no-op
        matches the SIP/Plivo pattern."""
        logger.info(
            "[VonageVoiceAdapter.gather_dtmf] call=%s max_digits=%d timeout=%d",
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
            "[VonageVoiceAdapter.gather_speech] call=%s language=%s timeout=%d",
            provider_call_id,
            language,
            timeout_seconds,
        )

    def transfer(self, provider_call_id: str, *, to_uri: str) -> None:
        """Transfer to a new NCCO via PUT /calls/{uuid} (action=transfer)."""
        resp = requests.put(
            f"{self.API_BASE}/calls/{provider_call_id}",
            json={
                "action": "transfer",
                "destination": {
                    "type": "ncco",
                    "ncco": [
                        {
                            "action": "connect",
                            "endpoint": [{"type": "phone", "number": to_uri}],
                        }
                    ],
                },
            },
            headers=self._auth_header(),
            timeout=15,
        )
        resp.raise_for_status()

    def start_recording(self, provider_call_id: str) -> str:
        """Vonage records via the ``record`` NCCO action mid-call.
        For a REST-driven start we PUT a fresh NCCO that includes the
        record action. Returns the call uuid (used as recording
        correlator) — the actual ``recording_url`` arrives later via
        the recording-completed event."""
        resp = requests.put(
            f"{self.API_BASE}/calls/{provider_call_id}",
            json={
                "action": "transfer",
                "destination": {
                    "type": "ncco",
                    "ncco": [
                        {
                            "action": "record",
                            "format": "mp3",
                            "endOnKey": "#",
                            "beepStart": False,
                            "timeOut": 3600,
                        }
                    ],
                },
            },
            headers=self._auth_header(),
            timeout=15,
        )
        resp.raise_for_status()
        return provider_call_id

    def stop_recording(self, provider_call_id: str, provider_recording_id: str) -> None:
        # Vonage has no explicit stop — the record action ends on
        # endOnKey, silence, or timeOut. Hanging up the call ends
        # recording cleanly.
        return

    def fetch_recording(self, provider_recording_id: str) -> bytes:
        """Download the recording bytes from Vonage's media URL.

        Vonage exposes the recording at the ``recording_url`` the event
        carries; the caller should pass that URL as
        ``provider_recording_id``. (We store it on
        ``VoiceRecording.provider_recording_id`` for SIP-parity.)
        """
        resp = requests.get(provider_recording_id, headers=self._auth_header(), timeout=30)
        resp.raise_for_status()
        return resp.content

    # ─── Webhook verification + parsing ─────────────────────────────────

    def verify_webhook(self, request) -> bool:
        """Verify a signed Vonage webhook.

        If ``signature_secret`` is set in credentials, expect a JWT in
        the ``Authorization: Bearer ...`` header signed HS256 with that
        secret (Vonage's signed-webhook format). If no secret is set,
        we accept the request — deployments without signed webhooks
        rely on IP allowlisting at the edge.
        """
        secret = self.credentials.get("signature_secret")
        if not secret:
            return True

        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.lower().startswith("bearer "):
            return False
        token = auth[7:].strip()

        try:
            jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return False
        return True

    def parse_webhook(self, request) -> NormalizedCallEvent:
        import json as _json

        # Vonage event webhooks are JSON-bodied.
        try:
            payload = _json.loads(request.body or b"{}")
        except _json.JSONDecodeError:
            payload = {}

        call_uuid = payload.get("uuid") or payload.get("conversation_uuid") or ""
        vonage_status = payload.get("status", "")
        canonical_status = self._normalize_status(vonage_status)

        hangup_cause: str | None
        if canonical_status in (
            CallStatus.COMPLETED,
            CallStatus.FAILED,
            CallStatus.CANCELED,
        ):
            hangup_cause = self.STATUS_TO_HANGUP_CAUSE.get(vonage_status, HangupCause.UNKNOWN)
        else:
            hangup_cause = None

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
            provider_call_id=str(call_uuid),
            event_type=event_type,
            hangup_cause=hangup_cause,
            payload=payload,
        )


register_voice_adapter(VoiceProvider.VONAGE.value, VonageVoiceAdapter)
