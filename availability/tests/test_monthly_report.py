"""
Tests for the monthly availability report (Cl. 5.4).

Run with: python manage.py test availability.tests.test_monthly_report

The arithmetic decides whether an SLA credit is owed, so the boundaries and the
data-integrity cases are tested explicitly.
"""

from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from availability.models import DailyAvailability, MaintenanceWindow, ProbeTarget
from availability.services.monthly_report import build_availability_report

# 2-minute probes: 720 per target per day, one failure = 120 seconds.
CHECKS_PER_DAY = 720
INTERVAL = 120


def _local(year, month, day, hour=0, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def _fill_month(year, month, days, failed_by_day=None, targets=None):
    """Write a clean month of data, with optional failures on given days."""
    failed_by_day = failed_by_day or {}
    targets = targets or [ProbeTarget.API, ProbeTarget.UI]

    for day in range(1, days + 1):
        for target in targets:
            DailyAvailability.objects.create(
                date=date(year, month, day),
                target=target,
                total_checks=CHECKS_PER_DAY,
                failed_checks=failed_by_day.get((day, target), 0),
                probe_interval_seconds=INTERVAL,
            )


@override_settings(AVAILABILITY_COMMITMENT_PERCENT="99.5")
class AvailabilityReportTestCase(TestCase):
    def test_a_perfect_month_is_100_percent(self):
        _fill_month(2026, 9, 30)

        report = build_availability_report(2026, 9)

        self.assertEqual(report.downtime_seconds, 0)
        self.assertEqual(report.availability, Decimal("100.000"))
        self.assertTrue(report.meets_commitment)
        self.assertTrue(report.is_complete)

    def test_failed_probes_become_downtime(self):
        # 10 failed API probes = 20 minutes.
        _fill_month(2026, 9, 30, failed_by_day={(5, ProbeTarget.API): 10})

        report = build_availability_report(2026, 9)

        self.assertEqual(report.downtime_seconds, 10 * INTERVAL)
        api = next(t for t in report.targets if t.target == ProbeTarget.API)
        self.assertEqual(api.failed_checks, 10)

    def test_downtime_is_summed_across_targets_not_maxed(self):
        """Summing never overstates availability; maxing would."""
        _fill_month(
            2026,
            9,
            30,
            failed_by_day={(5, ProbeTarget.API): 10, (12, ProbeTarget.UI): 10},
        )

        report = build_availability_report(2026, 9)

        self.assertEqual(report.downtime_seconds, 20 * INTERVAL)

    def test_maintenance_is_excluded_from_both_sides(self):
        _fill_month(2026, 9, 30)
        MaintenanceWindow.objects.create(
            starts_at=_local(2026, 9, 14, 2, 0),
            ends_at=_local(2026, 9, 14, 4, 0),
            reason="Postgres major upgrade",
        )

        report = build_availability_report(2026, 9)

        self.assertEqual(report.maintenance_seconds, 2 * 3600)
        self.assertEqual(report.billable_seconds, report.period_seconds - 2 * 3600)
        # No unplanned downtime, so excluding maintenance still leaves 100%.
        self.assertEqual(report.availability, Decimal("100.000"))

    def test_maintenance_outside_the_period_is_ignored(self):
        _fill_month(2026, 9, 30)
        MaintenanceWindow.objects.create(
            starts_at=_local(2026, 8, 10, 2, 0),
            ends_at=_local(2026, 8, 10, 4, 0),
            reason="Previous month",
        )

        report = build_availability_report(2026, 9)

        self.assertEqual(report.maintenance_seconds, 0)

    def test_maintenance_straddling_the_month_boundary_counts_only_the_overlap(self):
        _fill_month(2026, 9, 30)
        MaintenanceWindow.objects.create(
            starts_at=_local(2026, 8, 31, 23, 0),
            ends_at=_local(2026, 9, 1, 1, 0),
            reason="Straddles midnight",
        )

        report = build_availability_report(2026, 9)

        self.assertEqual(report.maintenance_seconds, 3600)

    def test_breaching_the_commitment_is_reported(self):
        # 99.5% of a 30-day month allows 216 minutes. 200 failures = 400 minutes.
        _fill_month(2026, 9, 30, failed_by_day={(5, ProbeTarget.API): 200})

        report = build_availability_report(2026, 9)

        self.assertFalse(report.meets_commitment)
        self.assertLess(report.availability, Decimal("99.5"))

    def test_just_inside_the_commitment_passes(self):
        # 100 failures = 200 minutes, inside the 216-minute allowance.
        _fill_month(2026, 9, 30, failed_by_day={(5, ProbeTarget.API): 100})

        report = build_availability_report(2026, 9)

        self.assertTrue(report.meets_commitment)

    def test_missing_days_are_flagged_not_assumed_healthy(self):
        """Absent data must not quietly read as 100% uptime."""
        _fill_month(2026, 9, 20)

        report = build_availability_report(2026, 9)

        self.assertFalse(report.is_complete)
        self.assertEqual(report.days_expected, 30)
        self.assertEqual(report.days_covered, 20)
        self.assertEqual(report.missing_days, 10)

    def test_a_day_missing_one_target_is_not_covered(self):
        _fill_month(2026, 9, 30)
        DailyAvailability.objects.filter(date=date(2026, 9, 9), target=ProbeTarget.UI).delete()

        report = build_availability_report(2026, 9)

        self.assertEqual(report.days_covered, 29)
        self.assertFalse(report.is_complete)

    def test_a_month_with_no_data_reports_zero_coverage(self):
        report = build_availability_report(2026, 9)

        self.assertEqual(report.days_covered, 0)
        self.assertEqual(report.downtime_seconds, 0)
        self.assertFalse(report.is_complete)

    def test_month_lengths_are_respected(self):
        self.assertEqual(build_availability_report(2026, 2).days_expected, 28)
        self.assertEqual(build_availability_report(2024, 2).days_expected, 29)
        self.assertEqual(build_availability_report(2026, 1).days_expected, 31)

    def test_label_names_the_period(self):
        self.assertEqual(build_availability_report(2026, 9).label, "September 2026")


class DailyAvailabilityModelTestCase(TestCase):
    def test_downtime_is_failures_times_interval(self):
        row = DailyAvailability(
            date=date(2026, 9, 1), target=ProbeTarget.API, total_checks=720, failed_checks=3, probe_interval_seconds=120
        )

        self.assertEqual(row.downtime_seconds, 360)
        self.assertEqual(row.successful_checks, 717)


class MaintenanceWindowTestCase(TestCase):
    def test_overlap_is_clamped_to_the_requested_period(self):
        window = MaintenanceWindow.objects.create(
            starts_at=_local(2026, 9, 10, 1, 0),
            ends_at=_local(2026, 9, 10, 5, 0),
            reason="Long window",
        )

        overlap = window.overlap_seconds(_local(2026, 9, 10, 2, 0), _local(2026, 9, 10, 3, 0))

        self.assertEqual(overlap, 3600)

    def test_non_overlapping_window_contributes_nothing(self):
        window = MaintenanceWindow.objects.create(
            starts_at=_local(2026, 9, 10, 1, 0),
            ends_at=_local(2026, 9, 10, 2, 0),
            reason="Early",
        )

        self.assertEqual(window.overlap_seconds(_local(2026, 9, 11, 0, 0), _local(2026, 9, 12, 0, 0)), 0)
