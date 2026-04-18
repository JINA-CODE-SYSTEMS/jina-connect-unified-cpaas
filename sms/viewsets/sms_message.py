from rest_framework import permissions, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from sms.models import SMSOutboundMessage
from sms.serializers import SMSOutboundMessageSerializer


class SMSSendMessageSerializer(serializers.Serializer):
    phone = serializers.CharField(help_text="Recipient phone number (E.164 format)")
    text = serializers.CharField(help_text="Message text to send")
    contact_id = serializers.IntegerField(required=False, help_text="TenantContact ID for inbox tracking")
    sender_id = serializers.CharField(required=False, help_text="Override sender ID")
    dlt_template_id = serializers.CharField(required=False, help_text="DLT template ID (India)")


class SMSOutboundMessageViewSet(BaseTenantModelViewSet):
    serializer_class = SMSOutboundMessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post"]
    required_permissions = {
        "list": "inbox.view",
        "retrieve": "inbox.view",
        "send": "inbox.reply",
        "default": "inbox.view",
    }

    def get_queryset(self):
        return SMSOutboundMessage.objects.filter(tenant__tenant_users__user=self.request.user).order_by("-created_at")

    @action(detail=False, methods=["post"], url_path="send")
    def send(self, request):
        serializer = SMSSendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        tenant_user = self._get_tenant_user()
        if not tenant_user:
            return Response(
                {"error": "Could not determine tenant for this request."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = tenant_user.tenant

        from sms.models import SMSApp
        from sms.services.message_sender import SMSMessageSender

        sms_app = SMSApp.objects.filter(tenant=tenant, is_active=True).first()
        if not sms_app:
            return Response(
                {"error": "No active SMS app configured for this tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sender = SMSMessageSender(sms_app)

        # Resolve contact for inbox tracking
        contact = None
        if data.get("contact_id"):
            from contacts.models import TenantContact

            contact = TenantContact.objects.filter(pk=data["contact_id"], tenant=tenant).first()

        result = sender.send_text(
            chat_id=data["phone"],
            text=data["text"],
            contact=contact,
            sender_id=data.get("sender_id"),
            dlt_template_id=data.get("dlt_template_id"),
        )

        resp_status = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return Response(result, status=resp_status)
