"""SMS signals."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from sms.models import SMSWebhookEvent


@receiver(post_save, sender=SMSWebhookEvent)
def queue_sms_event_processing(sender, instance, created, **kwargs):
    if created:
        from sms.tasks import process_sms_event_task

        process_sms_event_task.delay(str(instance.pk))
