"""
Telegram signals — queue event processing on webhook event creation.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from telegram.models import TelegramWebhookEvent


@receiver(post_save, sender=TelegramWebhookEvent)
def queue_tg_event_processing(sender, instance, created, **kwargs):
    """Queue a Celery task to process newly-created webhook events."""
    if created:
        from telegram.tasks import process_tg_event_task

        process_tg_event_task.delay(str(instance.pk))
