"""
The Cl. 5.4 availability report can be delivered, once, and never from
nothing (#231).

Run with: python manage.py test availability.tests.test_report_delivery

The arithmetic was already built and merged; nothing sent it. Two guards
matter more than the happy path:

* a duplicate schedule must not mail the same statement twice — the
  crontab on this box was doubled for months (jain-t/jina-connect#612)
* a month with no monitoring data still produces a number, and emailing a
  partner a contractual availability figure computed from no measurements
  would be worse than sending nothing
"""

import base64
import re
import zlib
from datetime import date
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from availability.models import DailyAvailability, ProbeTarget
from availability.services.monthly_report import build_availability_report
from availability.services.monthly_report_pdf import render_availability_report_pdf
from tenants.models import SentPartnerReport

RECIPIENTS = ["partner-ops@example.invalid"]


def _fill(year, month, days, failed=0):
    for day in range(1, days + 1):
        for target in (ProbeTarget.API, ProbeTarget.UI):
            DailyAvailability.objects.create(
                date=date(year, month, day),
                target=target,
                total_checks=720,
                failed_checks=failed if (day == 1 and target == ProbeTarget.API) else 0,
                probe_interval_seconds=120,
            )


def _pdf_text(pdf: bytes) -> str:
    """The words a reader would see, pulled back out of the PDF.

    Asserting on the bytes starting with %PDF only proves a file was produced;
    what matters contractually is what it says. reportlab compresses each page
    stream with ASCII85 then Flate and draws text as `(...) Tj`, so the streams
    are decoded and the drawn strings joined. Line breaks become separate runs,
    hence the whitespace squeeze — a phrase must match whether or not the
    layout happened to wrap it.
    """
    words = []
    for stream in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        try:
            page = zlib.decompress(base64.a85decode(stream.strip(), adobe=True))
        except Exception:  # not a text stream (fonts, metadata)
            continue
        drawn = re.findall(r"\((?:[^()\\]|\\.)*\)", page.decode("latin-1"))
        words.extend(run[1:-1].replace("\\(", "(").replace("\\)", ")") for run in drawn)
    return re.sub(r"\s+", " ", " ".join(words))


@override_settings(PARTNER_REPORT_RECIPIENTS=RECIPIENTS, AVAILABILITY_COMMITMENT_PERCENT="99.5")
class AvailabilityReportDeliveryTestCase(TestCase):
    def setUp(self):
        mail.outbox = []

    def _run(self, *args):
        out = StringIO()
        call_command("send_availability_report", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_a_month_with_data_is_sent_and_recorded(self):
        _fill(2026, 8, 31)

        output = self._run("--year", "2026", "--month", "8")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Service Availability Report", mail.outbox[0].subject)
        self.assertEqual(SentPartnerReport.objects.filter(kind=SentPartnerReport.KIND_AVAILABILITY).count(), 1)
        self.assertIn("Sent 2026-08", output)

    def test_the_pdf_is_attached(self):
        _fill(2026, 8, 31)

        self._run("--year", "2026", "--month", "8")

        name, content, mimetype = mail.outbox[0].attachments[0]
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))

    def test_the_body_states_whether_the_commitment_was_met(self):
        _fill(2026, 8, 31)

        self._run("--year", "2026", "--month", "8")

        self.assertIn("meets the commitment", mail.outbox[0].body)

    def test_a_breach_is_stated_plainly(self):
        # 400 failed probes = 800 minutes, well past the 99.5% allowance.
        _fill(2026, 8, 31, failed=400)

        self._run("--year", "2026", "--month", "8")

        self.assertIn("DOES NOT MEET", mail.outbox[0].body)

    # ── the two guards ────────────────────────────────────────────────────
    def test_a_month_with_no_data_is_refused(self):
        """An availability figure computed from nothing must not be sent."""
        output = self._run("--year", "2026", "--month", "8")

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(SentPartnerReport.objects.count(), 0)
        self.assertIn("computed from nothing", output)

    def test_force_overrides_the_no_data_guard(self):
        self._run("--year", "2026", "--month", "8", "--force")

        self.assertEqual(len(mail.outbox), 1)

    def test_a_second_run_sends_nothing(self):
        _fill(2026, 8, 31)
        self._run("--year", "2026", "--month", "8")
        mail.outbox = []

        output = self._run("--year", "2026", "--month", "8")

        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("Already sent", output)

    def test_partial_data_is_sent_but_flagged(self):
        """Incomplete months are still reported — silence would be worse."""
        _fill(2026, 8, 20)

        self._run("--year", "2026", "--month", "8")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("20/31", mail.outbox[0].body)

    def test_dry_run_sends_nothing(self):
        _fill(2026, 8, 31)

        output = self._run("--year", "2026", "--month", "8", "--dry-run")

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(SentPartnerReport.objects.count(), 0)
        self.assertIn("dry-run", output)

    def test_half_a_period_is_rejected(self):
        with self.assertRaises(CommandError):
            self._run("--month", "8")


@override_settings(PARTNER_REPORT_RECIPIENTS=[])
class NoRecipientsTestCase(TestCase):
    def test_nothing_is_recorded_without_recipients(self):
        _fill(2026, 8, 31)

        out = StringIO()
        call_command("send_availability_report", "--year", "2026", "--month", "8", stdout=out, stderr=out)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(SentPartnerReport.objects.count(), 0)


class CronEntryPointTestCase(TestCase):
    def test_the_cron_function_invokes_the_command(self):
        from availability.cron import send_monthly_availability_report

        with mock.patch("availability.cron.call_command") as called:
            send_monthly_availability_report()

        called.assert_called_once_with("send_availability_report")

    def test_the_job_is_scheduled(self):
        from django.conf import settings

        paths = [entry[1] for entry in settings.CRONJOBS]
        self.assertIn("availability.cron.send_monthly_availability_report", paths)


@override_settings(AVAILABILITY_COMMITMENT_PERCENT="99.5", AVAILABILITY_MIN_DAY_COVERAGE=0.9)
class PdfStatesItsBasisTestCase(TestCase):
    """The PDF has to state what changed the number, not just print it.

    A partner reads this to decide whether a service credit is owed. A bare
    percentage invites the argument; the basis forecloses it.
    """

    def _pdf(self, days):
        _fill(2026, 8, days)
        return _pdf_text(render_availability_report_pdf(build_availability_report(2026, 8)))

    def test_incomplete_coverage_is_stated_when_days_are_missing(self):
        """The worst case: partial data reads as excellent *because* monitoring failed."""
        text = self._pdf(20)

        self.assertIn("Incomplete data", text)
        self.assertIn("11 of 31 days", text)
        self.assertIn("20/31", text)

    def test_a_complete_month_is_not_flagged_incomplete(self):
        text = self._pdf(31)

        # The positive assertion first: a helper that silently extracted
        # nothing would satisfy the assertNotIn below without reading the PDF.
        self.assertIn("Service Availability Report", text)
        self.assertNotIn("Incomplete data", text)
        self.assertIn("31/31", text)

    def test_the_maintenance_exclusion_is_stated(self):
        self.assertIn("excluded from both sides", self._pdf(31))

    def test_the_summing_decision_is_stated(self):
        """Summing rather than maxing biases the figure against us; say so."""
        text = self._pdf(31)

        self.assertIn("summed across probe targets rather than taking the greatest", text)

    def test_the_probe_interval_approximation_is_stated(self):
        self.assertIn("one full probe interval", self._pdf(31))


@override_settings(PARTNER_REPORT_RECIPIENTS=RECIPIENTS, AVAILABILITY_COMMITMENT_PERCENT="99.5")
class AttachedPdfTestCase(TestCase):
    """What is asserted about the render must also hold for what is sent."""

    def setUp(self):
        mail.outbox = []

    def test_the_attached_pdf_states_incomplete_coverage(self):
        _fill(2026, 8, 20)

        call_command("send_availability_report", "--year", "2026", "--month", "8", stdout=StringIO())

        _name, content, _mimetype = mail.outbox[0].attachments[0]
        text = _pdf_text(content)
        self.assertIn("Incomplete data", text)
        self.assertIn("11 of 31 days", text)
