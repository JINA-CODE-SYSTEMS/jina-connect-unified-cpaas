"""Vonage NCCO (Nexmo Call Control Object) emitter (#165).

Unlike TwiML / Plivo XML, NCCO is **JSON** — a list of action dicts
returned from the answer webhook. Each emitter returns one action dict;
``assemble`` returns the list as-is.

Example NCCO::

    [
      {"action": "talk", "text": "Hello", "voiceName": "Aditi", "language": "en-IN"},
      {"action": "input", "type": ["dtmf"], "dtmf": {"maxDigits": 1, "timeOut": 3}},
      {"action": "connect", "endpoint": [{"type": "phone", "number": "+14155550100"}]}
    ]

NCCO docs: https://developer.vonage.com/en/voice/voice-api/ncco-reference
"""

from __future__ import annotations

from typing import Any, Callable


def play(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Emit a ``talk`` action (TTS) or ``stream`` action (audio URL).

    Vonage's ``voiceName`` attribute carries both voice + (implicit)
    language; ``language`` is set separately too when supplied.
    """
    if node_data.get("tts_text"):
        action: dict[str, Any] = {"action": "talk", "text": node_data["tts_text"]}
        if node_data.get("tts_voice"):
            action["voiceName"] = node_data["tts_voice"]
        if node_data.get("tts_language"):
            action["language"] = node_data["tts_language"]
        return action
    if node_data.get("audio_url"):
        # NCCO ``stream`` takes a list of URLs (Vonage tries each in
        # order until one succeeds).
        return {"action": "stream", "streamUrl": [node_data["audio_url"]]}
    # Validator should have caught this — emit a brief talk so the
    # call doesn't hang silently.
    return {"action": "talk", "text": ""}


def gather_dtmf(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Emit an ``input`` action configured for DTMF.

    ``eventUrl`` is where Vonage POSTs the gathered input — wired to
    the gather webhook by the adapter when needed (full IVR loop in #168).
    """
    action: dict[str, Any] = {
        "action": "input",
        "type": ["dtmf"],
        "dtmf": {
            "maxDigits": int(node_data["max_digits"]),
            "timeOut": int(node_data["timeout_seconds"]),
        },
    }
    if node_data.get("finish_on_key"):
        action["dtmf"]["submitOnHash"] = node_data["finish_on_key"] == "#"
    if context.get("gather_action_url"):
        action["eventUrl"] = [context["gather_action_url"]]
    return action


def gather_speech(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Emit an ``input`` action configured for speech."""
    action: dict[str, Any] = {
        "action": "input",
        "type": ["speech"],
        "speech": {
            "language": node_data["language"],
            "endOnSilence": float(node_data["timeout_seconds"]) / 2,  # heuristic
        },
    }
    if context.get("gather_action_url"):
        action["eventUrl"] = [context["gather_action_url"]]
    return action


def record(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Emit a ``record`` action."""
    action: dict[str, Any] = {
        "action": "record",
        "format": "mp3",
        "endOnSilence": int(node_data.get("finish_on_silence") or 5),
        "endOnKey": "#",
        "beepStart": bool(node_data.get("play_beep", True)),
        "timeOut": int(node_data["max_duration_seconds"]),
    }
    if context.get("recording_action_url"):
        action["eventUrl"] = [context["recording_action_url"]]
    return action


def transfer(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Emit a ``connect`` action with a phone endpoint."""
    return {
        "action": "connect",
        "endpoint": [{"type": "phone", "number": node_data["to_uri"]}],
    }


def hangup(node_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """NCCO has no explicit ``hangup`` — the call ends when the action
    list runs out. Emit a no-op talk so the list isn't empty when
    hangup is the only step (Vonage rejects empty NCCOs).
    """
    return {"action": "talk", "text": "", "bargeIn": False}


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
    """Return the ordered NCCO list as-is (no wrapper)."""
    return list(chunks)
