"""
The Cl. 4.2 report can actually be delivered, once per period (#230).

Run with: python manage.py test tenants.tests.test_report_scheduling

It was written as a Celery task, and this deployment has no celery beat —
celery-v2.service runs a worker only and every periodic job goes through
django-crontab. The task was unreachable by any path, so a contractual
report would never have been sent and nothing would have said so.

The once-per-period guard is not belt-and-braces: the django-crontab entries
on this box were installed in two crontabs and every job ran twice for
months (jain-t/jina-connect#612).
"""

from datetime import date
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from tenants.models import SentPartnerReport, Tenant
from tenants.tasks import previous_month

RECIPIENTS = ["partner-ops@example.invalid"]


@override_settings(PARTNER_REPORT_RECIPIENTS=RECIPIENTS)
class SendActiveAccountReportCommandTestCase(TestCase):
    def setUp(self):
        tenant = Tenant.objects.create(name="Billed Co")
        Tenant.objects.filter(pk=tenant.pk).update(created_at=date(2026, 1, 1))
        mail.outbox = []

    def _run(self, *args):
        out = StringIO()
        call_command("send_active_account_report", *args, stdout=out, stderr=out)
        return out.getvalue()

    def _sent_count(self):
        return SentPartnerReport.objects.filter(kind=SentPartnerReport.KIND_ACTIVE_ACCOUNTS).count()

    def test_it_sends_and_records_delivery(self):
        output = self._run("--year", "2026", "--month", "8")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Active Customer Account Report", mail.outbox[0].subject)
        self.assertEqual(self._sent_count(), 1)
        self.assertIn("Sent 2026-08", output)

    def test_the_pdf_is_attached(self):
        self._run("--year", "2026", "--month", "8")

        name, _content, mimetype = mail.outbox[0].attachments[0]
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(mimetype, "application/pdf")

    def test_a_second_run_for_the_same_period_sends_nothing(self):
        """A duplicate schedule must not mail a partner the same statement twice."""
        self._run("--year", "2026", "--month", "8")
        mail.outbox = []

        output = self._run("--year", "2026", "--month", "8")

        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("Already sent", output)
        self.assertEqual(self._sent_count(), 1)

    def test_force_resends(self):
        self._run("--year", "2026", "--month", "8")
        mail.outbox = []

        self._run("--year", "2026", "--month", "8", "--force")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self._sent_count(), 1)

    def test_a_different_period_is_not_blocked(self):
        self._run("--year", "2026", "--month", "8")
        mail.outbox = []

        self._run("--year", "2026", "--month", "7")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self._sent_count(), 2)

    def test_dry_run_sends_nothing_and_records_nothing(self):
        output = self._run("--year", "2026", "--month", "8", "--dry-run")

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self._sent_count(), 0)
        self.assertIn("dry-run", output)

    def test_it_defaults_to_the_previous_month(self):
        year, month = previous_month()

        self._run()

        self.assertTrue(
            SentPartnerReport.objects.filter(
                kind=SentPartnerReport.KIND_ACTIVE_ACCOUNTS, year=year, month=month
            ).exists()
        )

    def test_half_a_period_is_rejected(self):
        with self.assertRaises(CommandError):
            self._run("--year", "2026")

    def test_an_impossible_month_is_rejected(self):
        with self.assertRaises(CommandError):
            self._run("--year", "2026", "--month", "13")


@override_settings(PARTNER_REPORT_RECIPIENTS=[])
class NoRecipientsTestCase(TestCase):
    def test_nothing_is_recorded_when_there_is_nobody_to_send_to(self):
        """Otherwise the period is marked delivered and never retried."""
        out = StringIO()
        call_command("send_active_account_report", "--year", "2026", "--month", "8", stdout=out, stderr=out)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(SentPartnerReport.objects.count(), 0)
        self.assertIn("No recipients", out.getvalue())


class CronEntryPointTestCase(TestCase):
    def test_the_cron_function_invokes_the_command(self):
        """django-crontab calls a dotted path, not a management command."""
        from tenants.cron import send_monthly_active_account_report

        with mock.patch("tenants.cron.call_command") as called:
            send_monthly_active_account_report()

        called.assert_called_once_with("send_active_account_report")

    def test_a_failure_is_raised_not_swallowed(self):
        """A contractual report failing to send must be loud."""
        from tenants.cron import send_monthly_active_account_report

        with mock.patch("tenants.cron.call_command", side_effect=RuntimeError("smtp down")):
            with self.assertRaises(RuntimeError):
                send_monthly_active_account_report()

    def test_the_job_is_actually_scheduled(self):
        """The whole bug was a task nothing could invoke."""
        from django.conf import settings

        paths = [entry[1] for entry in settings.CRONJOBS]
        self.assertIn("tenants.cron.send_monthly_active_account_report", paths)
