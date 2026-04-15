from rest_framework import permissions, viewsets

from rcs.models import RCSOutboundMessage
from rcs.serializers import RCSOutboundMessageSerializer


class RCSOutboundMessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RCSOutboundMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return RCSOutboundMessage.objects.filter(tenant__tenant_users__user=self.request.user).order_by("-created_at")
