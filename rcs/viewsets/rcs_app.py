from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied

from rcs.models import RCSApp
from rcs.serializers import RCSAppSerializer


class RCSAppViewSet(viewsets.ModelViewSet):
    serializer_class = RCSAppSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return RCSApp.objects.filter(tenant__tenant_users__user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        tenant_user = self.request.user.user_tenants.first()
        if not tenant_user:
            raise PermissionDenied("User has no associated tenant.")
        app = serializer.save(tenant=tenant_user.tenant)
        # Auto-generate webhook_url if not already set (requires pk)
        if not app.webhook_url:
            app.save()
