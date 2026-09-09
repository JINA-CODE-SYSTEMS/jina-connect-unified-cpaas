"""Scheduled partner reporting tasks (Fabtary agreement Cl. 4.2)."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from tenants.services.active_account_report import build_active_account_report
from tenants.services.active_account_report_pdf import render_active_account_report_pdf

logger = logging.getLogger(__name__)


def previous_month(today: date | None = None) -> tuple[int, int]:
    """The calendar month before ``today`` in the deployment's timezone."""
    today = today or timezone.localdate()
    first_of_this_month = today.replace(day=1)
    last_month = first_of_this_month - timedelta(days=1)
    return last_month.year, last_month.month


def build_and_send_active_account_report(year: int, month: int, recipients: list[str] | None = None) -> int:
    """Render the month's report and email it. Returns the number of recipients."""
    recipients = recipients if recipients is not None else settings.PARTNER_REPORT_RECIPIENTS

    if not recipients:
        logger.warning("Active Customer Account report for %s-%02d not sent: no recipients configured", year, month)
        return 0

    report = build_active_account_report(year, month)
    pdf = render_active_account_report_pdf(report)

    message = EmailMessage(
        subject=f"Active Customer Account Report — {report.label}",
        body=(
            f"Active Customer Account Report for {report.label}.\n\n"
            f"Accounts in period: {report.account_count}\n"
            f"Billable accounts (pro rata): {report.billable_accounts}\n\n"
            "The attached PDF itemises each account and states the basis of the calculation.\n"
        ),
        to=recipients,
    )
    message.attach(
        f"active-customer-accounts-{year}-{month:02d}.pdf",
        pdf,
        "application/pdf",
    )
    message.send(fail_silently=False)

    logger.info(
        "Active Customer Account report for %s sent to %d recipient(s): %s accounts, %s billable",
        report.label,
        len(recipients),
        report.account_count,
        report.billable_accounts,
    )
    return len(recipients)


@shared_task
def send_monthly_active_account_report(year: int | None = None, month: int | None = None) -> int:
    """Email the Active Customer Account report.

    Defaults to the previous calendar month, since the schedule runs on the 1st
    and a month is only complete once it has ended.
    """
    if year is None or month is None:
        year, month = previous_month()

    return build_and_send_active_account_report(year, month)
