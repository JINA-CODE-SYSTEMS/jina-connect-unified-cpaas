from rest_framework import permissions, viewsets

from sms.models import SMSOutboundMessage
from sms.serializers import SMSOutboundMessageSerializer


class SMSOutboundMessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SMSOutboundMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SMSOutboundMessage.objects.filter(tenant__tenant_users__user=self.request.user).order_by("-created_at")
