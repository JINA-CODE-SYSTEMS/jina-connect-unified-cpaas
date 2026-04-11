"""
WAWebhookEvent Serializers (v2)

Serializers for WhatsApp Webhook Events.
"""

from drf_yasg import openapi
from rest_framework import serializers

from abstract.serializers import BaseSerializer
from wa.models import WAWebhookEvent


class WAWebhookEventListSerializer(BaseSerializer):
    """
    Minimal serializer for webhook event list views.

    Used for efficient list endpoints with only essential fields.
    """

    class Meta:
        model = WAWebhookEvent
        fields = [
            "id",
            "event_type",
            "bsp",
            "is_processed",
            "created_at",
        ]
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WAWebhookEventList",
            "description": "Minimal Webhook Event for list views",
        }


class WAWebhookEventSerializer(BaseSerializer):
    """
    Full serializer for Webhook Events.

    Read-only serializer for viewing webhook event data including:
    - Event type and BSP source
    - Raw payload data
    - Processing status and errors

    This is primarily a read-only serializer as webhook events
    are created by incoming webhooks, not via the API.
    """

    event_type_display = serializers.CharField(
        source="get_event_type_display", read_only=True, help_text="Human-readable event type"
    )
    bsp_display = serializers.CharField(source="get_bsp_display", read_only=True, help_text="Human-readable BSP name")

    class Meta:
        model = WAWebhookEvent
        fields = [
            "id",
            "wa_app",
            "event_type",
            "event_type_display",
            "bsp",
            "bsp_display",
            "payload",
            "is_processed",
            "processed_at",
            "error_message",
            "retry_count",
            "message",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "wa_app",
            "event_type",
            "bsp",
            "payload",
            "is_processed",
            "processed_at",
            "error_message",
            "retry_count",
            "message",
            "created_at",
        ]
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WAWebhookEvent",
            "description": "WhatsApp Webhook Event (v2)",
        }
