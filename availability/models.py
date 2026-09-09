"""Availability records for the monthly SLA report (Fabtary agreement Cl. 5.4).

External synthetic monitoring is the measurement instrument; these tables are
the record. Two reasons the data is copied here rather than queried live:

  * Grafana Cloud's free tier retains metrics for 14 days, and a calendar-month
    report needs 31.
  * A contractual figure that may have to be defended in eighteen months should
    not depend on a third party's retention policy.

One row per probe target per day. Daily granularity is deliberate — it is the
smallest unit the monthly figure needs, and it keeps the table small enough to
retain indefinitely.
"""

from datetime import timedelta

from django.core.validators import MinValueValidator
from django.db import models


class ProbeTarget(models.TextChoices):
    """What was probed. Both must be up for the platform to be usable."""

    API = "api", "API"
    UI = "ui", "Web UI"


class MaintenanceWindow(models.Model):
    """A planned maintenance window, excluded from the availability calculation.

    Recorded ad hoc rather than as a fixed weekly slot, because there is no
    standing window today. Excluding planned maintenance is what makes 99.5%
    achievable while still shipping upgrades.
    """

    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=255, help_text="What the window was for, as reported to the partner.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]
        verbose_name = "Maintenance window"
        verbose_name_plural = "Maintenance windows"

    def __str__(self):
        return f"{self.starts_at:%Y-%m-%d %H:%M} to {self.ends_at:%H:%M} — {self.reason}"

    @property
    def duration(self) -> timedelta:
        return self.ends_at - self.starts_at

    def overlap_seconds(self, window_start, window_end) -> int:
        """Seconds of this window falling inside the given period."""
        start = max(self.starts_at, window_start)
        end = min(self.ends_at, window_end)
        return max(0, int((end - start).total_seconds()))


class DailyAvailability(models.Model):
    """One probe target's results for one day, as reported by the monitor.

    Stores the raw counts rather than a percentage so the figure can be
    recomputed if the method changes — a percentage discards the evidence.
    """

    date = models.DateField(db_index=True)
    target = models.CharField(max_length=8, choices=ProbeTarget.choices)

    total_checks = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    failed_checks = models.PositiveIntegerField(default=0)

    probe_interval_seconds = models.PositiveIntegerField(
        default=120,
        help_text="Interval between probes. A failed check is counted as this much downtime.",
    )

    source = models.CharField(max_length=32, default="grafana", help_text="Which monitor reported this.")
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "target"]
        constraints = [
            models.UniqueConstraint(fields=["date", "target"], name="unique_daily_availability_per_target"),
            models.CheckConstraint(
                condition=models.Q(failed_checks__lte=models.F("total_checks")),
                name="failed_checks_not_greater_than_total",
            ),
        ]
        verbose_name = "Daily availability"
        verbose_name_plural = "Daily availability"

    def __str__(self):
        return f"{self.date} {self.get_target_display()}: {self.successful_checks}/{self.total_checks}"

    @property
    def successful_checks(self) -> int:
        return self.total_checks - self.failed_checks

    @property
    def downtime_seconds(self) -> int:
        """Downtime inferred from failed probes.

        Each failure stands for one probe interval. That is an approximation:
        a probe every two minutes cannot see an outage shorter than two
        minutes, and rounds a longer one to the nearest interval. It is the
        same approximation every synthetic-monitoring SLA makes, and it is
        stated on the report rather than hidden.
        """
        return self.failed_checks * self.probe_interval_seconds
