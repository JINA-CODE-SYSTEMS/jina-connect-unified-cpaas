"""Active Customer Account report — Fabtary agreement Cl. 4.2.

An "active customer account" is any account onboarded on the deployment,
regardless of whether it sends messages. Accounts are counted pro rata by the
days they existed within the calendar month, so an account onboarded or
archived mid-month contributes a fraction rather than a whole.

Day counting is inclusive at both ends: an account onboarded on the 10th and
archived on the 10th existed for one day, not zero. The archival day is billed
because the account was available for part of it.

The report reads Tenant rows directly. Tenant.archive() stamps archived_at
rather than deleting the row, because a hard delete removes the evidence the
invoice is derived from. Tenant does carry simple_history (inherited from
BaseWallet), so a deletion would leave a historical row - but an audit log is
the wrong source for billing: it is subject to pruning, and it cannot
distinguish "archived, customer data purged" from "row deleted". archived_at
records archival as an explicit business state instead.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from tenants.models import Tenant

# Money-adjacent, so Decimal rather than float. Four places is finer than any
# realistic month (1/31 = 0.0323) and keeps the sum stable when it is priced.
PRORATA_PRECISION = Decimal("0.0001")


@dataclass(frozen=True)
class AccountLine:
    """One account's contribution to a month."""

    tenant_id: int
    name: str
    onboarded_on: date
    archived_on: date | None
    active_days: int
    days_in_month: int

    @property
    def is_full_month(self) -> bool:
        return self.active_days == self.days_in_month

    @property
    def prorata(self) -> Decimal:
        """Fraction of the month this account was live, to 4 decimal places."""
        return (Decimal(self.active_days) / Decimal(self.days_in_month)).quantize(
            PRORATA_PRECISION, rounding=ROUND_HALF_UP
        )

    @property
    def status(self) -> str:
        return "Archived" if self.archived_on else "Active"


@dataclass(frozen=True)
class ActiveAccountReport:
    """The month's itemised account report."""

    period_start: date
    period_end: date
    days_in_month: int
    lines: list[AccountLine]

    @property
    def account_count(self) -> int:
        """Accounts that existed at any point in the month."""
        return len(self.lines)

    @property
    def full_month_count(self) -> int:
        return sum(1 for line in self.lines if line.is_full_month)

    @property
    def partial_month_count(self) -> int:
        return self.account_count - self.full_month_count

    @property
    def billable_accounts(self) -> Decimal:
        """Sum of the pro-rata fractions — the number an invoice multiplies."""
        total = sum((line.prorata for line in self.lines), Decimal("0"))
        return total.quantize(PRORATA_PRECISION, rounding=ROUND_HALF_UP)

    @property
    def label(self) -> str:
        return self.period_start.strftime("%B %Y")


def _as_local_date(value) -> date:
    """Interpret a stored datetime in the deployment's timezone."""
    return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()


def build_active_account_report(year: int, month: int) -> ActiveAccountReport:
    """Build the itemised Active Customer Account report for a calendar month."""
    days_in_month = monthrange(year, month)[1]
    period_start = date(year, month, 1)
    period_end = date(year, month, days_in_month)

    lines: list[AccountLine] = []

    # An account is in scope if it was onboarded on or before the month ended
    # and was not archived before the month began.
    candidates = Tenant.objects.filter(created_at__date__lte=period_end).order_by("created_at", "id")

    for tenant in candidates:
        onboarded_on = _as_local_date(tenant.created_at)
        archived_on = _as_local_date(tenant.archived_at) if tenant.archived_at else None

        if archived_on and archived_on < period_start:
            continue

        first_day = max(onboarded_on, period_start)
        last_day = min(archived_on, period_end) if archived_on else period_end

        if last_day < first_day:
            continue

        lines.append(
            AccountLine(
                tenant_id=tenant.id,
                name=tenant.name,
                onboarded_on=onboarded_on,
                archived_on=archived_on,
                active_days=(last_day - first_day).days + 1,
                days_in_month=days_in_month,
            )
        )

    return ActiveAccountReport(
        period_start=period_start,
        period_end=period_end,
        days_in_month=days_in_month,
        lines=lines,
    )
