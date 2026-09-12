from rest_framework import viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from abstract.backends import DateTimeAwareFilterBackend
from abstract.pagination_class import BasePaginationClass
from abstract.serializers import BaseSerializer
from abstract.tenant_scoping import tenant_filter_path
from tenants.permission_classes import TenantRolePermission
from users.impersonation import impersonated_tenant_id

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

    def scope_to_impersonated_tenant(self, queryset):
        """Narrow ``queryset`` to the one organisation an impersonated session
        may read, or return it untouched when this is an ordinary request (#326).

        An impersonation token keeps ``is_superuser`` true — it has to, since the
        actor holds no role in the organisation being viewed and
        ``TenantRolePermission`` would otherwise 403 the very reads the feature
        exists for. Before this, that flag also took the session straight to the
        unscoped ``.all()`` below, so a session reading under a banner naming one
        organisation was in fact listing every organisation's rows. Not an
        escalation — the actor's own ordinary token already reads everything —
        but support staff can answer with the wrong customer's figures, or call a
        record missing when it belongs to someone else.

        Call this from any ``get_queryset`` override that builds its own
        queryset instead of delegating here, or the override reopens the hole
        for its own model.

        Raises ``ImproperlyConfigured`` for an impersonated request against a
        model with no declared tenant path. Deliberately: the alternative is
        serving every organisation, and a new model that forgets its
        declaration should break in review, not leak in support.
        """
        tenant_id = impersonated_tenant_id(self.request)
        if tenant_id is None:
            return queryset
        return queryset.filter(**{tenant_filter_path(queryset.model): tenant_id})

    # ── queryset pipeline ─────────────────────────────────────────────

    def get_queryset(self):
        """
        1. An impersonated session reads exactly one organisation (#326).
        2. Tenant-scoped queryset (existing behaviour).
        3. If the user's role priority is at or below the agent threshold,
           delegate to ``get_role_scoped_queryset()`` for row-level filtering.
        """
        user = self.request.user

        # Checked ahead of everything else because neither branch below can
        # serve an impersonated session correctly: membership filtering finds
        # nothing (the actor belongs to nothing in the organisation being
        # viewed) and the superuser branch hands over every organisation.
        # Role scoping is skipped on purpose — the session is meant to show what
        # that organisation's owner sees, and an owner is not row-scoped.
        if impersonated_tenant_id(self.request) is not None:
            return self.scope_to_impersonated_tenant(self.queryset.all())

        if user.is_superuser:
            # Unchanged for an ordinary, non-impersonated superuser: still every
            # row. Narrowing that is a separate decision from #326.
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
