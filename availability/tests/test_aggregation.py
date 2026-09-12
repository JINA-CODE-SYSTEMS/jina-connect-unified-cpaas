"""
Tests for the nightly aggregation that feeds the Cl. 5.4 report.

Run with: python manage.py test availability.tests.test_aggregation

This job is the only writer of the numbers a contractual availability figure
is computed from, and it runs unattended at 01:30. The cases that matter are
the ones where it would otherwise invent data: a monitoring gap, a partial
day, a selector matching the wrong check, a day that has not finished yet.
"""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from availability.models import DailyAvailability, ProbeTarget
from availability.services.grafana import (
    GrafanaNotConfigured,
    GrafanaUnavailable,
    _query_url,
    fetch_probe_results,
    query_range,
)
from availability.services.monthly_report import build_availability_report

COMMAND = "aggregate_availability"

CONFIGURED = dict(
    GRAFANA_PROM_URL="https://prometheus-prod-01-eu-west-0.grafana.net/api/prom",
    GRAFANA_PROM_USER="123456",
    GRAFANA_PROM_TOKEN="glc_token",
    AVAILABILITY_PROBE_SELECTORS={"api": 'job="jc-api"', "ui": 'job="jc-ui"'},
    AVAILABILITY_PROBE_INTERVAL_SECONDS=120,
    AVAILABILITY_FAILURE_THRESHOLD=0.5,
    TIME_ZONE="Asia/Kolkata",
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _matrix(values):
    return {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {}, "values": values}]}}


def _yesterday():
    return timezone.localdate() - timedelta(days=1)


@override_settings(**CONFIGURED)
class GrafanaClientTestCase(TestCase):
    def test_query_url_appends_the_api_version_to_grafanas_base(self):
        self.assertEqual(
            _query_url(),
            "https://prometheus-prod-01-eu-west-0.grafana.net/api/prom/api/v1/query_range",
        )

    def test_a_base_that_already_names_the_api_version_is_not_doubled(self):
        with override_settings(GRAFANA_PROM_URL="https://example.grafana.net/api/prom/api/v1/"):
            self.assertEqual(_query_url(), "https://example.grafana.net/api/prom/api/v1/query_range")

    def test_missing_url_is_a_configuration_error_not_a_crash(self):
        with override_settings(GRAFANA_PROM_URL=""), self.assertRaises(GrafanaNotConfigured):
            _query_url()

    def test_missing_token_is_a_configuration_error(self):
        with override_settings(GRAFANA_PROM_TOKEN=""), self.assertRaises(GrafanaNotConfigured):
            fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)

    def test_missing_selector_names_the_env_var_to_set(self):
        with override_settings(AVAILABILITY_PROBE_SELECTORS={"api": "", "ui": ""}):
            with self.assertRaises(GrafanaNotConfigured) as caught:
                fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)
        self.assertIn("AVAILABILITY_PROBE_SELECTOR_API", str(caught.exception))

    def test_rejected_credentials_are_reported_as_configuration_not_an_outage(self):
        """A 401 means the token expired; retrying tonight will not help."""
        with patch("availability.services.grafana.requests.get", return_value=FakeResponse(status_code=401)):
            with self.assertRaises(GrafanaNotConfigured):
                fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)

    def test_a_server_error_is_reported_as_unavailable(self):
        with patch(
            "availability.services.grafana.requests.get",
            return_value=FakeResponse(status_code=503, text="upstream down"),
        ):
            with self.assertRaises(GrafanaUnavailable):
                fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)

    def test_a_network_failure_is_reported_as_unavailable(self):
        with patch(
            "availability.services.grafana.requests.get",
            side_effect=requests.ConnectionError("no route to host"),
        ):
            with self.assertRaises(GrafanaUnavailable):
                fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)

    def test_a_selector_matching_several_checks_is_refused(self):
        """Reading the first of several series would silently measure the wrong thing."""
        payload = {
            "status": "success",
            "data": {"result": [{"metric": {"job": "a"}, "values": []}, {"metric": {"job": "b"}, "values": []}]},
        }
        with patch("availability.services.grafana.requests.get", return_value=FakeResponse(payload)):
            with self.assertRaises(GrafanaUnavailable) as caught:
                fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)
        self.assertIn("more than", str(caught.exception))

    def test_an_empty_result_is_zero_checks_not_a_perfect_day(self):
        payload = {"status": "success", "data": {"result": []}}
        with patch("availability.services.grafana.requests.get", return_value=FakeResponse(payload)):
            total, failed = fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)
        self.assertEqual((total, failed), (0, 0))

    def test_counts_come_back_as_intervals_and_failures(self):
        values = [[1, "1"], [2, "1"], [3, "0"], [4, "1"]]
        with patch("availability.services.grafana.requests.get", return_value=FakeResponse(_matrix(values))):
            total, failed = fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)
        self.assertEqual((total, failed), (4, 1))

    def test_a_minority_of_probe_locations_failing_is_not_downtime(self):
        """Two of three locations up reads 0.667, above the 0.5 threshold."""
        values = [[1, "0.6666666"], [2, "1"]]
        with patch("availability.services.grafana.requests.get", return_value=FakeResponse(_matrix(values))):
            total, failed = fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)
        self.assertEqual((total, failed), (2, 0))

    def test_a_majority_of_probe_locations_failing_is_downtime(self):
        values = [[1, "0.3333333"], [2, "0"]]
        with patch("availability.services.grafana.requests.get", return_value=FakeResponse(_matrix(values))):
            total, failed = fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)
        self.assertEqual((total, failed), (2, 2))

    def test_an_even_split_counts_as_failed(self):
        """Half the locations down is not evidence the platform is up."""
        values = [[1, "0.5"]]
        with override_settings(AVAILABILITY_FAILURE_THRESHOLD=0.51):
            with patch("availability.services.grafana.requests.get", return_value=FakeResponse(_matrix(values))):
                total, failed = fetch_probe_results(ProbeTarget.API, *self._bounds(), 120)
        self.assertEqual((total, failed), (1, 1))

    def test_the_first_window_starts_one_interval_into_the_day(self):
        """Windows tile the day: the point at 00:02 covers 00:00 to 00:02."""
        captured = {}

        def capture(url, params=None, **kwargs):
            captured.update(params)
            return FakeResponse(_matrix([]))

        start, end = self._bounds()
        with patch("availability.services.grafana.requests.get", side_effect=capture):
            fetch_probe_results(ProbeTarget.API, start, end, 120)

        self.assertEqual(captured["start"], start.timestamp() + 120)
        self.assertEqual(captured["end"], end.timestamp())
        self.assertEqual(captured["step"], 120)
        self.assertIn('avg_over_time(probe_success{job="jc-api"}[120s])', captured["query"])
        # Grouped, not collapsed: a selector matching two checks must come
        # back as two series so it can be refused rather than averaged.
        self.assertIn("avg by (job, instance)", captured["query"])

    def test_an_interval_that_would_overrun_prometheus_point_limit_is_refused(self):
        start, end = self._bounds()
        with self.assertRaises(ValueError):
            query_range("up", start, end, 1)

    def _bounds(self):
        from availability.management.commands.aggregate_availability import day_bounds

        return day_bounds(date(2026, 9, 1))


@override_settings(**CONFIGURED)
class AggregateCommandTestCase(TestCase):
    def setUp(self):
        self.out, self.err = StringIO(), StringIO()

    def _run(self, **kwargs):
        """Run the command, returning (stdout, stderr).

        The streams are kept on the instance as well, so a test asserting on
        what the command said can still read it after a CommandError.
        """
        call_command(COMMAND, stdout=self.out, stderr=self.err, **kwargs)
        return self.out.getvalue(), self.err.getvalue()

    def test_it_writes_one_row_per_target_for_yesterday(self):
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(720, 3),
        ):
            self._run()

        rows = DailyAvailability.objects.all()
        self.assertEqual(rows.count(), 2)
        self.assertEqual({row.target for row in rows}, {ProbeTarget.API, ProbeTarget.UI})
        for row in rows:
            self.assertEqual(row.date, _yesterday())
            self.assertEqual(row.total_checks, 720)
            self.assertEqual(row.failed_checks, 3)
            self.assertEqual(row.probe_interval_seconds, 120)
            self.assertEqual(row.source, "grafana")

    def test_re_running_overwrites_rather_than_colliding(self):
        """The unique constraint is (date, target), so a backfill must upsert."""
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(720, 3),
        ):
            self._run()
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(720, 11),
        ):
            self._run()

        self.assertEqual(DailyAvailability.objects.count(), 2)
        self.assertEqual({row.failed_checks for row in DailyAvailability.objects.all()}, {11})

    def test_a_day_with_no_data_gets_no_row_and_the_run_fails(self):
        """A row of zero checks would read as a covered day carrying no evidence.

        And a run that recorded nothing must not exit 0: cron cannot tell a
        quiet night from a monitor that stopped reporting, and the monthly
        report would not notice until the 1st.
        """
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(0, 0),
        ):
            with self.assertRaises(CommandError) as caught:
                self._run()

        self.assertEqual(DailyAvailability.objects.count(), 0)
        self.assertIn("no check results", self.err.getvalue())
        self.assertIn("nothing was recorded", str(caught.exception))

    def test_one_target_failing_does_not_stop_the_other(self):
        def per_target(target, *args):
            if target == ProbeTarget.API:
                raise GrafanaUnavailable("timed out")
            return (720, 0)

        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            side_effect=per_target,
        ):
            _, err = self._run()

        self.assertEqual(DailyAvailability.objects.count(), 1)
        self.assertEqual(DailyAvailability.objects.get().target, ProbeTarget.UI)
        self.assertIn("timed out", err)

    def test_a_configuration_error_stops_immediately(self):
        """Nothing about the next target will differ, so do not repeat the error."""
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            side_effect=GrafanaNotConfigured("GRAFANA_PROM_TOKEN is not set"),
        ):
            with self.assertRaises(CommandError):
                self._run()

    def test_backfill_writes_every_day_in_the_range(self):
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(720, 0),
        ):
            self._run(days=5)

        self.assertEqual(DailyAvailability.objects.count(), 10)
        dates = {row.date for row in DailyAvailability.objects.all()}
        self.assertEqual(dates, {_yesterday() - timedelta(days=n) for n in range(5)})

    def test_an_explicit_date_is_honoured(self):
        day = _yesterday() - timedelta(days=30)
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(720, 0),
        ):
            self._run(date=day.isoformat())

        self.assertEqual({row.date for row in DailyAvailability.objects.all()}, {day})

    def test_today_is_refused_because_it_is_not_over(self):
        """A partial day would be recorded as a short day and flag the month incomplete."""
        with self.assertRaises(CommandError) as caught:
            self._run(date=timezone.localdate().isoformat())
        self.assertIn("not over yet", str(caught.exception))

    def test_a_malformed_date_is_refused(self):
        with self.assertRaises(CommandError):
            self._run(date="01-09-2026")

    def test_dry_run_writes_nothing(self):
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(720, 4),
        ):
            out, _ = self._run(dry_run=True)

        self.assertEqual(DailyAvailability.objects.count(), 0)
        self.assertIn("dry-run", out)

    def test_a_backfill_skips_the_gap_and_still_writes_around_it(self):
        """The day Grafana lost must not take its neighbours down with it.

        This is the shape of a real backfill: monitoring stopped for one day,
        somebody notices a week later and re-runs the range.
        """
        gap = _yesterday() - timedelta(days=1)

        def per_day(target, start, end, interval):
            return (0, 0) if start.date() == gap else (720, 0)

        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            side_effect=per_day,
        ):
            _, err = self._run(days=3)

        self.assertEqual(DailyAvailability.objects.count(), 4)
        self.assertEqual(
            {row.date for row in DailyAvailability.objects.all()},
            {_yesterday() - timedelta(days=2), _yesterday()},
        )
        self.assertIn(str(gap), err)

    def test_re_running_a_backfill_fills_the_gap_without_duplicating_the_rest(self):
        """Idempotent across a range, not just for a single day."""
        gap = _yesterday() - timedelta(days=1)

        def per_day(target, start, end, interval):
            return (0, 0) if start.date() == gap else (720, 0)

        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            side_effect=per_day,
        ):
            self._run(days=3)
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(720, 2),
        ):
            self._run(days=3)

        rows = DailyAvailability.objects.all()
        self.assertEqual(rows.count(), 6)
        self.assertEqual(rows.filter(date=gap).count(), 2)
        # Every row carries the second pass's counts: an upsert, not an insert
        # that happened to dodge the unique constraint.
        self.assertEqual({row.failed_checks for row in rows}, {2})

    def test_a_rejected_credential_raises_rather_than_recording_zeros(self):
        """End to end through the HTTP layer: a 401 must not look like uptime.

        A silently-swallowed 401 writes nothing and exits 0, and a month of
        that is indistinguishable from a month of perfect availability.
        """
        with patch("availability.services.grafana.requests.get", return_value=FakeResponse(status_code=401)):
            with self.assertRaises(CommandError) as caught:
                self._run()

        self.assertEqual(DailyAvailability.objects.count(), 0)
        self.assertIn("credentials", str(caught.exception))

    @override_settings(AVAILABILITY_PROBE_INTERVAL_SECONDS=60)
    def test_counts_are_stored_raw_and_downtime_derives_from_the_stored_interval(self):
        """Counts, never a percentage — and the interval comes from the row.

        A percentage discards the evidence and cannot be recomputed if the
        method changes. The interval is stored alongside the counts for the
        same reason: a row aggregated at 60s must not later be read as 120s
        because that is the default.
        """
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(1440, 17),
        ):
            self._run()

        row = DailyAvailability.objects.get(target=ProbeTarget.API)
        self.assertEqual(row.total_checks, 1440)
        self.assertEqual(row.failed_checks, 17)
        self.assertEqual(row.probe_interval_seconds, 60)
        self.assertEqual(row.downtime_seconds, 17 * 60)

    def test_a_partial_run_is_still_a_success(self):
        """One target missing is a warning; the rows that were written are real."""

        def per_target(target, *args):
            return (0, 0) if target == ProbeTarget.API else (720, 0)

        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            side_effect=per_target,
        ):
            out, _ = self._run()

        self.assertEqual(DailyAvailability.objects.count(), 1)
        self.assertIn("1 skipped", out)

    def test_a_short_day_is_written_and_flagged(self):
        """The evidence is kept; the report decides whether it counts as coverage."""
        with patch(
            "availability.management.commands.aggregate_availability.fetch_probe_results",
            return_value=(40, 0),
        ):
            out, _ = self._run()

        self.assertEqual(DailyAvailability.objects.count(), 2)
        self.assertIn("short", out)


@override_settings(AVAILABILITY_COMMITMENT_PERCENT="99.5", AVAILABILITY_MIN_DAY_COVERAGE=0.9)
class PartialDayCoverageTestCase(TestCase):
    """A row holding a handful of checks is a monitoring gap, not a measured day."""

    def _write(self, day, total, failed=0, interval=120):
        for target, _ in ProbeTarget.choices:
            DailyAvailability.objects.create(
                date=day, target=target, total_checks=total, failed_checks=failed, probe_interval_seconds=interval
            )

    def test_a_day_with_a_handful_of_checks_does_not_count_as_covered(self):
        for day in range(1, 31):
            self._write(date(2026, 9, day), 720)
        DailyAvailability.objects.filter(date=date(2026, 9, 9)).update(total_checks=5)

        report = build_availability_report(2026, 9)

        self.assertEqual(report.days_covered, 29)
        self.assertFalse(report.is_complete)

    def test_a_day_just_above_the_floor_still_counts(self):
        for day in range(1, 31):
            self._write(date(2026, 9, day), 720)
        DailyAvailability.objects.filter(date=date(2026, 9, 9)).update(total_checks=700)

        self.assertEqual(build_availability_report(2026, 9).days_covered, 30)

    def test_the_downtime_on_a_thin_day_is_still_counted(self):
        """Excluding the day from coverage must not excuse the outage it recorded."""
        for day in range(1, 31):
            self._write(date(2026, 9, day), 720)
        DailyAvailability.objects.filter(date=date(2026, 9, 9), target=ProbeTarget.API).update(
            total_checks=5, failed_checks=5
        )

        report = build_availability_report(2026, 9)

        self.assertEqual(report.downtime_seconds, 5 * 120)
        self.assertLess(report.availability, Decimal("100.000"))

    def test_a_row_with_a_nonsensical_interval_is_not_treated_as_coverage(self):
        self._write(date(2026, 9, 1), 720, interval=0)

        self.assertEqual(build_availability_report(2026, 9).days_covered, 0)


class AggregationCronEntryPointTestCase(TestCase):
    """The job only exists if cron calls it.

    Merged CRONJOBS entries have shipped without being installed on the box
    more than once, which closes a ticket and changes no behaviour. These
    assertions cover the half that lives in the repository; `crontab add`
    still has to be run on the host.
    """

    def test_the_cron_function_invokes_the_command(self):
        from availability.cron import aggregate_daily_availability

        with patch("availability.cron.call_command") as called:
            aggregate_daily_availability()

        called.assert_called_once_with("aggregate_availability")

    def test_the_nightly_job_is_scheduled(self):
        from django.conf import settings

        paths = [entry[1] for entry in settings.CRONJOBS]
        self.assertIn("availability.cron.aggregate_daily_availability", paths)

    def test_the_nightly_job_is_scheduled_nightly(self):
        """Copied from the monthly entry, it would run twelve times a year."""
        from django.conf import settings

        schedule = next(
            entry[0] for entry in settings.CRONJOBS if entry[1] == "availability.cron.aggregate_daily_availability"
        )
        _minute, _hour, day_of_month, month, _weekday = schedule.split()
        self.assertEqual((day_of_month, month), ("*", "*"))
