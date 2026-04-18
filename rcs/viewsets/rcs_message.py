import logging

from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from rcs.models import RCSOutboundMessage
from rcs.serializers import RCSOutboundMessageSerializer

logger = logging.getLogger(__name__)


class RCSSendMessageSerializer(serializers.Serializer):
    chat_id = serializers.RegexField(
        r"^\+[1-9]\d{1,14}$",
        help_text="E.164 phone number of the recipient (e.g. +14155550100)",
    )
    text = serializers.CharField(required=False, allow_blank=True, help_text="Message text")
    media_url = serializers.URLField(required=False, help_text="URL of media to send")
    media_type = serializers.ChoiceField(
        choices=["image", "video", "audio", "document"],
        default="image",
        required=False,
    )
    contact_id = serializers.IntegerField(required=False, help_text="TenantContact ID for inbox tracking")

    def validate(self, attrs):
        if not attrs.get("text") and not attrs.get("media_url"):
            raise serializers.ValidationError("Either 'text' or 'media_url' must be provided.")
        return attrs


class RCSOutboundMessageViewSet(BaseTenantModelViewSet):
    serializer_class = RCSOutboundMessageSerializer
    http_method_names = ["get", "post"]
    required_permissions = {
        "list": "inbox.view",
        "retrieve": "inbox.view",
        "revoke": "inbox.reply",
        "send": "inbox.reply",
        "default": "inbox.view",
    }

    def get_queryset(self):
        tenant_user = self._get_tenant_user()
        if tenant_user:
            return RCSOutboundMessage.objects.filter(tenant=tenant_user.tenant).order_by("-created_at")
        return RCSOutboundMessage.objects.none()

    def create(self, request, *args, **kwargs):
        """Disabled — use the /send/ action instead."""
        return Response(
            {"detail": "Use the /send/ action to send RCS messages."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        """Revoke a sent RCS message via the provider API (#112).

        Only messages in SENT or DELIVERED status can be revoked.
        """
        from rcs.providers import get_rcs_provider

        msg = self.get_object()

        if msg.status not in ("SENT", "DELIVERED"):
            return Response(
                {"detail": f"Cannot revoke message in status {msg.status}."},
                status=400,
            )

        try:
            provider = get_rcs_provider(msg.rcs_app)
            provider.revoke_message(msg.provider_message_id)
            msg.status = "REVOKED"
            msg.save(update_fields=["status"])
            logger.info("[RCS revoke] Message %s revoked successfully", msg.pk)
            return Response({"detail": "Message revoked.", "status": msg.status})
        except Exception:
            logger.exception("[RCS revoke] Failed to revoke message %s", msg.pk)
            return Response({"detail": "Revocation failed. Please try again later."}, status=502)

    @action(detail=False, methods=["post"], url_path="send")
    def send(self, request):
        """Send an ad-hoc RCS message (#129)."""
        serializer = RCSSendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        tenant_user = self._get_tenant_user()
        if not tenant_user:
            return Response(
                {"error": "Could not determine tenant for this request."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = tenant_user.tenant

        from rcs.models import RCSApp
        from rcs.services.message_sender import RCSMessageSender

        rcs_app = RCSApp.objects.filter(tenant=tenant, is_active=True).first()
        if not rcs_app:
            return Response(
                {"error": "No active RCS app configured for this tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sender = RCSMessageSender(rcs_app)

        # Resolve contact for inbox tracking
        contact = None
        if data.get("contact_id"):
            from contacts.models import TenantContact

            contact = TenantContact.objects.filter(pk=data["contact_id"], tenant=tenant).first()
            if contact is None:
                return Response(
                    {"error": f"TenantContact {data['contact_id']} not found for this tenant."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        chat_id = data["chat_id"]
        text = data.get("text", "")
        media_url = data.get("media_url")
        media_type = data.get("media_type", "image")

        if media_url:
            result = sender.send_media(
                chat_id=chat_id,
                media_type=media_type,
                media_url=media_url,
                caption=text or None,
                contact=contact,
            )
        else:
            result = sender.send_text(
                chat_id=chat_id,
                text=text,
                contact=contact,
            )

        resp_status = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return Response(result, status=resp_status)
