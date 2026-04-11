from abstract.viewsets.base import BaseTenantModelViewSet
from tenants.models import TenantUser
from tenants.serializers import TenantUserSerializer


class TenantUserViewSet(BaseTenantModelViewSet):
    """
    A viewset for managing tenant users.
    """

    queryset = TenantUser.objects.all()
    serializer_class = TenantUserSerializer
    search_fields = ["user__email", "user__first_name", "user__last_name", "user__username"]
    required_permissions = {
        "list": "users.view",
        "retrieve": "users.view",
        "create": "users.invite",
        "partial_update": "users.change_role",
        "default": "users.view",
    }
    filterset_fields = {
        "tenant": ["exact", "in"],
        "created_by": ["exact", "in"],
        "updated_by": ["exact", "in"],
        "is_active": ["exact"],
    }
