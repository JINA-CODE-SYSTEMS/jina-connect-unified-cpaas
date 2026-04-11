import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def cleanup_old_notifications():
    """Delete notifications older than 90 days."""
    from notifications.models import Notification

    cutoff = timezone.now() - timedelta(days=90)
    deleted, _ = Notification.objects.filter(created_at__lt=cutoff).delete()
    if deleted:
        logger.info("Cleaned up %d notifications older than 90 days", deleted)
