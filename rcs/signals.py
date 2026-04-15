"""RCS signals."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from rcs.models import RCSWebhookEvent


@receiver(post_save, sender=RCSWebhookEvent)
def queue_rcs_event_processing(sender, instance, created, **kwargs):
    if created:
        from rcs.tasks import process_rcs_event_task

        process_rcs_event_task.delay(str(instance.pk))
