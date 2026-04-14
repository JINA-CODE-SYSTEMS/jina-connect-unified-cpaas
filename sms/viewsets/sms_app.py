from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied

from sms.models import SMSApp
from sms.serializers import SMSAppSerializer


class SMSAppViewSet(viewsets.ModelViewSet):
    serializer_class = SMSAppSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SMSApp.objects.filter(tenant__tenant_users__user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        tenant_user = self.request.user.tenant_users.first()
        if not tenant_user:
            raise PermissionDenied("User has no associated tenant.")
        serializer.save(tenant=tenant_user.tenant)
