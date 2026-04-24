"""
Telegram signals — queue event processing on webhook event creation.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from telegram.models import TelegramOutboundMessage, TelegramWebhookEvent

logger = logging.getLogger(__name__)


@receiver(post_save, sender=TelegramWebhookEvent)
def queue_tg_event_processing(sender, instance, created, **kwargs):
    """Queue a Celery task to process newly-created webhook events."""
    if created:
        from telegram.tasks import process_tg_event_task

        process_tg_event_task.delay(str(instance.pk))


@receiver(post_save, sender=TelegramOutboundMessage)
def broadcast_telegram_status_update(sender, instance: TelegramOutboundMessage, created: bool, **kwargs):
    """
    Broadcast Telegram message status updates to WebSocket clients.
    This enables real-time status tick updates in the team inbox UI.
    """
    if created:
        return  # Only broadcast updates, not creation

    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        from team_inbox.models import Messages

        # Find the team_inbox message linked to this outbound message
        inbox_message = Messages.objects.filter(telegram_outbound=instance).first()
        if not inbox_message:
            logger.warning(
                f"[broadcast_telegram_status_update] No inbox message found for telegram_outbound {instance.id}"
            )
            return

        # Get channel layer
        channel_layer = get_channel_layer()
        if not channel_layer:
            logger.warning("[broadcast_telegram_status_update] Channel layer not available")
            return

        # Build status update event matching WhatsApp format
        group_name = f"team_inbox_{inbox_message.tenant_id}"
        event_data = {
            "id": inbox_message.id,
            "message_id": inbox_message.message_id.numbering if inbox_message.message_id else None,
            "outgoing_status": instance.status,
            "outgoing_sent_at": instance.sent_at.isoformat() if instance.sent_at else None,
            "outgoing_delivered_at": instance.delivered_at.isoformat() if instance.delivered_at else None,
            "outgoing_read_at": instance.read_at.isoformat() if instance.read_at else None,
            "outgoing_failed_at": instance.failed_at.isoformat() if instance.failed_at else None,
        }

        logger.info(
            f"[broadcast_telegram_status_update] Broadcasting status {instance.status} for message {inbox_message.id} to {group_name}"
        )

        async_to_sync(channel_layer.group_send)(
            group_name, {"type": "team_message", "message": {"type": "message_status_update", **event_data}}
        )

        logger.info(
            f"[broadcast_telegram_status_update] Successfully broadcast status update for message {inbox_message.id}"
        )

    except Exception as e:
        logger.error(
            f"[broadcast_telegram_status_update] Error broadcasting status for {instance.id}: {str(e)}", exc_info=True
        )
