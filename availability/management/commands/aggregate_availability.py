"""Copy the previous day's synthetic-monitoring results into DailyAvailability.

Grafana Cloud's free tier keeps metrics for 14 days; a calendar-month report
needs 31, and a contractual figure that may have to be defended in eighteen
months should not depend on a third party's retention policy at all. So the
counts are copied here nightly, as raw counts rather than a percentage, and
kept.

Two behaviours are deliberate:

**A day with no data gets no row.** Writing ``total_checks=0`` would make the
day look reported while carrying no evidence, and the monthly report counts a
day as covered when a row exists. A missing row is the honest record of a
missing day, and the report already says so out loud.

**Re-running is safe.** The unique constraint is ``(date, target)`` and the
command upserts, so a backfill after a monitoring outage overwrites rather
than collides. Grafana's retention is the only limit on how far back
``--days`` can usefully reach.
"""

from datetime import date as date_cls
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from availability.models import DailyAvailability, ProbeTarget
from availability.services.grafana import (
    GrafanaNotConfigured,
    GrafanaUnavailable,
    fetch_probe_results,
)


def day_bounds(day: date_cls) -> tuple[datetime, datetime]:
    """Local midnight to local midnight, so a DST day is 23 or 25 hours.

    The monthly report bounds its period in local time too (#229); if the two
    disagreed, the days at each end of a month would be counted twice or not
    at all.
    """
    tz = timezone.get_current_timezone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


class Command(BaseCommand):
    help = "Aggregate synthetic-monitoring results into DailyAvailability (defaults to yesterday)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Aggregate this day only, as YYYY-MM-DD. Defaults to yesterday.")
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="Backfill this many days, ending yesterday (or at --date). Default 1.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report what would be written, without writing.")

    def handle(self, *args, **options):
        last_day = self._resolve_last_day(options["date"])
        days = options["days"]
        if days < 1:
            raise CommandError("--days must be at least 1.")

        interval = int(settings.AVAILABILITY_PROBE_INTERVAL_SECONDS)
        if interval < 1:
            raise CommandError("AVAILABILITY_PROBE_INTERVAL_SECONDS must be positive.")

        written = skipped = 0
        for offset in reversed(range(days)):
            day = last_day - timedelta(days=offset)
            for target, _label in ProbeTarget.choices:
                if self._aggregate_one(day, target, interval, options["dry_run"]):
                    written += 1
                else:
                    skipped += 1

        verb = "would write" if options["dry_run"] else "wrote"
        summary = f"{verb} {written} row(s) across {days} day(s)"
        if skipped:
            self.stdout.write(self.style.WARNING(f"{summary}; {skipped} skipped for want of data."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{summary}."))

    def _resolve_last_day(self, raw: str | None) -> date_cls:
        yesterday = timezone.localdate() - timedelta(days=1)
        if not raw:
            return yesterday
        try:
            day = date_cls.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError(f"--date must be YYYY-MM-DD, got {raw!r}.") from exc
        if day > yesterday:
            raise CommandError(
                f"{day} is not over yet in {timezone.get_current_timezone()}. "
                f"Aggregating a partial day would record it as a short day and flag the month incomplete."
            )
        return day

    def _aggregate_one(self, day: date_cls, target: str, interval: int, dry_run: bool) -> bool:
        """Fetch and store one target's results for one day. True if a row was written."""
        start, end = day_bounds(day)
        try:
            total, failed = fetch_probe_results(target, start, end, interval)
        except GrafanaNotConfigured as exc:
            # Nothing about the next day or target will be different, so stop
            # rather than repeating the same error once per target per day.
            raise CommandError(str(exc)) from exc
        except GrafanaUnavailable as exc:
            self.stderr.write(self.style.ERROR(f"{day} {target}: {exc}"))
            return False

        expected = int((end - start).total_seconds() // interval)
        if total == 0:
            self.stderr.write(
                self.style.ERROR(
                    f"{day} {target}: Grafana returned no check results. Leaving the day unrecorded, "
                    f"which the monthly report counts as missing rather than healthy."
                )
            )
            return False

        note = f"{day} {target}: {total}/{expected} intervals, {failed} failed"
        if total < expected:
            note += " (short — monitoring gap or a check created mid-day)"

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"--dry-run: {note}"))
            return True

        DailyAvailability.objects.update_or_create(
            date=day,
            target=target,
            defaults={
                "total_checks": total,
                "failed_checks": failed,
                "probe_interval_seconds": interval,
                "source": "grafana",
            },
        )
        self.stdout.write(note)
        return True
