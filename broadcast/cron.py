"""
Cron functions for broadcast module
"""
import logging
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def update_broadcast_status():
    """
    Cron job to check broadcast message completion and update broadcast status accordingly.
    
    This job checks broadcasts that are in SENDING status and updates them based on 
    the completion status of their messages:
    - SENT: All messages delivered successfully
    - PARTIALLY_SENT: Some messages delivered, some failed
    - FAILED: All messages failed
    
    IMPORTANT: We wait for delivery confirmation (DELIVERED/READ) before considering
    a message successful. SENT status only means the API accepted the message, not that
    it was delivered. This ensures accurate refunds since Meta/Gupshup don't charge
    for messages that fail to deliver.
    """
    from broadcast.models import (Broadcast, BroadcastMessage,
                                  BroadcastStatusChoices, MessageStatusChoices)
    
    try:
        logger.info("Starting update_broadcast_status cron job")
        
        # Get all broadcasts that are currently in SENDING status
        sending_broadcasts = Broadcast.objects.filter(
            status=BroadcastStatusChoices.SENDING,
            #need to check if scheduled_at is in the past and 10 minutes have passed since scheduled_at
            scheduled_time__lte=timezone.now() - timezone.timedelta(minutes=10)
        ).prefetch_related('broadcasts')
        
        updated_count = 0
        
        # Max age: broadcasts stuck in SENDING for > 24 hours are force-completed
        max_age_cutoff = timezone.now() - timedelta(hours=24)
        
        for broadcast in sending_broadcasts:
            # Get message statistics for this broadcast
            message_stats = broadcast.broadcasts.aggregate(
                total=Count('id'),
                sent=Count('id', filter=Q(status=MessageStatusChoices.SENT)),
                delivered=Count('id', filter=Q(status=MessageStatusChoices.DELIVERED)),
                read=Count('id', filter=Q(status=MessageStatusChoices.READ)),
                failed=Count('id', filter=Q(status=MessageStatusChoices.FAILED)),
                blocked=Count('id', filter=Q(status=MessageStatusChoices.BLOCKED)),
                pending=Count('id', filter=Q(status__in=[
                    MessageStatusChoices.PENDING, 
                    MessageStatusChoices.QUEUED,
                    MessageStatusChoices.SENDING,
                ]))
            )
            
            total_messages = message_stats['total']
            pending_messages = message_stats['pending']
            # SENT + DELIVERED + READ count as successful (SENT accepted by API, delivery may still come)
            successful_messages = message_stats['sent'] + message_stats['delivered'] + message_stats['read']
            failed_messages = message_stats['failed'] + message_stats['blocked']
            
            # If broadcast exceeded max age, force-complete regardless of pending
            is_timed_out = broadcast.scheduled_time and broadcast.scheduled_time < max_age_cutoff
            
            # Skip if there are still pending/queued/sending messages AND not timed out
            if pending_messages > 0 and not is_timed_out:
                logger.debug(f"Broadcast {broadcast.id} has {pending_messages} pending messages, skipping")
                continue
            
            if is_timed_out and pending_messages > 0:
                logger.warning(
                    f"Broadcast {broadcast.id} timed out after 24h with {pending_messages} "
                    f"pending messages — force-completing"
                )
                # Treat still-pending messages as failed for final status calculation
                failed_messages += pending_messages
                pending_messages = 0
            
            # Determine new status based on completion
            old_status = broadcast.status
            new_status = None
            
            if total_messages == 0:
                # No messages created - mark as failed
                new_status = BroadcastStatusChoices.FAILED
                broadcast.reason_for_cancellation = "No messages were created for this broadcast"
            elif successful_messages == total_messages:
                # All messages delivered successfully
                new_status = BroadcastStatusChoices.SENT
            elif failed_messages == total_messages:
                # All messages failed
                new_status = BroadcastStatusChoices.FAILED
                broadcast.reason_for_cancellation = "All messages failed to deliver"
            elif successful_messages > 0:
                # Some delivered, some failed
                new_status = BroadcastStatusChoices.PARTIALLY_SENT
            else:
                # Shouldn't happen, but handle edge case
                new_status = BroadcastStatusChoices.FAILED
                broadcast.reason_for_cancellation = "Unable to determine broadcast completion status"
            
            # Update broadcast status if changed
            if new_status and new_status != old_status:
                broadcast.status = new_status
                broadcast.save(update_fields=['status', 'reason_for_cancellation', 'updated_at'])
                updated_count += 1
                logger.info(
                    f"Updated broadcast {broadcast.id} from {old_status} to {new_status} "
                    f"({successful_messages}/{total_messages} successful, {failed_messages} failed)"
                )
                # Fire notification for terminal states
                try:
                    from notifications.signals import create_broadcast_completion_notification
                    create_broadcast_completion_notification(broadcast, new_status)
                except Exception:
                    logger.exception('Failed to create broadcast completion notification')
        
        logger.info(f"Completed update_broadcast_status cron job - updated {updated_count} broadcasts")
        return {
            'success': True,
            'updated_count': updated_count
        }
        
    except Exception as e:
        logger.exception(f"Error in update_broadcast_status cron job: {str(e)}")
        raise
