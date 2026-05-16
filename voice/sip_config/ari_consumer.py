"""Long-running Asterisk ARI event consumer (#163).

ARI exposes per-call events (``StasisStart`` / ``ChannelDtmfReceived`` /
``RecordingFinished`` / etc.) over a WebSocket. SIP-channel "webhooks"
are actually these local events — once translated into
``NormalizedCallEvent`` they look identical to HTTP voice webhooks
from the rest of the app's perspective.

This module runs as a separate process (``manage.py voice_ari_consumer``)
on every host that talks to Asterisk. It connects to the ARI events
WebSocket, translates each frame, and queues ``voice.tasks.process_call_status``
the same way an HTTP webhook handler would.

``websocket-client`` is added to requirements.txt — the import is lazy
so the rest of the voice package imports cleanly even without it.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

from django.conf import settings

from voice.constants import CallEventType

logger = logging.getLogger(__name__)


# ARI event types we care about, mapped onto our canonical CallEventType.
_ARI_EVENT_TYPE_MAP = {
    "StasisStart": CallEventType.INITIATED,
    "ChannelRingingStarted": CallEventType.RINGING,
    "ChannelStateChange": None,  # decided per-state below
    "ChannelDtmfReceived": CallEventType.DTMF,
    "ChannelTalkingStarted": CallEventType.SPEECH,
    "RecordingStarted": CallEventType.RECORDING_STARTED,
    "RecordingFinished": CallEventType.RECORDING_COMPLETED,
    "ChannelLeftBridge": CallEventType.TRANSFERRED,
    "StasisEnd": CallEventType.COMPLETED,
    "ChannelHangupRequest": CallEventType.COMPLETED,
    "ChannelDestroyed": CallEventType.COMPLETED,
}


def _build_ws_url() -> str:
    """Compose the ARI events WebSocket URL from Django settings."""
    base = getattr(settings, "ASTERISK_ARI_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("ASTERISK_ARI_URL is not configured.")
    # Asterisk's WebSocket lives at /ari/events ; auth is via query string.
    qs = urlencode(
        {
            "api_key": (
                f"{getattr(settings, 'ASTERISK_ARI_USER', '')}:{getattr(settings, 'ASTERISK_ARI_PASSWORD', '')}"
            ),
            "app": getattr(settings, "ASTERISK_ARI_APP_NAME", "jina-voice"),
            "subscribeAll": "true",
        }
    )
    # http(s) → ws(s).
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_base}/ari/events?{qs}"


def translate_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one ARI event dict into the payload shape expected by
    ``voice.tasks.process_call_status``.

    Returns ``None`` for events we don't care about (e.g.
    ``DeviceStateChanged``); callers should skip these.
    """
    ari_type = event.get("type")
    if not ari_type or ari_type not in _ARI_EVENT_TYPE_MAP:
        return None

    canonical = _ARI_EVENT_TYPE_MAP[ari_type]
    if canonical is None:
        # ChannelStateChange — defer to the channel state.
        state = (event.get("channel") or {}).get("state")
        canonical = {
            "Ring": CallEventType.RINGING,
            "Ringing": CallEventType.RINGING,
            "Up": CallEventType.ANSWERED,
        }.get(state)
        if canonical is None:
            return None

    channel = event.get("channel") or {}
    # Asterisk identifies the call via the channel's name / id; for our
    # purposes we tag the call with the SIP Call-ID stored on the
    # ``channelvars`` map (set when we ORIGINATE).
    provider_call_id = (event.get("args") or [None])[0] or channel.get("id") or ""

    hangup_cause: str | None = None
    if ari_type in {"StasisEnd", "ChannelHangupRequest", "ChannelDestroyed"}:
        cause = event.get("cause") or 0
        hangup_cause = _q850_to_canonical(int(cause)) if cause else None

    return {
        "provider_call_id": str(provider_call_id),
        "event_type": canonical,
        "hangup_cause": hangup_cause,
        "raw": event,
    }


# Q.850 cause code → canonical HangupCause.
_Q850 = {
    16: "NORMAL_CLEARING",
    17: "USER_BUSY",
    18: "NO_USER_RESPONSE",
    19: "NO_ANSWER",
    21: "CALL_REJECTED",
    27: "DESTINATION_OUT_OF_ORDER",
    28: "INVALID_NUMBER_FORMAT",
    31: "NORMAL_TEMPORARY_FAILURE",
    34: "NORMAL_TEMPORARY_FAILURE",
    38: "NETWORK_OUT_OF_ORDER",
}


def _q850_to_canonical(cause_code: int) -> str:
    return _Q850.get(cause_code, "UNKNOWN")


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket loop
# ─────────────────────────────────────────────────────────────────────────────


def run_consumer() -> None:  # pragma: no cover — requires live Asterisk
    """Connect to ARI's WebSocket and forward each event to Celery.

    Blocks forever. ``manage.py voice_ari_consumer`` is the entry point.

    Restart-on-disconnect is the caller's responsibility — typically a
    process supervisor (systemd / supervisord / a Docker restart
    policy). We deliberately don't loop inside the function so a
    transient WebSocket error surfaces and exits with non-zero.
    """
    try:
        import websocket  # lazy: websocket-client is in requirements but optional
    except ImportError as e:
        raise RuntimeError("voice_ari_consumer requires the 'websocket-client' package.") from e

    from voice.tasks import process_call_status

    url = _build_ws_url()
    logger.info("[voice.sip_config.ari_consumer] connecting to %s", url)

    def on_message(ws, raw_message: str) -> None:
        try:
            event = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning("ARI sent non-JSON message: %s", raw_message[:200])
            return
        payload = translate_event(event)
        if payload is None:
            return
        process_call_status.delay(payload)

    def on_error(ws, error) -> None:  # noqa: ARG001
        logger.warning("[voice.sip_config.ari_consumer] websocket error: %s", error)

    def on_close(ws, status_code, msg) -> None:  # noqa: ARG001
        logger.info("[voice.sip_config.ari_consumer] websocket closed (%s)", status_code)

    ws_app = websocket.WebSocketApp(
        url,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    ws_app.run_forever()
