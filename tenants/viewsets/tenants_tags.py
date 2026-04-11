from abstract.viewsets.base import BaseTenantModelViewSet
from tenants.models import TenantTags
from tenants.serializers import TenantTagsSerializer


class TenantTagsViewSet(BaseTenantModelViewSet):
    """
    A viewset for managing tenant tags.
    """

    queryset = TenantTags.objects.all()
    serializer_class = TenantTagsSerializer
    required_permissions = {
        "list": "tenant.view",
        "retrieve": "tenant.view",
        "create": "tenant.edit",
        "partial_update": "tenant.edit",
        "default": "tenant.view",
    }
    filterset_fields = {
        "tenant": ["exact", "in"],
        "created_by": ["exact", "in"],
        "updated_by": ["exact", "in"],
        "is_active": ["exact"],
        "name": ["exact", "in", "icontains", "istartswith"],
    }
