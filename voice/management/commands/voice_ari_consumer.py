"""``manage.py voice_ari_consumer`` — long-running Asterisk ARI listener.

Run one instance per Asterisk host. Translates ARI WebSocket events
into ``voice.tasks.process_call_status`` payloads so SIP calls feed
the same state machine as HTTP voice webhooks.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from voice.sip_config.ari_consumer import run_consumer


class Command(BaseCommand):
    help = "Run the Asterisk ARI events consumer (long-running)."

    def handle(self, *args, **options):  # noqa: ARG002
        self.stdout.write(self.style.NOTICE("Starting ARI consumer…"))
        run_consumer()
