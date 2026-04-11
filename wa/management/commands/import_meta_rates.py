"""
Management command to import Meta WhatsApp base rates from a CSV file.

Usage:
    python manage.py import_meta_rates --file meta_rates_2026_02.csv
    python manage.py import_meta_rates --file rates.csv --effective-from 2026-02-01
    python manage.py import_meta_rates --file rates.csv --dry-run

Expected CSV format:
    destination_country,message_type,rate
    IN,MARKETING,0.009900
    IN,UTILITY,0.004200
    IN,AUTHENTICATION,0.003100
    US,MARKETING,0.025000
    ...

- ``destination_country`` — ISO 3166-1 alpha-2 (e.g. IN, US, BR)
- ``message_type`` — MARKETING | UTILITY | AUTHENTICATION
- ``rate`` — USD decimal (Meta's billing currency)
"""

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from wa.models import MessageTypeChoices, MetaBaseRate

VALID_MESSAGE_TYPES = set(MessageTypeChoices.values)


class Command(BaseCommand):
    help = "Import Meta WhatsApp base rates from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            required=True,
            help="Path to CSV file containing Meta base rates.",
        )
        parser.add_argument(
            "--effective-from",
            type=str,
            default=None,
            help="Effective date (YYYY-MM-DD). Defaults to 1st of current month.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate without writing to the database.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["file"])
        if not csv_path.exists():
            raise CommandError(f"File not found: {csv_path}")

        # Determine effective_from
        if options["effective_from"]:
            try:
                effective_from = date.fromisoformat(options["effective_from"])
            except ValueError:
                raise CommandError("Invalid date format. Use YYYY-MM-DD.")
        else:
            today = timezone.now().date()
            effective_from = today.replace(day=1)

        dry_run = options["dry_run"]
        self.stdout.write(f"Effective from: {effective_from}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database changes"))

        # Parse CSV
        rows = self._parse_csv(csv_path)
        self.stdout.write(f"Parsed {len(rows)} valid rows from {csv_path.name}")

        if dry_run:
            for r in rows[:10]:
                self.stdout.write(f"  {r['country']}/{r['type']} = ${r['rate']}")
            if len(rows) > 10:
                self.stdout.write(f"  ... and {len(rows) - 10} more")
            return

        # Write to DB
        created, updated = self._import_rates(rows, effective_from)
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created} created, {updated} updated, {len(rows)} total rows for {effective_from}"
            )
        )

    def _parse_csv(self, csv_path: Path) -> list[dict]:
        """Parse and validate CSV rows."""
        rows = []
        errors = []

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            # Validate headers
            required = {"destination_country", "message_type", "rate"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise CommandError(f"CSV must have columns: {', '.join(sorted(required))}. Found: {reader.fieldnames}")

            for i, row in enumerate(reader, start=2):  # start=2 accounts for header
                country = (row.get("destination_country") or "").strip().upper()
                msg_type = (row.get("message_type") or "").strip().upper()
                rate_str = (row.get("rate") or "").strip()

                # Validate
                if len(country) != 2:
                    errors.append(f"Row {i}: invalid country '{country}'")
                    continue
                if msg_type not in VALID_MESSAGE_TYPES:
                    errors.append(f"Row {i}: invalid message_type '{msg_type}'")
                    continue
                try:
                    rate = Decimal(rate_str)
                    if rate < 0:
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    errors.append(f"Row {i}: invalid rate '{rate_str}'")
                    continue

                rows.append({"country": country, "type": msg_type, "rate": rate})

        if errors:
            for e in errors[:20]:
                self.stderr.write(self.style.ERROR(e))
            if len(errors) > 20:
                self.stderr.write(f"... and {len(errors) - 20} more errors")
            raise CommandError(f"{len(errors)} validation errors found in CSV.")

        return rows

    @transaction.atomic
    def _import_rates(self, rows: list[dict], effective_from: date) -> tuple[int, int]:
        """
        Import rates: mark old ones as not current, upsert new ones.
        Returns (created_count, updated_count).
        """
        created = 0
        updated = 0

        # Mark previous rates for same effective_from as not current
        # (in case of re-import for same period)
        MetaBaseRate.objects.filter(
            effective_from=effective_from,
        ).update(is_current=False)

        # Also close out any earlier "current" rates
        MetaBaseRate.objects.filter(
            is_current=True,
            effective_from__lt=effective_from,
        ).update(is_current=False, effective_to=effective_from)

        for row in rows:
            _, was_created = MetaBaseRate.objects.update_or_create(
                destination_country=row["country"],
                message_type=row["type"],
                effective_from=effective_from,
                defaults={
                    "rate": row["rate"],
                    "is_current": True,
                    "effective_to": None,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return created, updated
