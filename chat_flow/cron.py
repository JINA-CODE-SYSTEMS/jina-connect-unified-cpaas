"""
Cron entry points for the chat_flow app.

django-crontab calls a dotted path, and this deployment has no celery beat.
"""

import logging

from django.core.management import call_command

logger = logging.getLogger(__name__)


def expire_idle_chatflow_sessions():
    """End chat-flow sessions that stopped advancing.

    Hourly rather than nightly: the visible symptom of a session outliving its
    conversation is that the flow cannot be edited, and an operator who has
    just been refused should not have to wait until tomorrow.
    """
    try:
        logger.info("Starting expire_idle_chatflow_sessions cron job")
        call_command("expire_chatflow_sessions")
        logger.info("Completed expire_idle_chatflow_sessions cron job successfully")
    except Exception as exc:
        logger.error("Error in expire_idle_chatflow_sessions cron job: %s", exc)
        raise
