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
        tenant_user = self.request.user.user_tenants.first()
        if not tenant_user:
            raise PermissionDenied("User has no associated tenant.")
        app = serializer.save(tenant=tenant_user.tenant)
        # Auto-generate webhook URLs if not already set
        if not app.webhook_url or not app.dlr_webhook_url:
            app.save()
