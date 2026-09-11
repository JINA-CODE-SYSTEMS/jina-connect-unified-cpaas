"""Deliver the Active Customer Account report (partner agreement Cl. 4.2).

This exists because the report had no way to run. It was written as a Celery
task, and this deployment has no celery beat — ``celery-v2.service`` runs a
worker only, and every periodic job goes through django-crontab. The task
was therefore unreachable by any path, and a contractual report would simply
never have been sent, silently (#230).

Delivery is recorded in SentPartnerReport and a second attempt for the same
period is refused. A schedule can fire twice; on this deployment it did, for
months (jain-t/jina-connect#612). Sending a partner the same statement twice
is worse than sending it late.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from django.db import transaction as db_transaction

from tenants.models import SentPartnerReport
from tenants.tasks import build_and_send_active_account_report, previous_month


class Command(BaseCommand):
    help = "Email the Active Customer Account report for a month (defaults to the previous one)."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Defaults to the previous calendar month.")
        parser.add_argument("--month", type=int, help="Defaults to the previous calendar month.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send again even if this period was already delivered.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be sent, and to whom, without sending.",
        )

    def handle(self, *args, **options):
        year, month = options["year"], options["month"]
        if (year is None) != (month is None):
            raise CommandError("Give both --year and --month, or neither.")
        if year is None:
            year, month = previous_month()
        if not 1 <= month <= 12:
            raise CommandError(f"{month} is not a month.")

        already = SentPartnerReport.objects.filter(
            kind=SentPartnerReport.KIND_ACTIVE_ACCOUNTS, year=year, month=month
        ).first()

        if already and not options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Already sent for {year}-{month:02d} at {already.sent_at:%Y-%m-%d %H:%M} "
                    f"to {already.recipients}. Nothing sent. Use --force to send again."
                )
            )
            return

        if options["dry_run"]:
            from django.conf import settings

            recipients = settings.PARTNER_REPORT_RECIPIENTS
            self.stdout.write(
                self.style.NOTICE(f"--dry-run: would send {year}-{month:02d} to {recipients or '(none configured)'}.")
            )
            return

        sent_to = build_and_send_active_account_report(year, month)
        if not sent_to:
            # No recipients configured. Not an error — the deployment may not
            # have a partner — but it must not be recorded as delivered.
            self.stdout.write(self.style.WARNING(f"No recipients configured; {year}-{month:02d} not sent."))
            return

        from django.conf import settings

        try:
            with db_transaction.atomic():
                SentPartnerReport.objects.update_or_create(
                    kind=SentPartnerReport.KIND_ACTIVE_ACCOUNTS,
                    year=year,
                    month=month,
                    defaults={"recipients": ", ".join(settings.PARTNER_REPORT_RECIPIENTS)},
                )
        except IntegrityError:
            # Two runs raced. The mail has gone either way; say so rather
            # than fail, because the operator needs to know it was sent.
            self.stdout.write(self.style.WARNING("Delivery recorded by a concurrent run."))

        self.stdout.write(self.style.SUCCESS(f"Sent {year}-{month:02d} to {sent_to} recipient(s)."))
