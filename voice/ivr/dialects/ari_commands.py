"""ARI command emitters (#163).

SIP "dialect" — instead of returning XML, each emitter returns a dict
describing an ARI command for the SIP adapter to execute against
``AriClient``. The IVR compiler walks the flow and the SIP adapter
runs each command in sequence.
"""

from __future__ import annotations

from typing import Any, Callable


def play(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Emit a ``play`` command.

    Audio URLs translate to Asterisk media URIs:
      * ``http(s)://...`` → ``sound:<url>`` (Asterisk fetches + caches)
      * TTS text → Asterisk TTS engine (e.g. ``synthesis:<text>``)
        which requires a TTS module configured server-side.
    """
    if node_data.get("audio_url"):
        return {"op": "play", "media": f"sound:{node_data['audio_url']}"}
    if node_data.get("tts_text"):
        return {
            "op": "play",
            "media": f"synthesis:{node_data['tts_text']}",
            "tts_voice": node_data.get("tts_voice"),
            "tts_language": node_data.get("tts_language"),
        }
    # Validator should have caught this — keep the call moving with
    # a short silent pause.
    return {"op": "play", "media": "sound:silence/1"}


def gather_dtmf(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "gather_dtmf",
        "max_digits": int(node_data["max_digits"]),
        "timeout_seconds": int(node_data["timeout_seconds"]),
        "finish_on_key": node_data.get("finish_on_key"),
    }


def gather_speech(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "gather_speech",
        "language": node_data["language"],
        "timeout_seconds": int(node_data["timeout_seconds"]),
    }


def record(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "record",
        "max_duration_seconds": int(node_data["max_duration_seconds"]),
        "play_beep": bool(node_data.get("play_beep", True)),
        "finish_on_silence": node_data.get("finish_on_silence"),
    }


def transfer(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {"op": "transfer", "to_uri": node_data["to_uri"]}


def hangup(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {"op": "hangup"}


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
    """Return the ordered command list as-is.

    Unlike XML dialects there's no wrapper element — the SIP adapter
    iterates and calls ARI for each command.
    """
    return list(chunks)
