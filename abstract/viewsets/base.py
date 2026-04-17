from rest_framework import viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from abstract.backends import DateTimeAwareFilterBackend
from abstract.pagination_class import BasePaginationClass
from abstract.serializers import BaseSerializer
from tenants.permission_classes import TenantRolePermission

# Slugs whose querysets are narrowed to only their assigned records.
# Per ticket #250: VIEWER sees all records (read-only enforced by permissions,
# not queryset), so only AGENT is scoped.
_SCOPED_ROLE_SLUGS = frozenset({"agent"})


class BaseModelViewSet(viewsets.ModelViewSet):
    """
    A base viewset that provides default `list()`, `create()`, and `partial_update()` actions.
    This viewset uses a custom pagination class and sets default ordering and HTTP method names.
    It also specifies a default serializer class.
    """

    pagination_class = BasePaginationClass
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    filter_backends = [DateTimeAwareFilterBackend, SearchFilter, OrderingFilter]
    ordering = ["-id"]
    http_method_names = ["get", "post", "patch"]
    serializer_class = BaseSerializer


class BaseTenantModelViewSet(BaseModelViewSet):
    """
    A base viewset that extends BaseModelViewSet to include tenant-specific functionality.
    This viewset overrides the `get_queryset` method to filter the queryset based on the tenant
    associated with the request user.

    Subclasses may override ``get_role_scoped_queryset()`` to apply row-level
    filtering for agents (e.g. agents see only assigned records).
    """

    permission_classes = [IsAuthenticated, TenantRolePermission]

    # ── helpers ────────────────────────────────────────────────────────

    def _get_tenant_user(self):
        """
        Return the request user's active TenantUser (cached per-request).

        Uses the ``tenant_id`` JWT claim when available so the lookup is
        precise for users that belong to more than one tenant.
        """
        cache_attr = "_cached_tenant_user"
        if not hasattr(self.request, cache_attr):
            from tenants.models import TenantUser

            filters = {"user": self.request.user, "is_active": True}
            tenant_id = getattr(self.request.user, "tenant_id", None)
            if tenant_id:
                filters["tenant_id"] = tenant_id
            setattr(
                self.request,
                cache_attr,
                TenantUser.objects.select_related("role").filter(**filters).first(),
            )
        return getattr(self.request, cache_attr)

    # ── queryset pipeline ─────────────────────────────────────────────

    def get_queryset(self):
        """
        1. Tenant-scoped queryset (existing behaviour).
        2. If the user's role priority is at or below the agent threshold,
           delegate to ``get_role_scoped_queryset()`` for row-level filtering.
        """
        user = self.request.user
        if user.is_superuser:
            return self.queryset.all()
        # check if the model's manager has the filter_by_user_tenant method
        if not hasattr(self.queryset.model.objects, "filter_by_user_tenant"):
            raise NotImplementedError("The model's manager must implement the 'filter_by_user_tenant' method.")
        qs = self.queryset.model.objects.filter_by_user_tenant(user)

        # Role-scoped filtering — only for AGENT role
        tenant_user = self._get_tenant_user()
        if tenant_user and tenant_user.role and tenant_user.role.slug in _SCOPED_ROLE_SLUGS:
            qs = self.get_role_scoped_queryset(qs, user, tenant_user)
        return qs

    def get_role_scoped_queryset(self, queryset, user, tenant_user):
        """
        Hook for subclasses to narrow the queryset for agents.

        Called **only** when the requesting user's role slug is in
        ``_SCOPED_ROLE_SLUGS`` (currently just ``agent``).
        The default implementation returns the queryset unchanged.

        Override in concrete viewsets to filter by ``assigned_to_user``,
        ``created_by``, etc.
        """
        return queryset
