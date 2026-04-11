from django.db.models.signals import post_save
from django.dispatch import receiver

from jina_connect import settings
from wa.models import MessageStatus, WAMessage, WATemplate, WAWebhookEvent


@receiver(post_save, sender=WATemplate)
def handle_pending_template(sender, instance, created, **kwargs):
    """
    Signal that triggers when a WATemplate is saved.
    Logs the event.  Actual BSP submission is handled synchronously in the
    viewset via the adapter layer — this signal is intentionally passive.
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"WATemplate post_save: id={instance.id}, created={created}, "
        f"status={instance.status}, needs_sync={instance.needs_sync}"
    )


@receiver(post_save, sender=WAMessage)
def send_outgoing_message_on_create(sender, instance, created, **kwargs):
    """
    Signal that triggers when a WAMessage is created.
    Queues the message for sending via Celery task if it's an outbound pending message.
    """
    if created and instance.direction == "OUTBOUND":
        # Only process messages that are in PENDING status
        if instance.status == MessageStatus.PENDING:
            import logging

            logger = logging.getLogger(__name__)
            logger.info(f"Outgoing message {instance.pk} created - queuing for sending")

            if settings.CELERY_BROKER_URL:
                from wa.tasks import send_wa_message

                print(f"Queuing Celery task to send message {instance.pk}")
                send_wa_message.delay(str(instance.pk))
            else:
                from wa.tasks import send_wa_message

                print(f"Sending message {instance.pk} synchronously (no Celery)")
                send_wa_message(str(instance.pk))


@receiver(post_save, sender=WAWebhookEvent)
def process_webhook_event(sender, instance, created, **kwargs):
    """
    Signal that triggers when a WAWebhookEvent is created.
    Queues the event for processing via Celery task.
    """
    if created and not instance.is_processed:
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"Webhook event {instance.pk} created - queuing for processing")

        if settings.CELERY_BROKER_URL:
            from wa.tasks import process_webhook_event_task

            process_webhook_event_task.delay(str(instance.pk))
