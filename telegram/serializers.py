"""
Telegram DRF serializers.
"""

from rest_framework import serializers

from telegram.models import TelegramBotApp, TelegramOutboundMessage, TelegramWebhookEvent


class TelegramBotAppSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramBotApp
        fields = [
            "id",
            "bot_username",
            "bot_user_id",
            "is_active",
            "webhook_url",
            "daily_limit",
            "messages_sent_today",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "bot_user_id",
            "webhook_url",
            "messages_sent_today",
            "created_at",
            "updated_at",
        ]


class TelegramBotAppCreateSerializer(serializers.ModelSerializer):
    """Used when registering a new bot — accepts only the token."""

    class Meta:
        model = TelegramBotApp
        fields = ["bot_token"]

    def validate_bot_token(self, value):
        """Verify the token is valid by calling getMe."""
        from telegram.services.bot_client import TelegramAPIError, TelegramBotClient

        try:
            client = TelegramBotClient(token=value)
            client.get_me()
        except TelegramAPIError:
            raise serializers.ValidationError(
                "Could not verify bot token with Telegram. Check the token and try again."
            )
        return value


class TelegramWebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramWebhookEvent
        fields = [
            "id",
            "update_id",
            "event_type",
            "is_processed",
            "retry_count",
            "error_message",
            "processed_at",
            "created_at",
        ]
        read_only_fields = fields


class TelegramOutboundMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramOutboundMessage
        fields = [
            "id",
            "chat_id",
            "message_type",
            "provider_message_id",
            "status",
            "sent_at",
            "failed_at",
            "error_message",
            "created_at",
        ]
        read_only_fields = fields
