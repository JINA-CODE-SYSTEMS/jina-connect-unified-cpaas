"""Voice app configuration.

``ready()`` registers the voice channel with the channel registry. The
adapter registry itself is populated by each concrete adapter module
(`voice/adapters/http_voice/twilio.py` etc.) calling
``register_voice_adapter`` at import time — added by later PRs (#160
onwards). Until then ``voice_adapter_factory`` raises ``NotImplementedError``
for any provider that asks for an adapter.

IVR node-type registration arrives with #168.
"""

from __future__ import annotations

from django.apps import AppConfig


class VoiceConfig(AppConfig):
    name = "voice"
    default_auto_field = "django.db.models.BigAutoField"
    verbose_name = "Voice"

    def ready(self) -> None:
        # Register the voice channel so `get_channel_adapter("VOICE", tenant)`
        # routes to our factory. Concrete adapters self-register on import,
        # so importing each one here populates ``voice.adapters.registry``.
        from jina_connect.channel_registry import register_channel
        from jina_connect.platform_choices import PlatformChoices
        from voice.adapters.registry import voice_adapter_factory

        register_channel(PlatformChoices.VOICE, voice_adapter_factory)

        # Import concrete adapter modules so they register themselves.
        # Wrapped in try/except to keep app startup resilient against a
        # single broken provider — the registry just won't have that entry.
        try:
            import voice.adapters.http_voice.twilio  # noqa: F401
        except Exception:  # pragma: no cover — defensive
            import logging

            logging.getLogger(__name__).exception("Failed to import Twilio voice adapter; provider unavailable.")

        try:
            import voice.adapters.sip  # noqa: F401
        except Exception:  # pragma: no cover — defensive
            import logging

            logging.getLogger(__name__).exception("Failed to import SIP voice adapter; provider unavailable.")

        # Hook the post-save signal that mirrors call lifecycle into
        # team_inbox + billing pipelines. Importing voice.signals also
        # wires the SIP provisioning hook below.
        import voice.signals  # noqa: F401
