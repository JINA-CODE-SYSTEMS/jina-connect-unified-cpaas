"""Exotel XML (ExoML) emitter (#167).

Shape is very close to TwiML — outer ``<Response>`` wrapper, ``<Say>``
for TTS, ``<Play>`` for audio, ``<Gather>`` for DTMF, ``<Record>``,
``<Dial>``, ``<Hangup>``. Exotel-specific addition: ``<Passthru>`` for
in-call routing decisions (used by the passthru webhook).

Docs: https://developer.exotel.com/api/passthru-applet
"""

from __future__ import annotations

from typing import Any, Callable
from xml.sax.saxutils import escape


def play(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Say>`` (TTS) or ``<Play>`` (audio URL)."""
    if node_data.get("tts_text"):
        text = escape(node_data["tts_text"])
        # Exotel uses ``voice`` attribute only — language is inferred
        # from the voice ID in their catalog.
        voice_attr = f' voice="{escape(node_data["tts_voice"])}"' if node_data.get("tts_voice") else ""
        return f"<Say{voice_attr}>{text}</Say>"
    if node_data.get("audio_url"):
        return f"<Play>{escape(node_data['audio_url'])}</Play>"
    return "<Pause/>"


def gather_dtmf(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    max_digits = int(node_data["max_digits"])
    timeout = int(node_data["timeout_seconds"])
    finish_on_key = node_data.get("finish_on_key")
    action = context.get("gather_action_url", "")
    inner = ""
    if node_data.get("prompt_tts"):
        inner = f"<Say>{escape(node_data['prompt_tts'])}</Say>"
    elif node_data.get("prompt_audio_url"):
        inner = f"<Play>{escape(node_data['prompt_audio_url'])}</Play>"
    finish_attr = f' finishOnKey="{escape(finish_on_key)}"' if finish_on_key else ""
    action_attr = f' action="{escape(action)}"' if action else ""
    return f'<Gather numDigits="{max_digits}" timeout="{timeout}"{finish_attr}{action_attr}>{inner}</Gather>'


def gather_speech(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Exotel doesn't have native speech recognition — fall back to a
    record action so the call's audio is captured and can be transcribed
    later. Returns a ``<Record>`` element."""
    return record(
        {
            "max_duration_seconds": int(node_data["timeout_seconds"]),
            "play_beep": False,
        },
        context,
    )


def record(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    max_duration = int(node_data["max_duration_seconds"])
    play_beep = "true" if node_data.get("play_beep", True) else "false"
    finish_on_silence = node_data.get("finish_on_silence")
    silence_attr = f' finishOnSilence="{int(finish_on_silence)}"' if finish_on_silence is not None else ""
    action = context.get("recording_action_url", "")
    action_attr = f' action="{escape(action)}"' if action else ""
    return f'<Record maxLength="{max_duration}" playBeep="{play_beep}"{silence_attr}{action_attr}/>'


def transfer(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    target = escape(node_data["to_uri"])
    return f"<Dial>{target}</Dial>"


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
    body = "".join(chunks)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
