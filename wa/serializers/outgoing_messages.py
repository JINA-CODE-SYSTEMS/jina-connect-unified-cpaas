"""
Outgoing Messages Serializer

Serializer for WhatsApp Messages (uses canonical WAMessage model).
"""

from abstract.serializers import BaseSerializer
from wa.models import WAMessage


class OutgoingMessagesSerializer(BaseSerializer):
    """
    Serializer for WhatsApp Messages.

    Uses the canonical WAMessage model for tracking
    messages and their delivery status.
    """

    class Meta:
        model = WAMessage
        fields = [
            "id",
            "wa_app",
            "contact",
            "wa_message_id",
            "direction",
            "message_type",
            "status",
            "text",
            "template",
            "template_params",
            "media_url",
            "media_caption",
            "error_code",
            "error_message",
            "sent_at",
            "delivered_at",
            "read_at",
            "failed_at",
            "raw_payload",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "wa_message_id",
            "sent_at",
            "delivered_at",
            "read_at",
            "failed_at",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        ]
