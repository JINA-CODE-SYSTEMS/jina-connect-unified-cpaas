"""``manage.py import_voice_cdr`` — load a vendor CDR into VoiceCall rows (#170)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Import a vendor CDR file and reconcile rows against VoiceCall.cost_*."

    def add_arguments(self, parser):
        parser.add_argument("file_path", help="Path to the CDR file (CSV).")
        parser.add_argument(
            "--config-id",
            required=True,
            help="VoiceProviderConfig UUID the CDR belongs to.",
        )
        parser.add_argument(
            "--vendor",
            default="generic",
            help="Registered CDR vendor parser name (default: generic).",
        )
        parser.add_argument(
            "--discrepancy-ratio",
            default="0.10",
            help=("Local-vs-carrier cost ratio above which a discrepancy is logged for review (default 0.10 = 10%%)."),
        )

    def handle(self, *args, **options):
        from voice.billing.importers import import_cdr_file

        try:
            config_id = UUID(options["config_id"])
        except (ValueError, KeyError) as exc:
            raise CommandError(f"--config-id must be a UUID: {exc}") from exc

        try:
            ratio = Decimal(options["discrepancy_ratio"])
        except InvalidOperation as exc:
            raise CommandError(f"--discrepancy-ratio must be a decimal: {exc}") from exc

        summary = import_cdr_file(
            options["file_path"],
            config_id,
            vendor=options["vendor"],
            discrepancy_ratio=ratio,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "CDR import complete: "
                f"matched={summary['matched']} "
                f"updated={summary['updated']} "
                f"skipped={summary['skipped']} "
                f"discrepancies={summary['discrepancies']}"
            )
        )
