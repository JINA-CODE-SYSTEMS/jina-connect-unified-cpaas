"""
Subscription Serializer

Serializer for WhatsApp Webhook Subscriptions (WASubscription).
"""

from rest_framework import serializers

from abstract.serializers import BaseSerializer
from wa.models import WASubscription


class SubscriptionSerializer(BaseSerializer):
    """
    Serializer for WASubscription - webhook subscription configuration.
    """

    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = WASubscription
        fields = [
            "id",
            "wa_app",
            "webhook_url",
            "event_types",
            "status",
            "status_display",
            "bsp_subscription_id",
            "verify_token",
            "error_message",
            "last_event_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "bsp_subscription_id", "error_message", "last_event_at", "created_at", "updated_at"]
