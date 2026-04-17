from rest_framework.exceptions import ValidationError
from rest_framework.mixins import CreateModelMixin

from broadcast.models import BroadcastPlatformChoices
from broadcast.viewsets.broadcast import BroadcastViewSet


class RCSBroadcastViewSet(BroadcastViewSet):
    """RCS-scoped broadcast API."""

    http_method_names = ["get", "post", "patch"]
    required_permissions = {
        "list": "broadcast.view",
        "retrieve": "broadcast.view",
        "create": "broadcast.create",
        "partial_update": "broadcast.create",
        "reserve_keyword_list": "broadcast.view",
        "min_scheduled_time": "broadcast.view",
        "default": "broadcast.view",
    }

    def get_queryset(self):
        return super().get_queryset().filter(platform=BroadcastPlatformChoices.RCS)

    def create(self, request, *args, **kwargs):
        return CreateModelMixin.create(self, request, *args, **kwargs)

    def perform_create(self, serializer):
        tenant_user = self._get_tenant_user()
        if not tenant_user:
            raise ValidationError("Could not determine tenant.")
        serializer.save(
            tenant=tenant_user.tenant,
            created_by=self.request.user,
            platform=BroadcastPlatformChoices.RCS,
        )
