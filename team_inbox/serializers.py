"""
Serializers for team inbox models
"""

from rest_framework import serializers

from team_inbox.models import Event, MessagePlatformChoices, Messages


class MessagesSerializer(serializers.ModelSerializer):
    """
    Serializer for Messages model
    """

    expires_at = serializers.ReadOnlyField()  # Property field from model
    outgoing_status = serializers.ReadOnlyField()  # Property field from model
    outgoing_sent_at = serializers.ReadOnlyField()  # Property field from model
    outgoing_delivered_at = serializers.ReadOnlyField()  # Property field from model
    outgoing_read_at = serializers.ReadOnlyField()  # Property field from model
    outgoing_failed_at = serializers.ReadOnlyField()  # Property field from model
    read_by_name = serializers.SerializerMethodField()
    read_by_id = serializers.IntegerField(source="read_by.id", read_only=True, allow_null=True)
    platform_display = serializers.SerializerMethodField()
    channel_app_id = serializers.SerializerMethodField()

    class Meta:
        model = Messages
        fields = [
            "id",
            "message_id",
            "content",
            "timestamp",
            "direction",
            "platform",
            "platform_display",
            "channel_app_id",
            "author",
            "tenant",
            "created_at",
            "updated_at",
            "expires_at",
            "is_read",
            "read_at",
            "read_by_id",
            "read_by_name",
            "outgoing_status",
            "outgoing_sent_at",
            "outgoing_delivered_at",
            "outgoing_read_at",
            "outgoing_failed_at",
        ]
        read_only_fields = [
            "id",
            "timestamp",
            "created_at",
            "updated_at",
            "expires_at",
            "is_read",
            "read_at",
            "read_by_id",
            "read_by_name",
            "platform_display",
            "channel_app_id",
            "outgoing_status",
            "outgoing_sent_at",
            "outgoing_delivered_at",
            "outgoing_read_at",
            "outgoing_failed_at",
        ]

    def get_platform_display(self, obj):
        """Human-readable platform name (#116)."""
        return obj.get_platform_display() if obj.platform else None

    def get_channel_app_id(self, obj):
        """Resolve channel app ID from linked outbound messages (#116)."""
        if obj.platform == "WHATSAPP" and obj.outgoing_message_id:
            return str(getattr(obj.outgoing_message, "wa_app_id", "") or "")
        # Check channel-specific outbound FK
        for attr, field in [
            ("telegram_outbound", None),
            ("sms_outbound_messages", "sms_app_id"),
            ("rcs_outbound_messages", "rcs_app_id"),
        ]:
            qs = getattr(obj, attr, None)
            if qs is not None:
                linked = qs.first()
                if linked:
                    if attr == "telegram_outbound":
                        return str(getattr(linked, "bot_app_id", "") or "")
                    return str(getattr(linked, field, "") or "")
        return None

    def get_read_by_name(self, obj):
        """Get the name of the user who read the message."""
        if obj.read_by:
            full_name = obj.read_by.get_full_name()
            if full_name:
                return full_name
            return obj.read_by.email or obj.read_by.username or "Unknown"
        return None

    def validate_content(self, value):
        """
        Validate message content structure
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("Content must be a JSON object")

        # Basic content validation
        if "text" not in value and "media" not in value:
            raise serializers.ValidationError("Content must contain either 'text' or 'media'")

        return value

    def validate_platform(self, value):
        """
        Validate platform choice
        """
        if value not in MessagePlatformChoices.values:
            raise serializers.ValidationError(f"Invalid platform. Choose from: {MessagePlatformChoices.values}")
        return value


class EventSerializer(serializers.ModelSerializer):
    """
    Serializer for Event model.
    Provides a single, consistent representation used by both
    the WebSocket consumer timeline and the post_save signal broadcast.
    """

    event_type_display = serializers.CharField(
        source="get_event_type_display",
        read_only=True,
    )
    created_by_name = serializers.ReadOnlyField()
    assigned_by_name = serializers.ReadOnlyField()
    assigned_to_name = serializers.ReadOnlyField()

    class Meta:
        model = Event
        fields = [
            "id",
            "event_type",
            "event_type_display",
            "note",
            "created_by_name",
            "assigned_by_name",
            "assigned_to_name",
            "icon",
            "color_background",
            "color_text",
            "event_data",
            "created_at",
            "timestamp",
        ]
        read_only_fields = fields


class MessageCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating new messages via WebSocket or API
    """

    class Meta:
        model = Messages
        fields = ["message_id", "content", "direction", "platform", "author"]

    def create(self, validated_data):
        """
        Create a new message with tenant context
        """
        # Tenant will be set by the view/consumer
        return super().create(validated_data)


class MessageListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for listing messages
    """

    content_preview = serializers.SerializerMethodField()

    class Meta:
        model = Messages
        fields = ["id", "message_id", "content_preview", "timestamp", "direction", "platform", "author"]

    def get_content_preview(self, obj):
        """
        Get a preview of message content
        """
        if not obj.content:
            return ""

        # If content has text, return first 100 characters
        if isinstance(obj.content, dict):
            text = obj.content.get("text", "")
            if text:
                return text[:100] + ("..." if len(text) > 100 else "")

            # If no text, check for media type
            media = obj.content.get("media", {})
            if media:
                media_type = media.get("type", "media")
                return f"[{media_type.upper()}]"

        return str(obj.content)[:100]


class WebSocketMessageSerializer(serializers.Serializer):
    """
    Serializer for WebSocket message validation
    """

    type = serializers.CharField(max_length=50)
    content = serializers.JSONField(required=False)
    platform = serializers.ChoiceField(
        choices=MessagePlatformChoices.choices, required=False, default=MessagePlatformChoices.WHATSAPP
    )
    recipient_id = serializers.CharField(max_length=255, required=False)
    message_ids = serializers.ListField(child=serializers.CharField(max_length=255), required=False)
    is_typing = serializers.BooleanField(required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=50)
    offset = serializers.IntegerField(min_value=0, required=False, default=0)
    client_type = serializers.ChoiceField(choices=["web", "mobile"], required=False, default="web")


class TypingIndicatorSerializer(serializers.Serializer):
    """
    Serializer for typing indicator data
    """

    user_id = serializers.IntegerField()
    username = serializers.CharField(max_length=255)
    is_typing = serializers.BooleanField()
    timestamp = serializers.DateTimeField()


class MessageReadStatusSerializer(serializers.Serializer):
    """
    Serializer for message read status
    """

    message_ids = serializers.ListField(child=serializers.CharField())
    user_id = serializers.IntegerField()
    timestamp = serializers.DateTimeField()
