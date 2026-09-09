"""Monthly availability report (Fabtary agreement Cl. 5.4).

Availability is computed against a period with planned maintenance removed from
both sides of the fraction:

    availability = (period - maintenance - unplanned_downtime)
                   -------------------------------------------
                            (period - maintenance)

Two decisions are deliberate and are stated on the report rather than buried:

**Combined downtime is summed across probe targets, not maxed.** The platform
is unusable if either the API or the UI is down, so the true figure is the
union of their outages, which daily aggregates cannot reconstruct. Summing
never overstates availability; taking the maximum would. For a number the
company is held to, the bias belongs against ourselves.

**Missing days are reported, never assumed healthy.** If the monitor or the
nightly aggregation failed, those days have no rows. Treating absent data as
100% uptime would quietly inflate the figure, so coverage is tracked and the
report says plainly when it is incomplete.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

from availability.models import DailyAvailability, MaintenanceWindow, ProbeTarget

PERCENT_PRECISION = Decimal("0.001")


def _percent(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0.000")
    value = Decimal(numerator) * Decimal(100) / Decimal(denominator)
    return value.quantize(PERCENT_PRECISION, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TargetAvailability:
    target: str
    label: str
    total_checks: int
    failed_checks: int
    downtime_seconds: int
    billable_seconds: int

    @property
    def availability(self) -> Decimal:
        return _percent(self.billable_seconds - self.downtime_seconds, self.billable_seconds)


@dataclass(frozen=True)
class AvailabilityReport:
    period_start: date
    period_end: date
    period_seconds: int
    maintenance_seconds: int
    targets: list[TargetAvailability]
    days_expected: int
    days_covered: int
    commitment: Decimal

    @property
    def billable_seconds(self) -> int:
        """Period with planned maintenance removed."""
        return max(0, self.period_seconds - self.maintenance_seconds)

    @property
    def downtime_seconds(self) -> int:
        """Union of target outages, approximated by summing and capping."""
        return min(sum(t.downtime_seconds for t in self.targets), self.billable_seconds)

    @property
    def availability(self) -> Decimal:
        return _percent(self.billable_seconds - self.downtime_seconds, self.billable_seconds)

    @property
    def meets_commitment(self) -> bool:
        return self.availability >= self.commitment

    @property
    def is_complete(self) -> bool:
        """Whether every day of the period has data for every target."""
        return self.days_covered >= self.days_expected

    @property
    def missing_days(self) -> int:
        return max(0, self.days_expected - self.days_covered)

    @property
    def label(self) -> str:
        return self.period_start.strftime("%B %Y")


def _period_bounds(year: int, month: int) -> tuple[datetime, datetime, int]:
    days_in_month = monthrange(year, month)[1]
    tz = timezone.get_current_timezone()
    start = datetime.combine(date(year, month, 1), time.min, tzinfo=tz)
    end = datetime.combine(date(year, month, days_in_month), time.max, tzinfo=tz)
    return start, end, days_in_month


def build_availability_report(year: int, month: int) -> AvailabilityReport:
    """Build the monthly availability report for a calendar month."""
    start, end, days_in_month = _period_bounds(year, month)
    period_seconds = int((end - start).total_seconds()) + 1

    maintenance_seconds = sum(
        window.overlap_seconds(start, end)
        for window in MaintenanceWindow.objects.filter(starts_at__lt=end, ends_at__gt=start)
    )
    billable_seconds = max(0, period_seconds - maintenance_seconds)

    rows = list(DailyAvailability.objects.filter(date__gte=start.date(), date__lte=end.date()))

    targets: list[TargetAvailability] = []
    for value, label in ProbeTarget.choices:
        for_target = [row for row in rows if row.target == value]
        targets.append(
            TargetAvailability(
                target=value,
                label=label,
                total_checks=sum(row.total_checks for row in for_target),
                failed_checks=sum(row.failed_checks for row in for_target),
                downtime_seconds=sum(row.downtime_seconds for row in for_target),
                billable_seconds=billable_seconds,
            )
        )

    # A day counts as covered only when every target reported for it.
    by_day: dict[date, set[str]] = {}
    for row in rows:
        by_day.setdefault(row.date, set()).add(row.target)
    expected_targets = {value for value, _ in ProbeTarget.choices}
    days_covered = sum(1 for reported in by_day.values() if reported >= expected_targets)

    return AvailabilityReport(
        period_start=start.date(),
        period_end=end.date(),
        period_seconds=period_seconds,
        maintenance_seconds=maintenance_seconds,
        targets=targets,
        days_expected=days_in_month,
        days_covered=days_covered,
        commitment=Decimal(str(settings.AVAILABILITY_COMMITMENT_PERCENT)),
    )
