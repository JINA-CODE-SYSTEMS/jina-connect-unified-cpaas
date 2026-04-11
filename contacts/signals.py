"""
Signals for contacts app.

Automatically creates Event entries when TenantContact assignment changes.
"""

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(pre_save, sender="contacts.TenantContact")
def capture_previous_assignment_state(sender, instance, **kwargs):
    """
    Capture the previous assignment and status state before saving.
    This is stored on the instance temporarily for use in post_save.
    """
    if instance.pk:
        try:
            from contacts.models import TenantContact

            old_instance = TenantContact.objects.filter(pk=instance.pk).first()
            if old_instance:
                # Store previous assignment state on instance for post_save signal
                instance._previous_assigned_to_type = old_instance.assigned_to_type
                instance._previous_assigned_to_id = old_instance.assigned_to_id
                instance._previous_assigned_to_user_id = old_instance.assigned_to_user_id
                # Store previous status
                instance._previous_status = old_instance.status
            else:
                instance._previous_assigned_to_type = None
                instance._previous_assigned_to_id = None
                instance._previous_assigned_to_user_id = None
                instance._previous_status = None
        except Exception as e:
            logger.error(f"Error capturing previous assignment state: {e}")
            instance._previous_assigned_to_type = None
            instance._previous_assigned_to_id = None
            instance._previous_assigned_to_user_id = None
            instance._previous_status = None
    else:
        # New instance
        instance._previous_assigned_to_type = None
        instance._previous_assigned_to_id = None
        instance._previous_assigned_to_user_id = None
        instance._previous_status = None


@receiver(post_save, sender="contacts.TenantContact")
def create_assignment_event_on_change(sender, instance, created, **kwargs):
    """
    Create an Event entry when assignment changes.

    This signal fires after every save of TenantContact.
    It compares the current assignment state with the previous state
    and creates an Event entry if there was a change.
    """
    # Skip if explicitly told to (e.g., during bulk operations or when viewset handles it)
    if getattr(instance, "_skip_assignment_event", False):
        return

    # Get previous state from pre_save signal
    previous_type = getattr(instance, "_previous_assigned_to_type", None)
    previous_id = getattr(instance, "_previous_assigned_to_id", None)
    previous_user_id = getattr(instance, "_previous_assigned_to_user_id", None)

    # Current state
    current_type = instance.assigned_to_type
    current_id = instance.assigned_to_id
    current_user_id = instance.assigned_to_user_id if instance.assigned_to_user else None

    # Check if assignment actually changed
    assignment_changed = (
        previous_type != current_type or previous_id != current_id or previous_user_id != current_user_id
    )

    # For new instances, only create event if there's an actual assignment (not UNASSIGNED)
    if created:
        from contacts.models import AssigneeTypeChoices

        if current_type == AssigneeTypeChoices.UNASSIGNED:
            return  # Don't create event for initial unassigned state
        assignment_changed = True

    if not assignment_changed:
        return

    try:
        from django.contrib.auth import get_user_model

        from contacts.models import AssigneeTypeChoices
        from team_inbox.models import ActorTypeChoices, Event, EventTypeChoices

        User = get_user_model()

        # Get the previous user object if needed
        previous_user = None
        if previous_user_id:
            previous_user = User.objects.filter(pk=previous_user_id).first()

        # Determine who made the change (assigned_by)
        assigned_by_type = instance.assigned_by_type
        assigned_by_id = instance.assigned_by_id
        assigned_by_user = instance.assigned_by_user

        # Map AssigneeTypeChoices to ActorTypeChoices
        def map_to_actor_type(assignee_type):
            if assignee_type == AssigneeTypeChoices.USER:
                return ActorTypeChoices.USER
            elif assignee_type == AssigneeTypeChoices.BOT:
                return ActorTypeChoices.BOT
            elif assignee_type == AssigneeTypeChoices.CHATFLOW:
                return ActorTypeChoices.CHATFLOW
            return None

        # Create MessageEventIds entry for timeline ordering
        from team_inbox.models import MessageEventIds

        event_id_entry = MessageEventIds.objects.create()

        # Determine event type based on whether this is an assignment or unassignment
        is_unassignment = current_type == AssigneeTypeChoices.UNASSIGNED
        event_type = EventTypeChoices.TICKET_UNASSIGNED if is_unassignment else EventTypeChoices.TICKET_ASSIGNED

        # Create the Event entry
        event = Event.objects.create(
            event_id=event_id_entry,  # Link to MessageEventIds for timeline ordering
            tenant=instance.tenant,
            contact=instance,
            event_type=event_type,
            note=instance.assignment_note,
            icon="👤" if not is_unassignment else "🚫",
            color_background="#E3F2FD" if not is_unassignment else "#FFF3E0",
            color_text="#1565C0" if not is_unassignment else "#E65100",
            # Created by (same as assigned_by for signal-created events)
            created_by_type=map_to_actor_type(assigned_by_type) if assigned_by_type else ActorTypeChoices.USER,
            created_by_id=assigned_by_id,
            created_by_user=assigned_by_user,
            # Assigned by
            assigned_by_type=map_to_actor_type(assigned_by_type) if assigned_by_type else None,
            assigned_by_id=assigned_by_id,
            assigned_by_user=assigned_by_user,
            # Assigned to
            assigned_to_type=map_to_actor_type(current_type)
            if current_type != AssigneeTypeChoices.UNASSIGNED
            else None,
            assigned_to_id=current_id,
            assigned_to_user=instance.assigned_to_user,
            # Store previous state in event_data for reference
            event_data={
                "previous_assigned_to_type": previous_type,
                "previous_assigned_to_id": previous_id,
                "previous_assigned_to_user_id": previous_user_id,
                "metadata": getattr(instance, "_assignment_metadata", {}),
            },
        )

        logger.info(
            f"Created assignment event {event.pk} (event_id={event_id_entry.pk}) for contact {instance.pk}: "
            f"{previous_type}({previous_id}) -> {current_type}({current_id})"
        )

        # Broadcast via WebSocket
        _broadcast_assignment_event(instance, event, previous_type, previous_id, previous_user)

        # Trigger ChatFlow if newly assigned to a ChatFlow
        if current_type == AssigneeTypeChoices.CHATFLOW and current_id:
            from chat_flow.models import ChatFlow

            try:
                flow = ChatFlow.objects.get(id=current_id)
                if not flow.is_active:
                    logger.info(f"Skipping chatflow start for contact {instance.id} — flow {current_id} is inactive")
                else:
                    _trigger_chatflow_start_session(instance, current_id)
            except ChatFlow.DoesNotExist:
                logger.warning(f"ChatFlow {current_id} does not exist, skipping trigger")

    except Exception as e:
        logger.exception(f"Error creating assignment event for contact {instance.pk}: {e}")


def _broadcast_assignment_event(contact, event, previous_type, previous_id, previous_user):
    """Broadcast assignment event via WebSocket using team_message."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if not channel_layer:
            logger.warning("No channel layer available for WebSocket broadcast")
            return

        room_group_name = f"team_inbox_{contact.tenant_id}"

        # Broadcast as new_event via team_message (same pattern as other events)
        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                "type": "team_message",
                "message": {
                    "type": "new_event",
                    "contact_id": contact.id,
                    "event": {
                        "id": event.pk,
                        "event_type": event.event_type,
                        "event_type_display": event.get_event_type_display(),
                        "note": event.note,
                        "created_by_name": event.created_by_name,
                        "assigned_by_name": event.assigned_by_name,
                        "assigned_to_name": event.assigned_to_name,
                        "assigned_to_type": event.assigned_to_type,  # For frontend to know if CHATFLOW
                        "assigned_to_id": event.assigned_to_id,
                        "icon": event.icon,
                        "color_background": event.color_background,
                        "color_text": event.color_text,
                        "event_data": event.event_data,
                        "created_at": event.created_at.isoformat() if event.created_at else None,
                    },
                },
            },
        )
        logger.info(f"Broadcasted new_event for contact {contact.id} to room {room_group_name}")
    except Exception as e:
        logger.error(f"Failed to broadcast assignment event: {e}")


def _trigger_chatflow_start_session(contact, chatflow_id: int):
    """
    Trigger ChatFlow execution when a contact is assigned to a ChatFlow.

    This starts the ChatFlow session and sends the first template message.
    The execution is done via Celery task to avoid blocking the signal.

    Args:
        contact: TenantContact instance
        chatflow_id: ID of the ChatFlow to start
    """
    try:
        from chat_flow.models import ChatFlow as ChatFlowModel
        from chat_flow.tasks import start_chatflow_session_task

        # Check if the flow is active before triggering
        if not ChatFlowModel.objects.filter(id=chatflow_id, is_active=True).exists():
            logger.warning(f"ChatFlow {chatflow_id} is inactive, skipping session start for contact {contact.id}")
            return

        # Use Celery task for async execution
        start_chatflow_session_task.delay(chatflow_id=chatflow_id, contact_id=contact.id)

        logger.info(f"Triggered ChatFlow {chatflow_id} start_session for contact {contact.id} (queued as Celery task)")

    except ImportError:
        # Fallback: Direct execution if Celery task not available
        logger.warning("Celery task not available, executing ChatFlow start_session directly")
        try:
            from chat_flow.models import ChatFlow
            from chat_flow.services.graph_executor import get_executor

            flow = ChatFlow.objects.get(id=chatflow_id)
            executor = get_executor(flow)
            result = executor.start_session(contact_id=contact.id)

            logger.info(
                f"ChatFlow {chatflow_id} started for contact {contact.id}. "
                f"Current node: {result.get('current_node_id')}"
            )
        except Exception as e:
            logger.exception(f"Failed to start ChatFlow {chatflow_id} for contact {contact.id}: {e}")
    except Exception as e:
        logger.exception(f"Failed to trigger ChatFlow {chatflow_id} for contact {contact.id}: {e}")


@receiver(post_save, sender="contacts.TenantContact")
def create_status_change_event(sender, instance, created, **kwargs):
    """
    Create an Event entry when ticket status changes (open/close).

    This signal fires after every save of TenantContact.
    It compares the current status with the previous status
    and creates TICKET_CLOSED or TICKET_REOPENED events.
    """
    # Skip if explicitly told to
    if getattr(instance, "_skip_status_event", False):
        return

    # Skip for new instances (no status change on creation)
    if created:
        return

    # Get previous status from pre_save signal
    previous_status = getattr(instance, "_previous_status", None)
    current_status = instance.status

    # Check if status actually changed
    if previous_status == current_status or previous_status is None:
        return

    try:
        from contacts.models import TicketStatusChoices
        from team_inbox.models import ActorTypeChoices, Event, EventTypeChoices, MessageEventIds

        # Determine event type
        if current_status == TicketStatusChoices.CLOSED:
            event_type = EventTypeChoices.TICKET_CLOSED
            icon = "✅"
            color_background = "#E8F5E9"
            color_text = "#2E7D32"
        elif current_status == TicketStatusChoices.OPEN and previous_status == TicketStatusChoices.CLOSED:
            event_type = EventTypeChoices.TICKET_REOPENED
            icon = "🔄"
            color_background = "#FFF8E1"
            color_text = "#F57F17"
        else:
            # Unknown status change, skip
            return

        # Get actor info (who made the change)
        closed_by_user = getattr(instance, "_status_changed_by_user", None)
        reason = getattr(instance, "_status_change_reason", None) or ""

        # Create MessageEventIds entry for timeline ordering
        event_id_entry = MessageEventIds.objects.create()

        # Create the Event entry
        event = Event.objects.create(
            event_id=event_id_entry,
            tenant=instance.tenant,
            contact=instance,
            event_type=event_type,
            note=reason,
            icon=icon,
            color_background=color_background,
            color_text=color_text,
            # Created by
            created_by_type=ActorTypeChoices.USER if closed_by_user else None,
            created_by_id=closed_by_user.id if closed_by_user else None,
            created_by_user=closed_by_user,
            # Store metadata in event_data
            event_data={
                "previous_status": previous_status,
                "new_status": current_status,
                "reason": reason,
            },
        )

        logger.info(
            f"Created status change event {event.pk} for contact {instance.pk}: {previous_status} -> {current_status}"
        )

        # Broadcast via WebSocket
        _broadcast_status_change_event(instance, event)

    except Exception as e:
        logger.exception(f"Error creating status change event for contact {instance.pk}: {e}")


def _broadcast_status_change_event(contact, event):
    """Broadcast status change event via WebSocket using team_message."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if not channel_layer:
            logger.warning("No channel layer available for WebSocket broadcast")
            return

        room_group_name = f"team_inbox_{contact.tenant_id}"

        # Broadcast as new_event via team_message
        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                "type": "team_message",
                "message": {
                    "type": "new_event",
                    "contact_id": contact.id,
                    "event": {
                        "id": event.pk,
                        "event_type": event.event_type,
                        "event_type_display": event.get_event_type_display(),
                        "note": event.note,
                        "created_by_name": event.created_by_name,
                        "icon": event.icon,
                        "color_background": event.color_background,
                        "color_text": event.color_text,
                        "event_data": event.event_data,
                        "created_at": event.created_at.isoformat() if event.created_at else None,
                    },
                    # Also send updated contact status
                    "contact_status": contact.status,
                },
            },
        )
        logger.info(f"Broadcasted status change event for contact {contact.id} to room {room_group_name}")
    except Exception as e:
        logger.error(f"Failed to broadcast status change event: {e}")
