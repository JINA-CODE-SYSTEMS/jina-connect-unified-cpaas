"""
WASubscription Serializers (v2)

Serializers for WhatsApp Webhook Subscriptions.
"""

from rest_framework import serializers
from drf_yasg import openapi

from abstract.serializers import BaseSerializer
from wa.models import WASubscription


class WASubscriptionV2ListSerializer(BaseSerializer):
    """
    Minimal serializer for subscription list views.
    
    Used for efficient list endpoints with only essential fields.
    """
    
    class Meta:
        model = WASubscription
        fields = [
            'id',
            'webhook_url',
            'status',
            'is_active',
        ]
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WASubscriptionList",
            "description": "Minimal Webhook Subscription for list views",
        }


class WASubscriptionV2Serializer(BaseSerializer):
    """
    Full serializer for Webhook Subscriptions (v2).
    
    Handles subscription management including:
    - Webhook URL configuration
    - Event type filtering
    - Activation status
    - Verify token (write-only for security)
    """
    
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True,
        help_text="Human-readable subscription status"
    )
    wa_app_name = serializers.CharField(
        source='wa_app.name',
        read_only=True,
        help_text="Name of the associated WA App"
    )
    
    class Meta:
        model = WASubscription
        fields = [
            'id',
            'wa_app',
            'wa_app_name',
            'name',
            'webhook_url',
            'event_types',
            'status',
            'status_display',
            'bsp_subscription_id',
            'verify_token',
            'error_message',
            'last_event_at',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'bsp_subscription_id',
            'error_message',
            'last_event_at',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'verify_token': {
                'write_only': True,
                'help_text': 'Token for webhook verification (write-only, not returned in responses)'
            },
            'webhook_url': {
                'help_text': 'URL to receive webhook events (must be HTTPS)'
            },
            'event_types': {
                'help_text': 'List of event types to subscribe to (e.g., ["message", "status"])'
            },
        }
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WASubscription",
            "description": "Webhook Subscription (v2)",
        }
    
    def validate_webhook_url(self, value):
        """
        Validate webhook URL is HTTPS.
        """
        if not value:
            raise serializers.ValidationError("Webhook URL is required")
        
        if not value.startswith('https://'):
            raise serializers.ValidationError(
                "Webhook URL must use HTTPS for security"
            )
        
        return value
    
    def validate_event_types(self, value):
        """
        Validate event types are valid.
        """
        valid_types = [
            'message',
            'message_status',
            'message_reaction',
            'template_status',
            'account_update',
        ]
        
        if not value:
            return value
        
        invalid = [et for et in value if et not in valid_types]
        if invalid:
            raise serializers.ValidationError(
                f"Invalid event types: {', '.join(invalid)}. "
                f"Valid types: {', '.join(valid_types)}"
            )
        
        return value
