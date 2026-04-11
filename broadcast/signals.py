import logging

from django.conf import settings
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from broadcast.models import Broadcast, BroadcastStatusChoices
from broadcast.services.credit_manager import BroadcastCreditManager, InsufficientBalanceError
from wa.models import WABroadcast

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Broadcast)
@receiver(pre_save, sender=WABroadcast)
def cache_broadcast_old_state(sender, instance, **kwargs):
    """
    Cache the old state of broadcast before save for comparison in post_save.
    This runs before the instance is saved to the database.
    """
    if instance.pk:  # Only for updates, not new creations
        try:
            old_instance = Broadcast.objects.only("status", "scheduled_time", "task_id").get(pk=instance.pk)
            instance._old_state = {
                "status": old_instance.status,
                "scheduled_time": old_instance.scheduled_time,
                "task_id": old_instance.task_id,
            }
            logger.debug(
                f"[PRE_SAVE] Cached old state for broadcast {instance.pk}: status={old_instance.status}, task_id={old_instance.task_id}"
            )
        except Broadcast.DoesNotExist:
            logger.debug("[PRE_SAVE] Broadcast %s does not exist yet (shouldn't happen)", instance.pk)
            pass


@receiver(post_save, sender=Broadcast)
@receiver(post_save, sender=WABroadcast)
def handle_broadcast_scheduling(sender, instance, created, update_fields, **kwargs):
    """
    Handle broadcast scheduling logic for both creation and updates.
    Uses the new model properties: good_to_send, green_signal_stages,
    in_the_past, threshold_time_from_now, assign_now

    All datetime comparisons are timezone-aware using Django's timezone utilities.
    """
    # Get current time in UTC (timezone-aware)
    now = timezone.now()

    if created:
        logger.debug(f"SIGNAL FIRED! Broadcast {instance.id} created with status: {instance.status}")
        logger.debug(f"   Model type: {type(instance).__name__}")
        logger.debug(f"   Good to send: {instance.good_to_send}")

        # Only auto-schedule if broadcast has green signal (QUEUED or SCHEDULED)
        if instance.status not in instance.green_signal_stages:
            logger.debug(
                f"[CREATION] Broadcast {instance.id} created with status {instance.status}, no scheduling needed"
            )
            return

        # Check if broadcast is good to send
        if not instance.good_to_send:
            logger.debug("[CREATION] Broadcast %s not ready to send (missing recipients or template)", instance.id)
            return

        logger.debug(f"[BROADCAST CREATE] ID: {instance.id}, Status: {instance.status}")

        # Note: assign_now() is already called in model.save(), so scheduled_time should be set
        if not instance.scheduled_time:
            logger.warning("Broadcast %s has no scheduled_time after save (assign_now should have set it)", instance.id)
            return

        # Validate scheduled_time is not in the past
        if instance.in_the_past and instance.status == BroadcastStatusChoices.QUEUED:
            logger.warning("[VALIDATION ERROR] Broadcast %s: Cannot schedule in the past", instance.id)
            Broadcast.objects.filter(pk=instance.id).update(
                status=BroadcastStatusChoices.FAILED,
                reason_for_cancellation="Cannot schedule broadcast in the past when status is QUEUED",
            )
            return

        # Validate scheduled_time is at least threshold time from now
        if instance.scheduled_time < instance.threshold_time_from_now:
            logger.warning(
                f"[VALIDATION ERROR] Broadcast {instance.id}: Schedule time must be at least {settings.BROADCAST_CANCELLATION_TIME_LIMIT_IN_MINUTES} minutes ahead"
            )
            Broadcast.objects.filter(pk=instance.id).update(
                status=BroadcastStatusChoices.FAILED,
                reason_for_cancellation=f"Scheduled time must be at least {settings.BROADCAST_CANCELLATION_TIME_LIMIT_IN_MINUTES} minutes in the future",
            )
            return

        # Schedule Celery task
        _schedule_broadcast_task(instance, now)

    else:
        # === UPDATE LOGIC ===
        logger.debug(f"[BROADCAST UPDATE] ID: {instance.id}, Status: {instance.status}")
        logger.debug("[UPDATE FIELDS] %s", update_fields)

        # Skip if this is an internal update from our own signal (task_id)
        if update_fields and set(update_fields).issubset({"task_id"}):
            logger.debug("[UPDATE] Skipping internal update for broadcast %s", instance.pk)
            return

        # Get cached old state
        old_state = getattr(instance, "_old_state", None)

        if not old_state:
            logger.debug("[UPDATE] No cached state for broadcast %s", instance.pk)
            # This might be a newly created broadcast being updated to QUEUED
            # Check if it needs scheduling
            if instance.status in instance.green_signal_stages and instance.good_to_send:
                logger.debug(f"[UPDATE] New broadcast {instance.pk} updated to {instance.status}, scheduling...")

                # Validate scheduling
                if not instance.scheduled_time:
                    logger.warning("Broadcast %s has no scheduled_time", instance.id)
                    return

                if instance.in_the_past and instance.status == BroadcastStatusChoices.QUEUED:
                    Broadcast.objects.filter(pk=instance.id).update(
                        status=BroadcastStatusChoices.FAILED,
                        reason_for_cancellation="Cannot schedule broadcast in the past when status is QUEUED",
                    )
                    return

                if instance.scheduled_time < instance.threshold_time_from_now:
                    Broadcast.objects.filter(pk=instance.id).update(
                        status=BroadcastStatusChoices.FAILED,
                        reason_for_cancellation=f"Scheduled time must be at least {settings.BROADCAST_CANCELLATION_TIME_LIMIT_IN_MINUTES} minutes in the future",
                    )
                    return

                # Schedule the task
                _schedule_broadcast_task(instance, now)
            return

        # Determine what changed
        status_changed = old_state["status"] != instance.status
        scheduled_time_changed = old_state["scheduled_time"] != instance.scheduled_time
        old_was_green = old_state["status"] in instance.green_signal_stages
        new_is_green = instance.status in instance.green_signal_stages

        logger.debug(
            f"[UPDATE CHANGES] Broadcast {instance.id}: "
            f"status_changed={status_changed} (old={old_state['status']}, new={instance.status}), "
            f"scheduled_time_changed={scheduled_time_changed}, "
            f"old_was_green={old_was_green}, new_is_green={new_is_green}"
        )

        # Handle status change to CANCELLED or from green to non-green (e.g., SCHEDULED -> DRAFT)
        if status_changed and (
            instance.status == BroadcastStatusChoices.CANCELLED or (old_was_green and not new_is_green)
        ):
            _handle_broadcast_cancellation(instance, old_state["task_id"])

        # Handle status change from non-green to green (e.g., CANCELLED -> SCHEDULED, DRAFT -> QUEUED)
        elif status_changed and not old_was_green and new_is_green:
            if instance.good_to_send:
                logger.debug(
                    f"[UPDATE] Broadcast {instance.id} changed from {old_state['status']} to {instance.status}, scheduling..."
                )

                # Validate scheduling
                if not instance.scheduled_time:
                    logger.warning("Broadcast %s has no scheduled_time", instance.id)
                    return

                if instance.in_the_past and instance.status == BroadcastStatusChoices.QUEUED:
                    Broadcast.objects.filter(pk=instance.id).update(
                        status=BroadcastStatusChoices.FAILED,
                        reason_for_cancellation="Cannot schedule broadcast in the past when status is QUEUED",
                    )
                    return

                if instance.scheduled_time < instance.threshold_time_from_now:
                    Broadcast.objects.filter(pk=instance.id).update(
                        status=BroadcastStatusChoices.FAILED,
                        reason_for_cancellation=f"Scheduled time must be at least {settings.BROADCAST_CANCELLATION_TIME_LIMIT_IN_MINUTES} minutes in the future",
                    )
                    return

                # Schedule new task
                _schedule_broadcast_task(instance, now)
            else:
                logger.debug("[UPDATE] Broadcast %s not ready to send (missing recipients or template)", instance.id)

        # Handle rescheduling - only if status is still in green signal stages
        elif scheduled_time_changed and instance.status in instance.green_signal_stages:
            _handle_broadcast_rescheduling(instance, old_state["task_id"], now)

        # Clean up instance state after processing
        if hasattr(instance, "_old_state"):
            del instance._old_state
        logger.debug("[CLEANUP] Removed cached state for broadcast %s", instance.pk)


def _schedule_broadcast_task(broadcast_instance, current_time):
    """
    Schedule a Celery task for the broadcast.

    Args:
        broadcast_instance: The broadcast instance to schedule
        current_time: Current timezone-aware datetime (from timezone.now())
    """
    try:
        from broadcast.tasks import setup_broadcast_task

        # Ensure both datetimes are timezone-aware for accurate calculation
        scheduled_time = broadcast_instance.scheduled_time
        if timezone.is_naive(scheduled_time):
            # Convert naive datetime to timezone-aware (assume it's in default timezone)
            scheduled_time = timezone.make_aware(scheduled_time)
            logger.warning("Scheduled time was naive, converted to timezone-aware: %s", scheduled_time)

        # Calculate countdown in seconds
        delta = (scheduled_time - current_time).total_seconds()
        countdown = max(0, int(delta))

        logger.debug("[TASK SCHEDULE INFO] Broadcast %s:", broadcast_instance.id)
        logger.debug("  Current time (UTC): %s", current_time)
        logger.debug("  Scheduled time: %s", scheduled_time)
        logger.debug("  Countdown: %ss (%.1f minutes)", countdown, countdown / 60)

        # Deduct credits BEFORE scheduling the Celery task to prevent race condition
        try:
            credit_manager = BroadcastCreditManager()
            credit_manager.deduct_credits_for_broadcast(broadcast_instance)
        except InsufficientBalanceError as e:
            error_msg = f"Insufficient balance: {str(e)}"
            logger.error("[CREDIT ERROR] %s", error_msg)
            # Mark broadcast as failed — no task was scheduled
            Broadcast.objects.filter(pk=broadcast_instance.pk).update(
                status=BroadcastStatusChoices.FAILED, reason_for_cancellation=error_msg
            )
            return
        except Exception as e:
            error_msg = f"Failed to deduct credits: {str(e)}"
            logger.error("[CREDIT ERROR] %s", error_msg)
            # Don't fail the broadcast for non-balance errors, just log

        # Schedule the Celery task only after credits are secured
        task_result = setup_broadcast_task.apply_async(args=[broadcast_instance.id], countdown=countdown)

        # Save task_id
        broadcast_instance.task_id = task_result.id
        broadcast_instance.reason_for_cancellation = None
        broadcast_instance.status = BroadcastStatusChoices.SCHEDULED
        # Prevent infinite recursion by updating only specific fields
        Broadcast.objects.filter(pk=broadcast_instance.pk).update(
            task_id=task_result.id, reason_for_cancellation=None, status=BroadcastStatusChoices.SCHEDULED
        )

        logger.debug(f"[TASK SCHEDULED] Broadcast {broadcast_instance.id}: Task {task_result.id} in {countdown}s")

    except Exception as e:
        error_msg = f"Failed to schedule broadcast task: {str(e)}"
        logger.error("[TASK ERROR] %s", error_msg)
        # Use queryset update to avoid recursion
        Broadcast.objects.filter(pk=broadcast_instance.pk).update(
            status=BroadcastStatusChoices.FAILED, reason_for_cancellation=error_msg
        )


def _handle_broadcast_cancellation(broadcast_instance, old_task_id):
    """
    Handle broadcast cancellation - cancel the Celery task.
    """
    logger.debug("[CANCEL] Broadcast %s cancelled", broadcast_instance.id)

    if old_task_id:
        try:
            from broadcast.tasks import cancel_broadcast_task

            cancel_broadcast_task.delay(old_task_id)
            logger.debug("[TASK CANCELLED] Task %s for broadcast %s", old_task_id, broadcast_instance.id)

            # Clear task_id
            broadcast_instance.task_id = None
            Broadcast.objects.filter(pk=broadcast_instance.pk).update(task_id=None)

        except Exception as e:
            error_msg = f"Failed to cancel task {old_task_id}: {str(e)}"
            logger.error("[CANCEL ERROR] %s", error_msg)
            # Save the error to reason_for_cancellation even if cancellation fails
            if not broadcast_instance.reason_for_cancellation:
                Broadcast.objects.filter(pk=broadcast_instance.pk).update(reason_for_cancellation=error_msg)


def _handle_broadcast_rescheduling(broadcast_instance, old_task_id, current_time):
    """
    Handle broadcast rescheduling - cancel old task and create new one.
    Uses model properties for validation.

    Args:
        broadcast_instance: The broadcast instance being rescheduled
        old_task_id: The task ID of the old scheduled task
        current_time: Current timezone-aware datetime (from timezone.now())
    """
    logger.debug("[RESCHEDULE] Broadcast %s to %s", broadcast_instance.id, broadcast_instance.scheduled_time)

    # Validate new scheduled_time using model properties
    if broadcast_instance.scheduled_time:
        # Ensure scheduled_time is timezone-aware
        scheduled_time = broadcast_instance.scheduled_time
        if timezone.is_naive(scheduled_time):
            scheduled_time = timezone.make_aware(scheduled_time)
            logger.warning("Scheduled time was naive, converted to timezone-aware: %s", scheduled_time)

        logger.debug("[RESCHEDULE VALIDATION] Current time (UTC): %s", current_time)
        logger.debug("[RESCHEDULE VALIDATION] New scheduled time: %s", scheduled_time)
        logger.debug("[RESCHEDULE VALIDATION] Threshold time: %s", broadcast_instance.threshold_time_from_now)

        if broadcast_instance.in_the_past and broadcast_instance.status == BroadcastStatusChoices.QUEUED:
            error_msg = "Cannot reschedule broadcast to the past when status is QUEUED"
            logger.error("[RESCHEDULE ERROR] %s", error_msg)
            Broadcast.objects.filter(pk=broadcast_instance.pk).update(
                status=BroadcastStatusChoices.FAILED, reason_for_cancellation=error_msg
            )
            return  # Don't raise, just fail the broadcast

        if broadcast_instance.scheduled_time < broadcast_instance.threshold_time_from_now:
            error_msg = f"New scheduled time must be at least {settings.BROADCAST_CANCELLATION_TIME_LIMIT_IN_MINUTES} minutes in the future"
            logger.error("[RESCHEDULE ERROR] %s", error_msg)
            Broadcast.objects.filter(pk=broadcast_instance.pk).update(
                status=BroadcastStatusChoices.FAILED, reason_for_cancellation=error_msg
            )
            return  # Don't raise, just fail the broadcast

    # Cancel old task
    if old_task_id:
        try:
            from broadcast.tasks import cancel_broadcast_task

            cancel_broadcast_task.delay(old_task_id)
            logger.debug("[TASK CANCELLED] Old task %s for rescheduling", old_task_id)
        except Exception as e:
            error_msg = f"Failed to cancel old task during rescheduling: {str(e)}"
            logger.error("[CANCEL ERROR] %s", error_msg)
            # Don't fail the rescheduling, but log the error
            if not broadcast_instance.reason_for_cancellation:
                Broadcast.objects.filter(pk=broadcast_instance.pk).update(reason_for_cancellation=error_msg)

    # Schedule new task
    _schedule_broadcast_task(broadcast_instance, current_time)


@receiver(post_save, sender=Broadcast)
@receiver(post_save, sender=WABroadcast)
def handle_broadcast_completion_refund(sender, instance, created, update_fields, **kwargs):
    """
    Process refund for failed messages when broadcast completes.
    Runs after broadcast reaches SENT, PARTIALLY_SENT, or FAILED status.
    """
    if created:
        return  # Only process on updates

    # Check if broadcast is complete
    if instance.status in [
        BroadcastStatusChoices.SENT,
        BroadcastStatusChoices.PARTIALLY_SENT,
        BroadcastStatusChoices.FAILED,
        BroadcastStatusChoices.CANCELLED,
    ]:
        try:
            credit_manager = BroadcastCreditManager()
            credit_manager.process_refund_for_broadcast(instance)
        except Exception as e:
            logger.error("[REFUND ERROR] Failed to process refund for broadcast %s: %s", instance.id, e)


# Helper function for validation in forms/serializers
def validate_broadcast_schedule(broadcast_instance):
    """
    Utility function to validate broadcast scheduling rules using model properties.
    Can be used in forms, serializers, or API endpoints.

    Args:
        broadcast_instance: Broadcast instance to validate

    Returns:
        tuple: (is_valid, error_message)
    """
    if not broadcast_instance.scheduled_time:
        return False, "Scheduled time is required"

    if broadcast_instance.in_the_past and broadcast_instance.status == BroadcastStatusChoices.QUEUED:
        return False, "Cannot schedule broadcast in the past when status is QUEUED"

    if broadcast_instance.scheduled_time < broadcast_instance.threshold_time_from_now:
        return (
            False,
            f"Scheduled time must be at least {settings.BROADCAST_CANCELLATION_TIME_LIMIT_IN_MINUTES} minutes in the future",
        )

    return True, "Valid schedule"


class BroadcastSchedulingError(Exception):
    """Custom exception for broadcast scheduling validation errors"""

    pass
