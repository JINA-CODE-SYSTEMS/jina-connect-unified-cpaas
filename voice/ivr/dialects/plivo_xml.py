"""Plivo XML (PLIVO XML) emitter — Plivo Voice control language (#164).

Wire-level differences from TwiML:
  * ``<Speak>`` for TTS (vs Twilio's ``<Say>``)
  * ``<GetDigits>`` for DTMF (vs Twilio's ``<Gather numDigits=...>``)
  * ``<GetInput>`` for speech
  * Outer wrapper is the same ``<Response>``

Each handler corresponds to one voice node type from ``chat_flow``; the
IVR compiler (#168) walks the flow and calls these to assemble the
per-call XML response.
"""

from __future__ import annotations

from typing import Any, Callable
from xml.sax.saxutils import escape


def play(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Speak>`` (TTS) or ``<Play>`` (pre-recorded audio).

    Plivo's TTS attribute names are ``voice`` and ``language`` — same
    as Twilio but with different voice catalogs (Plivo ships its own
    set; the adapter layer maps cross-vendor voice ids when needed).
    """
    if node_data.get("tts_text"):
        text = escape(node_data["tts_text"])
        attrs = ""
        if node_data.get("tts_voice"):
            attrs += f' voice="{escape(node_data["tts_voice"])}"'
        if node_data.get("tts_language"):
            attrs += f' language="{escape(node_data["tts_language"])}"'
        return f"<Speak{attrs}>{text}</Speak>"
    if node_data.get("audio_url"):
        return f"<Play>{escape(node_data['audio_url'])}</Play>"
    # Validator should have caught the empty case — emit a short pause
    # rather than leave the call silent forever.
    return '<Wait length="1"/>'


def gather_dtmf(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<GetDigits>``.

    ``action`` points at the gather webhook; ``digitTimeout`` is the
    inter-digit timeout, ``timeout`` is the no-input timeout.
    """
    max_digits = int(node_data["max_digits"])
    timeout = int(node_data["timeout_seconds"])
    finish_on_key = node_data.get("finish_on_key")
    action = context.get("gather_action_url", "")
    inner = ""
    if node_data.get("prompt_tts"):
        inner = f"<Speak>{escape(node_data['prompt_tts'])}</Speak>"
    elif node_data.get("prompt_audio_url"):
        inner = f"<Play>{escape(node_data['prompt_audio_url'])}</Play>"
    finish_attr = f' finishOnKey="{escape(finish_on_key)}"' if finish_on_key else ""
    action_attr = f' action="{escape(action)}"' if action else ""
    return f'<GetDigits numDigits="{max_digits}" timeout="{timeout}"{finish_attr}{action_attr}>{inner}</GetDigits>'


def gather_speech(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<GetInput>`` configured for speech."""
    language = node_data["language"]
    timeout = int(node_data["timeout_seconds"])
    action = context.get("gather_action_url", "")
    action_attr = f' action="{escape(action)}"' if action else ""
    return f'<GetInput inputType="speech" language="{escape(language)}" speechTimeout="{timeout}"{action_attr}/>'


def record(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Record>``."""
    max_duration = int(node_data["max_duration_seconds"])
    play_beep = "true" if node_data.get("play_beep", True) else "false"
    finish_on_silence = node_data.get("finish_on_silence")
    action = context.get("recording_action_url", "")
    silence_attr = f' finishOnSilence="{int(finish_on_silence)}"' if finish_on_silence is not None else ""
    action_attr = f' action="{escape(action)}"' if action else ""
    return f'<Record maxLength="{max_duration}" playBeep="{play_beep}"{silence_attr}{action_attr}/>'


def transfer(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Dial>`` — Plivo's transfer primitive."""
    target = escape(node_data["to_uri"])
    return f"<Dial><Number>{target}</Number></Dial>"


def hangup(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    return "<Hangup/>"


_HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], str]] = {
    "voice.play": play,
    "voice.gather_dtmf": gather_dtmf,
    "voice.gather_speech": gather_speech,
    "voice.record": record,
    "voice.transfer": transfer,
    "voice.hangup": hangup,
}


def get_handler(type_id: str):
    return _HANDLERS.get(type_id)


def assemble(chunks: list[str]) -> str:
    """Wrap a list of Plivo XML elements in ``<Response>...</Response>``."""
    body = "".join(chunks)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
