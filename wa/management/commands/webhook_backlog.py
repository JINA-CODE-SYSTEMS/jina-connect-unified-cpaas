"""Report inbound webhook events that were stored but never processed.

The backlog is the shared symptom of several unrelated faults — a dead Celery
broker, a worker that is not running, a parser raising on every message — and
until now the only way to see it was to query the database by hand (#269).

Exits non-zero when the backlog exceeds ``--max``, so it can be wired to cron
or a monitoring check without any parsing of its output.

Usage:
    python manage.py webhook_backlog
    python manage.py webhook_backlog --older-than 15 --max 0
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Report WAWebhookEvent rows still unprocessed, and fail if there are too many."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than",
            type=int,
            default=10,
            metavar="MINUTES",
            help=(
                "Only count events older than this many minutes (default: 10). "
                "Events queued seconds ago are in flight, not stuck."
            ),
        )
        parser.add_argument(
            "--max",
            type=int,
            default=0,
            help="Exit non-zero when the backlog exceeds this (default: 0).",
        )
        parser.add_argument(
            "--show",
            type=int,
            default=5,
            help="How many of the oldest stuck events to list (default: 5).",
        )

    def handle(self, *args, **options):
        from wa.models import WAWebhookEvent

        cutoff = timezone.now() - timezone.timedelta(minutes=options["older_than"])
        stuck = WAWebhookEvent.objects.filter(is_processed=False, created_at__lt=cutoff).order_by("created_at")
        count = stuck.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS(f"No webhook events unprocessed for over {options['older_than']}m."))
            return

        style = self.style.ERROR if count > options["max"] else self.style.WARNING
        self.stdout.write(style(f"{count} webhook event(s) unprocessed for over {options['older_than']}m."))

        for event in stuck[: options["show"]]:
            self.stdout.write(
                f"  {event.pk}  {event.created_at:%Y-%m-%d %H:%M}  "
                f"type={event.event_type or '?'}  bsp={event.bsp or '?'}  "
                f"err={(event.error_message or '')[:80]}"
            )
        if count > options["show"]:
            self.stdout.write(f"  … and {count - options['show']} more")

        if count > options["max"]:
            # Non-zero so cron or a monitoring check notices without parsing text.
            raise SystemExit(1)
