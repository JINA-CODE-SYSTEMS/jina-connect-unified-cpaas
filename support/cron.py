"""Cron entry points for the support app.

django-crontab calls a dotted path, and this deployment has no celery beat.
"""

import logging

from support import conf, services

logger = logging.getLogger(__name__)


def auto_close_resolved_tickets():
    """Close resolved tickets the customer has not answered within the promised window."""
    if not conf.is_configured():
        return
    try:
        closed = services.auto_close_due()
        if closed:
            logger.info("support: auto-closed %s resolved ticket(s)", closed)
    except Exception as exc:
        logger.error("Error in auto_close_resolved_tickets cron job: %s", exc)
        raise
