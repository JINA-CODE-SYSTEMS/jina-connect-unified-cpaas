"""
Cron entry points for the tenants app.

django-crontab calls a dotted path, so scheduled work needs a plain function
here rather than a Celery task. This deployment has no celery beat — see
tenants/management/commands/send_active_account_report.py.
"""

import logging

from django.core.management import call_command

logger = logging.getLogger(__name__)


def send_monthly_active_account_report():
    """Deliver the previous month's Active Customer Account report."""
    try:
        logger.info("Starting send_monthly_active_account_report cron job")
        call_command("send_active_account_report")
        logger.info("Completed send_monthly_active_account_report cron job successfully")
    except Exception as exc:
        # Logged and re-raised: a contractual report failing to send must be
        # loud. Silence is what this ticket was about.
        logger.error("Error in send_monthly_active_account_report cron job: %s", exc)
        raise
