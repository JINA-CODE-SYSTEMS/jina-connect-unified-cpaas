"""
Django signals for team inbox models.
Handles auto-broadcasting of new messages and events to WebSocket clients.
"""
import json
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.signals import post_save
from django.dispatch import receiver
from team_inbox.models import Event, Messages
from team_inbox.serializers import MessagesSerializer

logger = logging.getLogger(__name__)


def broadcast_to_tenant_team(tenant_id: int, message_type: str, data: dict):
    """
    Broadcast a message to all users connected to a tenant's team inbox.
    """
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning("[broadcast_to_tenant_team] Channel layer not available, skipping broadcast")
            return
        
        group_name = f"team_inbox_{tenant_id}"
        
        logger.info(f"[broadcast_to_tenant_team] Sending {message_type} to group {group_name}")
        
        # Serialize data to JSON and back to ensure all datetime objects are converted to strings
        # This handles datetime objects that can't be sent through channel layer
        serialized_data = json.loads(json.dumps(data, cls=DjangoJSONEncoder))
        
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "team_message",
                "message": {
                    "type": message_type,
                    **serialized_data
                }
            }
        )
        logger.info(f"[broadcast_to_tenant_team] Successfully sent {message_type} to {group_name}")
        
    except Exception as e:
        logger.error(f"[broadcast_to_tenant_team] Error broadcasting to tenant {tenant_id}: {str(e)}", exc_info=True)


@receiver(post_save, sender=Messages)
def broadcast_new_message(sender, instance: Messages, created: bool, **kwargs):
    """
    Broadcast new messages to all connected WebSocket clients for the tenant.
    This ensures webhook-created messages are also pushed to the team inbox UI.
    """
    if not created:
        return  # Only broadcast new messages, not updates
    
    try:
        logger.info(f"[broadcast_new_message] Broadcasting message {instance.id} for tenant {instance.tenant_id}")
        
        # Serialize the message
        serializer = MessagesSerializer(instance)
        serializer_data = serializer.data
        
        logger.info(f"[broadcast_new_message] Serialized message {instance.id} successfully")
        
        broadcast_to_tenant_team(
            tenant_id=instance.tenant_id,
            message_type="new_message",
            data={
                "message": serializer_data,
                "contact_id": instance.contact_id,
                "numbering": instance.message_id.numbering if instance.message_id else None,
                "timestamp": instance.timestamp.isoformat() if instance.timestamp else None,
            }
        )
        
        logger.info(f"[broadcast_new_message] Broadcast complete for message {instance.id}")
        
    except Exception as e:
        logger.error(f"[broadcast_new_message] Error broadcasting message {instance.id}: {str(e)}", exc_info=True)


@receiver(post_save, sender=Event)
def broadcast_new_event(sender, instance: Event, created: bool, **kwargs):
    """
    Broadcast new events to all connected WebSocket clients for the tenant.
    """
    if not created:
        return  # Only broadcast new events, not updates
    
    try:
        from team_inbox.serializers import EventSerializer
        event_data = EventSerializer(instance).data
        broadcast_to_tenant_team(
            tenant_id=instance.tenant_id,
            message_type="new_event",
            data={
                "event": event_data,
                "contact_id": instance.contact_id,
                "numbering": instance.event_id.numbering if instance.event_id else None,
                "timestamp": instance.created_at.isoformat() if instance.created_at else None,
            }
        )
        
    except Exception as e:
        logger.error(f"Error broadcasting new event {instance.id}: {str(e)}")
