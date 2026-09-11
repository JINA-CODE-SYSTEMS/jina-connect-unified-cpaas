"""Deliver the monthly availability report (partner agreement Cl. 5.4).

Same shape as the Cl. 4.2 report: delivery is recorded so a duplicate
schedule cannot send the same statement twice (jain-t/jina-connect#612).

One difference that matters. This report is computed from monitoring data,
and if nothing wrote that data the arithmetic still produces a number —
0 days covered, and an availability figure derived from nothing. Emailing a
partner a contractual availability statement built on no measurements would
be worse than sending nothing, so the command refuses unless forced.
"""

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from django.db import transaction as db_transaction

from availability.services.monthly_report import build_availability_report
from availability.services.monthly_report_pdf import render_availability_report_pdf
from tenants.models import SentPartnerReport
from tenants.tasks import previous_month


class Command(BaseCommand):
    help = "Email the monthly service availability report (defaults to the previous month)."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Defaults to the previous calendar month.")
        parser.add_argument("--month", type=int, help="Defaults to the previous calendar month.")
        parser.add_argument("--force", action="store_true", help="Send despite missing data or a prior send.")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be sent, without sending.")

    def handle(self, *args, **options):
        year, month = options["year"], options["month"]
        if (year is None) != (month is None):
            raise CommandError("Give both --year and --month, or neither.")
        if year is None:
            year, month = previous_month()
        if not 1 <= month <= 12:
            raise CommandError(f"{month} is not a month.")

        already = SentPartnerReport.objects.filter(
            kind=SentPartnerReport.KIND_AVAILABILITY, year=year, month=month
        ).first()
        if already and not options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Already sent for {year}-{month:02d} at {already.sent_at:%Y-%m-%d %H:%M}. "
                    f"Nothing sent. Use --force to send again."
                )
            )
            return

        report = build_availability_report(year, month)

        if report.days_covered == 0 and not options["force"]:
            self.stdout.write(
                self.style.ERROR(
                    f"No monitoring data for {year}-{month:02d}: 0 of {report.days_expected} days covered. "
                    f"Refusing to send an availability statement computed from nothing. "
                    f"Check the nightly aggregation, then re-run, or use --force."
                )
            )
            return

        recipients = settings.PARTNER_REPORT_RECIPIENTS
        if not recipients:
            self.stdout.write(self.style.WARNING(f"No recipients configured; {year}-{month:02d} not sent."))
            return

        if options["dry_run"]:
            self.stdout.write(
                self.style.NOTICE(
                    f"--dry-run: would send {year}-{month:02d} to {recipients}. "
                    f"Availability {report.availability}% against {report.commitment}%, "
                    f"{report.days_covered}/{report.days_expected} days covered."
                )
            )
            return

        pdf = render_availability_report_pdf(report)
        status = "meets" if report.meets_commitment else "DOES NOT MEET"
        message = EmailMessage(
            subject=f"Service Availability Report — {report.label}",
            body=(
                f"Service availability for {report.label}.\n\n"
                f"Measured availability: {report.availability}%\n"
                f"Commitment: {report.commitment}% — {status} the commitment\n"
                f"Days with complete data: {report.days_covered}/{report.days_expected}\n\n"
                "The attached PDF states the basis of the calculation.\n"
            ),
            to=recipients,
        )
        message.attach(f"availability-{year}-{month:02d}.pdf", pdf, "application/pdf")
        message.send(fail_silently=False)

        try:
            with db_transaction.atomic():
                SentPartnerReport.objects.update_or_create(
                    kind=SentPartnerReport.KIND_AVAILABILITY,
                    year=year,
                    month=month,
                    defaults={"recipients": ", ".join(recipients)},
                )
        except IntegrityError:
            self.stdout.write(self.style.WARNING("Delivery recorded by a concurrent run."))

        self.stdout.write(self.style.SUCCESS(f"Sent {year}-{month:02d} to {len(recipients)} recipient(s)."))
