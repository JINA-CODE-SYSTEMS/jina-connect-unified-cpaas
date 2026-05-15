"""TwiML emitter — Twilio Voice control language.

Emits the XML that Twilio expects in response to its ``answer`` and
``gather`` webhooks. Each handler corresponds to one voice-node type
in ``chat_flow``; the IVR compiler (#168) walks the flow and calls
these to assemble the per-call TwiML response.

For #160 we only need the static-play path (``play`` + ``hangup``) —
full IVR (gather, record, transfer) lands with #168. The handlers are
all defined here so #168 can wire them into the compiler without
re-touching this file.
"""

from __future__ import annotations

from typing import Any, Callable
from xml.sax.saxutils import escape

# ─────────────────────────────────────────────────────────────────────────────
# Per-node-type emitters
#
# Each emitter takes ``(node_data, context)`` and returns a single TwiML
# element as a string. The compiler concatenates the returned chunks and
# wraps them in ``<Response>...</Response>`` via ``assemble``.
# ─────────────────────────────────────────────────────────────────────────────


def play(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Say>`` (TTS) or ``<Play>`` (pre-recorded audio).

    Falls back to ``<Say>`` if both are set, since TTS is the broader
    default. The IVR node validator (#168) enforces at-least-one.
    """
    if node_data.get("tts_text"):
        text = escape(node_data["tts_text"])
        voice = node_data.get("tts_voice") or context.get("default_tts_voice")
        language = node_data.get("tts_language") or context.get("default_tts_language")
        attrs = ""
        if voice:
            attrs += f' voice="{escape(voice)}"'
        if language:
            attrs += f' language="{escape(language)}"'
        return f"<Say{attrs}>{text}</Say>"
    if node_data.get("audio_url"):
        return f"<Play>{escape(node_data['audio_url'])}</Play>"
    # Neither set — validator should have caught this. Emit a silent pause
    # rather than empty TwiML so callers don't see a stalled call.
    return '<Pause length="1"/>'


def gather_dtmf(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Gather input="dtmf" ...>``.

    The ``action`` URL points back to the gather webhook so the next
    flow step can resolve the digits.
    """
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
    return (
        f'<Gather input="dtmf" numDigits="{max_digits}" timeout="{timeout}"{finish_attr}{action_attr}>{inner}</Gather>'
    )


def gather_speech(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Gather input="speech" ...>``."""
    language = node_data["language"]
    timeout = int(node_data["timeout_seconds"])
    action = context.get("gather_action_url", "")
    action_attr = f' action="{escape(action)}"' if action else ""
    return f'<Gather input="speech" language="{escape(language)}" speechTimeout="{timeout}"{action_attr}/>'


def record(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Record>``."""
    max_duration = int(node_data["max_duration_seconds"])
    play_beep = "true" if node_data.get("play_beep", True) else "false"
    finish_on_silence = node_data.get("finish_on_silence")
    action = context.get("recording_action_url", "")
    silence_attr = f' finishOnKey="" silenceTimeout="{int(finish_on_silence)}"' if finish_on_silence is not None else ""
    action_attr = f' action="{escape(action)}"' if action else ""
    return f'<Record maxLength="{max_duration}" playBeep="{play_beep}"{silence_attr}{action_attr}/>'


def transfer(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Dial>`` — Twilio's transfer primitive."""
    target = escape(node_data["to_uri"])
    return f"<Dial>{target}</Dial>"


def hangup(node_data: dict[str, Any], context: dict[str, Any]) -> str:
    """Emit ``<Hangup/>``."""
    return "<Hangup/>"


# ─────────────────────────────────────────────────────────────────────────────
# Handler lookup + assembly
# ─────────────────────────────────────────────────────────────────────────────


_HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], str]] = {
    "voice.play": play,
    "voice.gather_dtmf": gather_dtmf,
    "voice.gather_speech": gather_speech,
    "voice.record": record,
    "voice.transfer": transfer,
    "voice.hangup": hangup,
}


def get_handler(type_id: str):
    """Return the emitter for a node ``type_id`` or ``None`` if unknown."""
    return _HANDLERS.get(type_id)


def assemble(chunks: list[str]) -> str:
    """Wrap a list of TwiML elements in ``<Response>...</Response>``.

    Always emits the XML prolog so Twilio's parser is content with the
    Content-Type=application/xml response we return.
    """
    body = "".join(chunks)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
