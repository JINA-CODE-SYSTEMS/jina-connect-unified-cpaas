"""
Cron entry points for the availability app.

django-crontab calls a dotted path, and this deployment has no celery beat.
"""

import logging

from django.core.management import call_command

logger = logging.getLogger(__name__)


def send_monthly_availability_report():
    """Deliver the previous month's availability report."""
    try:
        logger.info("Starting send_monthly_availability_report cron job")
        call_command("send_availability_report")
        logger.info("Completed send_monthly_availability_report cron job successfully")
    except Exception as exc:
        logger.error("Error in send_monthly_availability_report cron job: %s", exc)
        raise
