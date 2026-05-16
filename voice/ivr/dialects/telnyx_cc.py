"""Telnyx Call Control "dialect" (#166).

Telnyx is command-driven — instead of returning XML/JSON from the
answer webhook, each step is a separate ``POST /v2/calls/{cc_id}/actions/<verb>``.

Each emitter here returns ``(verb, payload)`` describing one command;
the adapter executes them in sequence. Same shape as the SIP/ARI
dialect — both produce a list of "do this next" steps rather than a
single response body.
"""

from __future__ import annotations

from typing import Any, Callable


def play(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Emit ``speak`` (TTS) or ``playback_start`` (audio URL)."""
    if node_data.get("tts_text"):
        payload: dict[str, Any] = {"payload": node_data["tts_text"]}
        if node_data.get("tts_voice"):
            payload["voice"] = node_data["tts_voice"]
        if node_data.get("tts_language"):
            payload["language"] = node_data["tts_language"]
        return {"verb": "speak", "payload": payload}
    if node_data.get("audio_url"):
        return {
            "verb": "playback_start",
            "payload": {"audio_url": node_data["audio_url"]},
        }
    # Validator should have caught this — emit a no-op speak so the
    # call doesn't stall.
    return {"verb": "speak", "payload": {"payload": ""}}


def gather_dtmf(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Emit ``gather_using_speak`` if a TTS prompt is set, else
    ``gather_using_audio`` for a recorded prompt, else ``gather`` bare.

    Telnyx splits gather into three flavours by prompt type — we pick
    based on what the node data carries.
    """
    base: dict[str, Any] = {
        "maximum_digits": int(node_data["max_digits"]),
        "timeout_millis": int(node_data["timeout_seconds"]) * 1000,
    }
    if node_data.get("finish_on_key"):
        base["terminating_digit"] = node_data["finish_on_key"]

    if node_data.get("prompt_tts"):
        return {
            "verb": "gather_using_speak",
            "payload": {**base, "payload": node_data["prompt_tts"]},
        }
    if node_data.get("prompt_audio_url"):
        return {
            "verb": "gather_using_audio",
            "payload": {**base, "audio_url": node_data["prompt_audio_url"]},
        }
    return {"verb": "gather", "payload": base}


def gather_speech(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Telnyx delivers speech transcription via the AI gather endpoints;
    we use ``transcription_start`` which records + transcribes while
    the call continues."""
    return {
        "verb": "transcription_start",
        "payload": {
            "language": node_data["language"],
            "transcription_engine": "B",  # Telnyx default
        },
    }


def record(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "verb": "record_start",
        "payload": {
            "format": "mp3",
            "channels": "single",
            "play_beep": bool(node_data.get("play_beep", True)),
            "max_length": int(node_data["max_duration_seconds"]),
        },
    }


def transfer(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {"verb": "transfer", "payload": {"to": node_data["to_uri"]}}


def hangup(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {"verb": "hangup", "payload": {}}


_HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "voice.play": play,
    "voice.gather_dtmf": gather_dtmf,
    "voice.gather_speech": gather_speech,
    "voice.record": record,
    "voice.transfer": transfer,
    "voice.hangup": hangup,
}


def get_handler(type_id: str):
    return _HANDLERS.get(type_id)


def assemble(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the ordered command list — no wrapper."""
    return list(chunks)
