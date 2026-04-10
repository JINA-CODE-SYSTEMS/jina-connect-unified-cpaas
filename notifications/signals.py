import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender='broadcast.Broadcast')
def notify_broadcast_scheduled(sender, instance, created, **kwargs):
    """Create notification when a broadcast is scheduled."""
    from broadcast.models import BroadcastStatusChoices

    if not created:
        return
    if instance.status not in (BroadcastStatusChoices.SCHEDULED, BroadcastStatusChoices.QUEUED):
        return

    def _create():
        try:
            from notifications.models import Notification, NotificationType
            Notification.objects.create(
                tenant=instance.tenant,
                notification_type=NotificationType.BROADCAST_SCHEDULED,
                title=f'Broadcast "{instance.name}" scheduled',
                message=f'Your broadcast has been scheduled for {instance.scheduled_time}.',
                metadata={'broadcast_id': instance.id},
            )
        except Exception:
            logger.exception('Failed to create broadcast_scheduled notification')

    transaction.on_commit(_create)


@receiver(post_save, sender='transaction.TenantTransaction')
def notify_wallet_recharged(sender, instance, created, **kwargs):
    """Create notification on successful wallet recharge and check low balance."""
    from transaction.models import TransactionTypeChoices

    if not (created and instance.transaction_type == TransactionTypeChoices.SUCCESS_RECHARGE):
        return

    def _create():
        try:
            from notifications.models import Notification, NotificationType
            amount_str = str(instance.amount)
            Notification.objects.create(
                tenant=instance.tenant,
                notification_type=NotificationType.WALLET_RECHARGED,
                title='Wallet recharged',
                message=f'Your wallet has been recharged with {amount_str}.',
                metadata={'transaction_id': instance.id},
            )
            # Check low balance after recharge
            instance.tenant.refresh_from_db()
            if instance.tenant.is_below_threshold:
                Notification.objects.create(
                    tenant=instance.tenant,
                    notification_type=NotificationType.LOW_BALANCE,
                    title='Low balance warning',
                    message=f'Your wallet balance is below the threshold of {instance.tenant.threshold_alert}.',
                    metadata={},
                )
        except Exception:
            logger.exception('Failed to create wallet notification')

    transaction.on_commit(_create)


def create_template_notification(template, old_status, new_status, reason=None):
    """Called from wa/tasks.py when a template status changes."""
    try:
        from notifications.models import Notification, NotificationType

        tenant = template.wa_app.tenant
        status_lower = new_status.upper()

        if status_lower == 'APPROVED':
            notif_type = NotificationType.TEMPLATE_APPROVED
            title = f'Template "{template.element_name}" approved'
            message = 'Your template has been approved by Meta and is ready to use.'
        elif status_lower in ('REJECTED', 'FAILED'):
            notif_type = NotificationType.TEMPLATE_REJECTED
            title = f'Template "{template.element_name}" rejected'
            message = f'Your template was rejected. Reason: {reason or "Not specified"}'
        else:
            return

        Notification.objects.create(
            tenant=tenant,
            notification_type=notif_type,
            title=title,
            message=message,
            metadata={'template_id': str(template.id), 'template_name': template.element_name},
        )
    except Exception:
        logger.exception('Failed to create template notification')


# ── Notification helpers for callers outside signals ──────────────────


def create_broadcast_completion_notification(broadcast, new_status):
    """Called from broadcast/cron.py when a broadcast reaches a terminal state."""
    try:
        from broadcast.models import BroadcastStatusChoices
        from notifications.models import Notification, NotificationType

        if new_status in (BroadcastStatusChoices.SENT, BroadcastStatusChoices.PARTIALLY_SENT):
            notif_type = NotificationType.BROADCAST_COMPLETED
            title = f'Broadcast "{broadcast.name}" completed'
            message = 'Your broadcast has been delivered successfully.'
        elif new_status == BroadcastStatusChoices.FAILED:
            notif_type = NotificationType.BROADCAST_FAILED
            title = f'Broadcast "{broadcast.name}" failed'
            message = broadcast.reason_for_cancellation or 'Your broadcast failed to deliver.'
        else:
            return

        Notification.objects.create(
            tenant=broadcast.tenant,
            notification_type=notif_type,
            title=title,
            message=message,
            metadata={'broadcast_id': broadcast.id},
        )
    except Exception:
        logger.exception('Failed to create broadcast completion notification')


def create_template_submitted_notification(template):
    """Called from wa/viewsets when a template is submitted to BSP."""
    try:
        from notifications.models import Notification, NotificationType

        Notification.objects.create(
            tenant=template.wa_app.tenant,
            notification_type=NotificationType.TEMPLATE_SUBMITTED,
            title=f'Template "{template.element_name}" submitted',
            message='Your template has been submitted for review.',
            metadata={'template_id': str(template.id), 'template_name': template.element_name},
        )
    except Exception:
        logger.exception('Failed to create template_submitted notification')


def create_contact_added_notification(contact):
    """Called from contacts viewset when a single contact is created."""
    try:
        from notifications.models import Notification, NotificationType

        name = f'{contact.first_name or ""} {contact.last_name or ""}'.strip() or str(contact.phone)
        Notification.objects.create(
            tenant=contact.tenant,
            notification_type=NotificationType.CONTACT_ADDED,
            title='New contact added',
            message=f'{name} has been added to your contacts.',
            metadata={'contact_id': contact.id},
        )
    except Exception:
        logger.exception('Failed to create contact_added notification')


def create_contact_imported_notification(tenant, created_count):
    """Called from contacts viewset after CSV bulk import."""
    try:
        from notifications.models import Notification, NotificationType

        Notification.objects.create(
            tenant=tenant,
            notification_type=NotificationType.CONTACT_IMPORTED,
            title='Contacts imported',
            message=f'{created_count} contact{"s" if created_count != 1 else ""} imported from CSV.',
            metadata={'created_count': created_count},
        )
    except Exception:
        logger.exception('Failed to create contact_imported notification')


def create_automation_notification(chatflow, notif_type_str):
    """Called from chat_flow serializer on flow update or failure."""
    try:
        from notifications.models import Notification, NotificationType

        tenant = chatflow.tenant if hasattr(chatflow, 'tenant') else (
            chatflow.start_template.wa_app.tenant if chatflow.start_template else None
        )
        if not tenant:
            return

        if notif_type_str == 'updated':
            Notification.objects.create(
                tenant=tenant,
                notification_type=NotificationType.AUTOMATION_UPDATED,
                title=f'Flow "{chatflow.name}" updated',
                message='Your chatflow has been updated.',
                metadata={'chatflow_id': chatflow.id},
            )
        elif notif_type_str == 'failed':
            Notification.objects.create(
                tenant=tenant,
                notification_type=NotificationType.AUTOMATION_FAILED,
                title=f'Flow "{chatflow.name}" failed',
                message='Your chatflow encountered an error during execution.',
                metadata={'chatflow_id': chatflow.id},
            )
    except Exception:
        logger.exception('Failed to create automation notification')
