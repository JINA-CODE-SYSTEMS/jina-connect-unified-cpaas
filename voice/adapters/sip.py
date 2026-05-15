"""Universal SIP adapter (#163).

One adapter for every SIP trunk vendor (Dialogic, Telnyx Elastic SIP,
Twilio Elastic SIP, Plivo SIP, Vonage SIP, Exotel SIP, Knowlarity,
Servetel, MyOperator, Tata Tele, Airtel, generic). Vendor differences
live in PJSIP profile YAMLs (``voice/sip_config/profiles/``); the
adapter itself is provider-agnostic and talks to Asterisk via ARI.

Inbound events arrive over the ARI WebSocket — see
``voice/sip_config/ari_consumer.py`` — and translate into the same
``NormalizedCallEvent`` shape HTTP voice adapters produce, so the
state machine + signals don't know the difference.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from voice.adapters.base import (
    CallInstructions,
    NormalizedCallEvent,
    ProviderCallHandle,
    VoiceAdapter,
)
from voice.adapters.registry import register_voice_adapter
from voice.constants import VoiceProvider
from voice.sip_config import pjsip_writer
from voice.sip_config.ari_client import AriClient
from wa.adapters.channel_base import Capabilities

logger = logging.getLogger(__name__)


class SIPVoiceAdapter(VoiceAdapter):
    """SIP adapter — universal across trunk vendors via Asterisk ARI.

    ``capabilities`` advertises everything the SIP+ARI stack supports.
    Provider cost is False because SIP trunks don't publish per-call
    cost callbacks; billing goes through ``VoiceRateCard`` (#170).
    """

    capabilities = Capabilities(
        supports_text=False,
        supports_voice_call=True,
        supports_recording=True,
        supports_dtmf_gather=True,
        supports_speech_gather=False,  # Asterisk speech recognition is module-dependent
        supports_call_transfer=True,
        supports_sip_refer=True,
        supports_provider_cost=False,
    )

    def __init__(self, provider_config) -> None:
        self.provider_config = provider_config
        self._creds_cache: dict | None = None
        self._ari_client_cache: AriClient | None = None

    # ── credential helpers ──────────────────────────────────────────────

    @property
    def credentials(self) -> dict:
        if self._creds_cache is None:
            raw = self.provider_config.credentials or "{}"
            self._creds_cache = json.loads(raw)
        return self._creds_cache

    @property
    def ari(self) -> AriClient:
        if self._ari_client_cache is None:
            self._ari_client_cache = AriClient()
        return self._ari_client_cache

    @property
    def endpoint_id(self) -> str:
        """The PJSIP endpoint id this config provisions in Asterisk."""
        return f"voice-{self.provider_config.id}-endpoint"

    # ── outbound ────────────────────────────────────────────────────────

    def initiate_call(
        self,
        *,
        from_number: str,
        to_number: str,
        callback_url: str,
        instructions: CallInstructions,
    ) -> ProviderCallHandle:
        """Place an outbound call via ARI ORIGINATE.

        Endpoint format: ``PJSIP/<dialed-number>@<endpoint-id>`` —
        Asterisk dials ``to_number`` out through the trunk configured
        on our endpoint.
        """
        endpoint = f"PJSIP/{to_number}@{self.endpoint_id}"
        result = self.ari.originate(
            endpoint=endpoint,
            callerid=from_number,
            variables={"VOICE_CALL_TENANT": str(self.provider_config.tenant_id)},
        )
        return ProviderCallHandle(
            provider_call_id=result["id"],
            raw=result,
        )

    def hangup(self, provider_call_id: str) -> None:
        self.ari.hangup_channel(provider_call_id)

    def play(
        self,
        provider_call_id: str,
        *,
        audio_url: str | None = None,
        tts_text: str | None = None,
        tts_voice: str | None = None,
    ) -> None:
        if audio_url:
            self.ari.play_media(provider_call_id, media=f"sound:{audio_url}")
        elif tts_text:
            # Asterisk's TTS layer varies by deployment; assume
            # ``synthesis:`` is wired to a TTS engine server-side.
            self.ari.play_media(provider_call_id, media=f"synthesis:{tts_text}")

    def gather_dtmf(
        self,
        provider_call_id: str,
        *,
        max_digits: int,
        timeout_seconds: int,
        finish_on_key: str | None = None,
    ) -> None:
        # ARI gather is event-driven — we don't actively poll here.
        # The consumer translates ``ChannelDtmfReceived`` events into
        # session state, which the IVR session reader uses to advance
        # the flow. See #168. For #163 this is a no-op that records
        # what we *would* gather; the consumer + IVR finish the loop.
        logger.info(
            "[SIPVoiceAdapter.gather_dtmf] channel=%s max_digits=%d timeout=%d",
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
        raise NotImplementedError(
            "Speech recognition over SIP requires an Asterisk speech module that's not configured by default."
        )

    def transfer(self, provider_call_id: str, *, to_uri: str) -> None:
        """Blind transfer via SIP REFER (ARI's redirect endpoint)."""
        self.ari.refer_channel(provider_call_id, to_uri=to_uri)

    # ── recording ───────────────────────────────────────────────────────

    def start_recording(self, provider_call_id: str) -> str:
        """Start a MixMonitor recording. Returns the Asterisk recording name."""
        name = f"call-{provider_call_id}"
        self.ari.record_channel(
            provider_call_id,
            name=name,
            max_duration_seconds=3600,
            beep=False,
        )
        return name

    def stop_recording(self, provider_call_id: str, provider_recording_id: str) -> None:
        self.ari.stop_recording(provider_recording_id)

    def fetch_recording(self, provider_recording_id: str) -> bytes:
        """Read recording bytes via ARI's stored-recording GET.

        Asterisk also writes the WAV to a local path; deployments with
        the recordings volume mounted shared can fall back to reading
        from disk. ``ASTERISK_RECORDINGS_DIR`` overrides the path.
        """
        try:
            return self.ari.get_recording_file(provider_recording_id)
        except Exception as exc:  # noqa: BLE001
            recordings_dir = os.environ.get("ASTERISK_RECORDINGS_DIR")
            if not recordings_dir:
                raise
            path = Path(recordings_dir) / f"{provider_recording_id}.wav"
            if not path.exists():
                raise FileNotFoundError(
                    f"Recording {provider_recording_id} not found locally and ARI GET failed: {exc}"
                ) from exc
            return path.read_bytes()

    # ── inbound ─────────────────────────────────────────────────────────

    def parse_webhook(self, request) -> NormalizedCallEvent:
        """SIP doesn't take HTTP webhooks — events arrive via the ARI
        consumer (``voice.sip_config.ari_consumer.translate_event``).

        Kept on the interface for parity; calling it on a SIP adapter
        is a programming error.
        """
        raise NotImplementedError(
            "SIPVoiceAdapter does not receive HTTP webhooks. ARI events "
            "are translated by voice.sip_config.ari_consumer."
        )

    # ── provisioning ────────────────────────────────────────────────────

    def ensure_provisioned(self) -> None:
        """Render + write the PJSIP config for this provider.

        Idempotent — re-rendering with the same provider config UUID
        overwrites the same drop file. Called from the SIP provisioning
        signal handler when a ``VoiceProviderConfig`` with
        ``provider='sip'`` is created or updated.
        """
        pjsip_writer.ensure_endpoint(self.provider_config)


# Self-register at import time.
register_voice_adapter(VoiceProvider.SIP.value, SIPVoiceAdapter)
