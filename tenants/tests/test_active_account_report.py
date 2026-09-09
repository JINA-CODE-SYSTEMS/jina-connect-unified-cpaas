"""
Tests for the Active Customer Account report (Fabtary agreement Cl. 4.2).

Run with: python manage.py test tenants.tests.test_active_account_report

The pro-rata arithmetic is what an invoice is derived from, so the boundaries
are tested explicitly rather than trusted.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from tenants.models import Tenant
from tenants.services import build_active_account_report

UTC = ZoneInfo("UTC")


def _local(year, month, day, hour=0, minute=0):
    """An instant in the deployment's timezone.

    Day boundaries in this report are local, so tests state their times the way
    an invoice would be read rather than in UTC.
    """
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def _make_tenant(name, created, archived=None):
    """Create a tenant with an explicit onboarding date.

    created_at is auto_now_add, so it has to be overwritten after insert.
    """
    tenant = Tenant.objects.create(name=name)
    Tenant.objects.filter(pk=tenant.pk).update(created_at=created, archived_at=archived)
    tenant.refresh_from_db()
    return tenant


class ActiveAccountReportTestCase(TestCase):
    """Accounts count pro rata by days existed, inclusive at both ends."""

    def test_account_live_all_month_counts_as_one(self):
        _make_tenant("Acme", _local(2026, 8, 15))

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.account_count, 1)
        self.assertEqual(report.full_month_count, 1)
        self.assertEqual(report.partial_month_count, 0)
        self.assertEqual(report.lines[0].active_days, 30)
        self.assertEqual(report.lines[0].prorata, Decimal("1.0000"))
        self.assertEqual(report.billable_accounts, Decimal("1.0000"))

    def test_account_onboarded_mid_month_is_prorated_from_that_day(self):
        # Onboarded 11 Sep: 11th through 30th inclusive is 20 of 30 days.
        _make_tenant("Mid", _local(2026, 9, 11, 9, 30))

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.lines[0].active_days, 20)
        self.assertEqual(report.lines[0].prorata, Decimal("0.6667"))
        self.assertFalse(report.lines[0].is_full_month)

    def test_archival_day_is_counted(self):
        # Live from before the month, archived on the 10th: 1st-10th is 10 days.
        _make_tenant("Leaver", _local(2026, 1, 1), archived=_local(2026, 9, 10, 23, 0))

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.lines[0].active_days, 10)
        self.assertEqual(report.lines[0].status, "Archived")

    def test_onboarded_and_archived_within_the_same_month(self):
        _make_tenant(
            "Brief",
            _local(2026, 9, 10),
            archived=_local(2026, 9, 14),
        )

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.lines[0].active_days, 5)

    def test_onboarded_and_archived_on_the_same_day_counts_one_day(self):
        _make_tenant(
            "SameDay",
            _local(2026, 9, 7, 8, 0),
            archived=_local(2026, 9, 7, 17, 0),
        )

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.lines[0].active_days, 1)

    def test_account_archived_before_the_month_is_excluded(self):
        _make_tenant("Gone", _local(2026, 1, 1), archived=_local(2026, 8, 31))

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.account_count, 0)
        self.assertEqual(report.billable_accounts, Decimal("0.0000"))

    def test_account_onboarded_after_the_month_is_excluded(self):
        _make_tenant("Future", _local(2026, 10, 1))

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.account_count, 0)

    def test_message_activity_is_irrelevant(self):
        # Cl. 4.2 counts accounts onboarded, not accounts that sent anything.
        _make_tenant("Silent", _local(2026, 9, 1))

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.account_count, 1)
        self.assertEqual(report.lines[0].prorata, Decimal("1.0000"))

    def test_month_lengths_are_respected(self):
        _make_tenant("Acme", _local(2020, 1, 1))

        self.assertEqual(build_active_account_report(2026, 2).days_in_month, 28)
        self.assertEqual(build_active_account_report(2024, 2).days_in_month, 29)
        self.assertEqual(build_active_account_report(2026, 1).days_in_month, 31)

    def test_report_is_itemised_and_totals_add_up(self):
        _make_tenant("Full", _local(2026, 1, 1))
        _make_tenant("Half", _local(2026, 9, 16))
        _make_tenant("Leaver", _local(2026, 1, 1), archived=_local(2026, 9, 15))

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.account_count, 3)
        self.assertEqual(report.full_month_count, 1)
        self.assertEqual(report.partial_month_count, 2)
        self.assertEqual({line.name for line in report.lines}, {"Full", "Half", "Leaver"})
        # 30/30 + 15/30 + 15/30 = 2.0
        self.assertEqual(report.billable_accounts, Decimal("2.0000"))

    def test_day_boundaries_follow_the_deployment_timezone(self):
        """Which day an account is billed for is decided in local time.

        2026-09-10 23:00 UTC is 04:30 on the 11th in Asia/Kolkata, so the
        account counts through the 11th. The invoice is read against the local
        calendar, so that is the boundary that matters.
        """
        _make_tenant("TZ", _local(2026, 1, 1), archived=datetime(2026, 9, 10, 23, 0, tzinfo=UTC))

        report = build_active_account_report(2026, 9)

        self.assertEqual(report.lines[0].archived_on.day, 11)
        self.assertEqual(report.lines[0].active_days, 11)

    def test_label_names_the_period(self):
        self.assertEqual(build_active_account_report(2026, 9).label, "September 2026")


class TenantArchiveTestCase(TestCase):
    """archive() stamps a timestamp instead of deleting the billing record."""

    def test_archive_marks_and_preserves_the_row(self):
        tenant = Tenant.objects.create(name="Acme")
        self.assertFalse(tenant.is_archived)

        tenant.archive()

        self.assertTrue(tenant.is_archived)
        self.assertIsNotNone(Tenant.objects.filter(pk=tenant.pk).first())

    def test_archiving_twice_keeps_the_original_timestamp(self):
        tenant = Tenant.objects.create(name="Acme")
        first = tenant.archive()

        second = tenant.archive(when=timezone.now())

        self.assertEqual(first, second)
