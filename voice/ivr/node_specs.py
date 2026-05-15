"""Voice node-type registrations for ``chat_flow.node_registry`` (#168).

Registers six voice-only node types so a flow author can mix them
with the channel-agnostic nodes (``conditional`` / ``set_variable`` /
``http_call`` / ``wait``). The node registry validates at flow-save:
a flow with ``voice.play`` on a WhatsApp platform is rejected with
a clear error.

``register_voice_node_types`` is idempotent (the registry itself
no-ops on identical re-registration). ``voice/apps.py`` calls it
once on app ready.
"""

from __future__ import annotations

from chat_flow.node_registry import NodeTypeSpec, register_node_type
from jina_connect.platform_choices import PlatformChoices

VOICE_ONLY = frozenset([PlatformChoices.VOICE])


def _validate_play(data: dict) -> list[str]:
    """``voice.play`` needs at least one of ``audio_url`` / ``tts_text``."""
    if not data.get("audio_url") and not data.get("tts_text"):
        return ["voice.play requires either audio_url or tts_text"]
    return []


def register_voice_node_types() -> None:
    """Register every voice node type. Safe to call repeatedly."""
    register_node_type(
        NodeTypeSpec(
            type_id="voice.play",
            display_name="Play audio or TTS",
            description="Play pre-recorded audio or speak a TTS prompt.",
            supported_platforms=VOICE_ONLY,
            required_data_fields=frozenset(),
            optional_data_fields=frozenset(["audio_url", "tts_text", "tts_voice", "tts_language"]),
            validator=_validate_play,
        )
    )
    register_node_type(
        NodeTypeSpec(
            type_id="voice.gather_dtmf",
            display_name="Gather DTMF",
            description="Collect a fixed-length sequence of digit presses.",
            supported_platforms=VOICE_ONLY,
            required_data_fields=frozenset(["max_digits", "timeout_seconds"]),
            optional_data_fields=frozenset(["finish_on_key", "prompt_tts", "prompt_audio_url"]),
        )
    )
    register_node_type(
        NodeTypeSpec(
            type_id="voice.gather_speech",
            display_name="Gather speech",
            description="Collect spoken input (where supported by the provider).",
            supported_platforms=VOICE_ONLY,
            required_data_fields=frozenset(["language", "timeout_seconds"]),
        )
    )
    register_node_type(
        NodeTypeSpec(
            type_id="voice.record",
            display_name="Record caller",
            description="Record the caller's audio into our storage bucket.",
            supported_platforms=VOICE_ONLY,
            required_data_fields=frozenset(["max_duration_seconds"]),
            optional_data_fields=frozenset(["finish_on_silence", "play_beep"]),
        )
    )
    register_node_type(
        NodeTypeSpec(
            type_id="voice.transfer",
            display_name="Transfer call",
            description="Connect / transfer the call to another number or SIP URI.",
            supported_platforms=VOICE_ONLY,
            required_data_fields=frozenset(["to_uri"]),
        )
    )
    register_node_type(
        NodeTypeSpec(
            type_id="voice.hangup",
            display_name="End call",
            description="Hang up the call.",
            supported_platforms=VOICE_ONLY,
            required_data_fields=frozenset(),
        )
    )
