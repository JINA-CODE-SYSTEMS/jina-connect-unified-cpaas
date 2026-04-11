"""
Cron functions for customer module
"""

import logging

from django.core.management import call_command

logger = logging.getLogger(__name__)


def check_template_status():
    """
    Cron job to check WhatsApp template statuses
    """
    try:
        logger.info("Starting check_template_status cron job")
        call_command("check_template_statuses_cron", "--verbose")
        logger.info("Completed check_template_status cron job successfully")
    except Exception as e:
        logger.error(f"Error in check_template_status cron job: {str(e)}")
        raise
