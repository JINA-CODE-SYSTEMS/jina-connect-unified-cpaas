import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from wa.models import MessageStatus, WAMessage, WATemplate, WAWebhookEvent

logger = logging.getLogger(__name__)


def _dispatch(task, *, what: str, pk) -> None:
    """Queue *task*, falling back to running it in-process.

    Three failure modes are collapsed here, because the symptom of each is the
    same — a row that is never processed and nothing saying so (#269):

    * **No broker configured.** Previously the webhook signal simply had no
      ``else``, so the event was stored and abandoned.
    * **A broker configured but unreachable.** This was the common case and the
      one the old check could never catch: ``CELERY_BROKER_URL`` defaults to
      ``redis://localhost:6379/0``, so the guard tested a non-empty string, not
      a reachable queue, and happily dispatched into nothing.
    * **Dispatch itself raising**, for any other reason.

    Running in-process makes the caller slower, which for a webhook means a
    slower 200 back to META. That is a real cost, but a bounded one, and it is
    strictly better than silently dropping inbound messages.
    """
    if settings.CELERY_BROKER_URL:
        try:
            task.delay(str(pk))
            return
        except Exception as exc:  # noqa: BLE001 — broker down must not lose the row
            logger.warning(
                "[wa.signals] could not queue %s for %s (%s) — running in-process instead",
                what,
                pk,
                exc,
            )
    else:
        logger.info("[wa.signals] no CELERY_BROKER_URL — running %s for %s in-process", what, pk)

    # A Celery task object is callable: this runs the body here and now.
    task(str(pk))


@receiver(post_save, sender=WATemplate)
def handle_pending_template(sender, instance, created, **kwargs):
    """
    Signal that triggers when a WATemplate is saved.
    Logs the event.  Actual BSP submission is handled synchronously in the
    viewset via the adapter layer — this signal is intentionally passive.
    """
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
            logger.info(f"Outgoing message {instance.pk} created - queuing for sending")

            from wa.tasks import send_wa_message

            _dispatch(send_wa_message, what="send", pk=instance.pk)


@receiver(post_save, sender=WAWebhookEvent)
def process_webhook_event(sender, instance, created, **kwargs):
    """
    Signal that triggers when a WAWebhookEvent is created.
    Queues the event for processing via Celery task.
    """
    if created and not instance.is_processed:
        logger.info(f"Webhook event {instance.pk} created - queuing for processing")

        from wa.tasks import process_webhook_event_task

        _dispatch(process_webhook_event_task, what="webhook processing", pk=instance.pk)
