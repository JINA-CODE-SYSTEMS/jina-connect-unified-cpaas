"""Telegram ad-hoc message sending viewset."""

import logging

from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from telegram.services.keyboard_builder import build_template_button_keyboard

logger = logging.getLogger(__name__)


class TelegramSendMessageSerializer(serializers.Serializer):
    chat_id = serializers.CharField(required=False, help_text="Telegram chat ID of the recipient")
    text = serializers.CharField(required=False, allow_blank=True, help_text="Message text")
    media_url = serializers.URLField(required=False, help_text="URL of media to send")
    media_type = serializers.ChoiceField(
        choices=["photo", "video", "audio", "document"],
        default="photo",
        required=False,
    )
    contact_id = serializers.IntegerField(required=False, help_text="TenantContact ID for inbox tracking")
    buttons = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        help_text="Array of button objects (type, text, url, phone_number)",
    )
    # Frontend sends media via these fields
    photo = serializers.URLField(required=False, help_text="Photo URL (alternative to media_url)")
    video = serializers.URLField(required=False, help_text="Video URL (alternative to media_url)")
    document = serializers.URLField(required=False, help_text="Document URL (alternative to media_url)")

    def validate(self, attrs):
        has_text = bool(attrs.get("text"))
        has_media = bool(attrs.get("media_url") or attrs.get("photo") or attrs.get("video") or attrs.get("document"))

        if not has_text and not has_media:
            raise serializers.ValidationError(
                "Either 'text' or media (media_url/photo/video/document) must be provided."
            )
        if not attrs.get("chat_id") and not attrs.get("contact_id"):
            raise serializers.ValidationError("Either 'chat_id' or 'contact_id' must be provided.")
        return attrs


class TelegramMessageViewSet(BaseTenantModelViewSet):
    """
    Viewset for sending ad-hoc Telegram messages.

    POST /telegram/v1/messages/send/
    """

    http_method_names = ["post"]
    required_permissions = {
        "send": "inbox.reply",
        "default": "inbox.reply",
    }

    def get_queryset(self):
        from telegram.models import TelegramOutboundMessage

        return TelegramOutboundMessage.objects.none()

    @action(detail=False, methods=["post"], url_path="send")
    def send(self, request):
        serializer = TelegramSendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        tenant_user = self._get_tenant_user()
        if not tenant_user:
            return Response(
                {"error": "Could not determine tenant for this request."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = tenant_user.tenant

        from telegram.models import TelegramBotApp
        from telegram.services.message_sender import TelegramMessageSender

        bot_app = TelegramBotApp.objects.filter(tenant=tenant, is_active=True).first()
        if not bot_app:
            return Response(
                {"error": "No active Telegram bot configured for this tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sender = TelegramMessageSender(bot_app)

        # Resolve contact for inbox tracking
        contact = None
        if data.get("contact_id"):
            from contacts.models import TenantContact

            contact = TenantContact.objects.filter(pk=data["contact_id"], tenant=tenant).first()
            if not contact:
                return Response(
                    {"error": f"Contact with ID {data['contact_id']} not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Get chat_id - either from payload or from contact
        chat_id = data.get("chat_id")
        if not chat_id:
            if contact and contact.telegram_chat_id:
                chat_id = str(contact.telegram_chat_id)
            else:
                return Response(
                    {"error": "Could not determine telegram_chat_id from contact."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        text = data.get("text", "")
        media_url = data.get("media_url")
        media_type = data.get("media_type", "photo")
        buttons = data.get("buttons")

        # Handle frontend media format (photo/video/document fields)
        if not media_url:
            if data.get("photo"):
                media_url = data.get("photo")
                media_type = "photo"
            elif data.get("video"):
                media_url = data.get("video")
                media_type = "video"
            elif data.get("document"):
                media_url = data.get("document")
                media_type = "document"

        logger.debug(
            "[TelegramMessage] send contact_id=%s chat_id=%s has_text=%s has_buttons=%s media_type=%s has_media=%s",
            data.get("contact_id"),
            chat_id,
            bool(text),
            bool(buttons),
            media_type,
            bool(media_url),
        )

        reply_markup = None
        if buttons:
            reply_markup = build_template_button_keyboard(buttons)

        if media_url:
            result = sender.send_media(
                chat_id=chat_id,
                media_type=media_type,
                media_url=media_url,
                caption=text or None,
                contact=contact,
                reply_markup=reply_markup,
            )
        else:
            result = sender.send_text(
                chat_id=chat_id,
                text=text,
                contact=contact,
                reply_markup=reply_markup,
            )

        resp_status = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return Response(result, status=resp_status)
