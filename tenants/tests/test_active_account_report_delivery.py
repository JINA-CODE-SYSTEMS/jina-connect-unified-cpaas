"""
Tests for Cl. 4.2 report delivery: PDF rendering, email, and the archive command.

Run with: python manage.py test tenants.tests.test_active_account_report_delivery
"""

from datetime import date, datetime
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from tenants.models import Tenant
from tenants.services.active_account_report import build_active_account_report
from tenants.services.active_account_report_pdf import render_active_account_report_pdf
from tenants.tasks import build_and_send_active_account_report, previous_month


def _local(year, month, day, hour=0, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def _make_tenant(name, created, archived=None):
    tenant = Tenant.objects.create(name=name)
    Tenant.objects.filter(pk=tenant.pk).update(created_at=created, archived_at=archived)
    tenant.refresh_from_db()
    return tenant


class PreviousMonthTestCase(TestCase):
    def test_rolls_back_across_year_boundaries(self):
        self.assertEqual(previous_month(date(2026, 9, 9)), (2026, 8))
        self.assertEqual(previous_month(date(2026, 1, 15)), (2025, 12))
        self.assertEqual(previous_month(date(2026, 3, 1)), (2026, 2))


class ReportPdfTestCase(TestCase):
    def test_renders_a_pdf(self):
        _make_tenant("Acme", _local(2026, 1, 1))

        pdf = render_active_account_report_pdf(build_active_account_report(2026, 9))

        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)

    def test_renders_with_no_accounts(self):
        """An empty month must still produce a document, not an exception."""
        pdf = render_active_account_report_pdf(build_active_account_report(2026, 9))

        self.assertTrue(pdf.startswith(b"%PDF-"))


@override_settings(PARTNER_NAME="Example Partner", PARTNER_REPORT_RECIPIENTS=["ops@example.invalid"])
class ReportEmailTestCase(TestCase):
    def test_sends_the_report_with_a_pdf_attachment(self):
        _make_tenant("Acme", _local(2026, 1, 1))

        sent = build_and_send_active_account_report(2026, 9)

        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)

        message = mail.outbox[0]
        self.assertEqual(message.to, ["ops@example.invalid"])
        self.assertIn("September 2026", message.subject)

        name, content, mimetype = message.attachments[0]
        self.assertEqual(name, "active-customer-accounts-2026-09.pdf")
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF-"))

    def test_body_states_the_headline_figures(self):
        _make_tenant("Full", _local(2026, 1, 1))
        _make_tenant("Half", _local(2026, 9, 16))

        build_and_send_active_account_report(2026, 9)

        body = mail.outbox[0].body
        self.assertIn("Accounts in period: 2", body)
        self.assertIn("1.5000", body)

    @override_settings(PARTNER_REPORT_RECIPIENTS=[])
    def test_sends_nothing_when_no_recipients_are_configured(self):
        """An unconfigured deployment must not silently email a stranger."""
        _make_tenant("Acme", _local(2026, 1, 1))

        sent = build_and_send_active_account_report(2026, 9)

        self.assertEqual(sent, 0)
        self.assertEqual(len(mail.outbox), 0)


class ArchiveTenantCommandTestCase(TestCase):
    def test_archives_and_keeps_the_row(self):
        tenant = Tenant.objects.create(name="Acme")
        out = StringIO()

        call_command("archive_tenant", tenant.pk, stdout=out)

        tenant.refresh_from_db()
        self.assertTrue(tenant.is_archived)
        self.assertIn("Archived tenant", out.getvalue())
        self.assertTrue(Tenant.objects.filter(pk=tenant.pk).exists())

    def test_dry_run_changes_nothing(self):
        tenant = Tenant.objects.create(name="Acme")
        out = StringIO()

        call_command("archive_tenant", tenant.pk, "--dry-run", stdout=out)

        tenant.refresh_from_db()
        self.assertFalse(tenant.is_archived)
        self.assertIn("Would archive", out.getvalue())

    def test_explicit_date_is_used(self):
        tenant = Tenant.objects.create(name="Acme")

        call_command("archive_tenant", tenant.pk, "--when", "2026-09-10", stdout=StringIO())

        tenant.refresh_from_db()
        self.assertEqual(timezone.localtime(tenant.archived_at).date(), date(2026, 9, 10))

    def test_already_archived_is_reported_not_overwritten(self):
        tenant = Tenant.objects.create(name="Acme")
        first = tenant.archive()
        out = StringIO()

        call_command("archive_tenant", tenant.pk, stdout=out)

        tenant.refresh_from_db()
        self.assertEqual(tenant.archived_at, first)
        self.assertIn("already archived", out.getvalue())

    def test_unknown_tenant_raises(self):
        with self.assertRaises(CommandError):
            call_command("archive_tenant", 999999, stdout=StringIO())

    def test_bad_date_raises(self):
        tenant = Tenant.objects.create(name="Acme")

        with self.assertRaises(CommandError):
            call_command("archive_tenant", tenant.pk, "--when", "10-09-2026", stdout=StringIO())

    def test_archived_account_is_prorated_in_the_report(self):
        """End to end: archiving is what makes a partial month appear."""
        tenant = _make_tenant("Leaver", _local(2026, 1, 1))

        call_command("archive_tenant", tenant.pk, "--when", "2026-09-15", stdout=StringIO())

        report = build_active_account_report(2026, 9)
        self.assertEqual(report.lines[0].active_days, 15)
        self.assertFalse(report.lines[0].is_full_month)
