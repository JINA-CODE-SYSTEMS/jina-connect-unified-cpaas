from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated

from abstract.backends import DateTimeAwareFilterBackend
from abstract.pagination_class import BasePaginationClass
from abstract.serializers import BaseSerializer
from abstract.tenant_scoping import tenant_filter_path, tenant_write_field
from tenants.permission_classes import TenantRolePermission
from users.impersonation import impersonated_tenant_id

# Slugs whose querysets are narrowed to only their assigned records.
# Per ticket #250: VIEWER sees all records (read-only enforced by permissions,
# not queryset), so only AGENT is scoped.
_SCOPED_ROLE_SLUGS = frozenset({"agent"})

# What a caller is told when the body names an organisation they may not write
# into. Deliberately the same message whether the organisation exists or not:
# distinguishing them would turn this endpoint into a way to enumerate other
# customers, which is the shape of #301.
FOREIGN_TENANT_WRITE_MESSAGE = (
    "The organisation named in this request is not one you may write into. "
    "Omit 'tenant' and it is taken from your own membership."
)


class BaseModelViewSet(viewsets.ModelViewSet):
    """
    A base viewset that provides default `list()`, `create()`, and `partial_update()` actions.
    This viewset uses a custom pagination class and sets default ordering and HTTP method names.
    It also specifies a default serializer class.

    It also decides, for every write it serves, which organisation the row may
    land in — see ``scope_write_to_permitted_tenant`` (#346). That lives here
    rather than on ``BaseTenantModelViewSet`` for one reason: ``RazorPayViewSet``
    is a direct subclass of *this* class, scopes its reads by hand, and had the
    same writable ``tenant``. A control one class lower would have missed it.
    """

    pagination_class = BasePaginationClass
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    filter_backends = [DateTimeAwareFilterBackend, SearchFilter, OrderingFilter]
    ordering = ["-id"]
    http_method_names = ["get", "post", "patch"]
    serializer_class = BaseSerializer

    # ── who is asking ──────────────────────────────────────────────────

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

    # ── write scoping (#346) ───────────────────────────────────────────

    def permitted_write_tenant_ids(self):
        """The organisation ids this request may create or move a row into.

        A ``frozenset`` constrains the request to those ids — an **empty** one
        means "no organisation", which refuses every submitted ``tenant`` rather
        than waving it through. ``None`` means unconstrained, and is returned in
        exactly two cases, both of them deliberate:

        * **The caller holds no tenant membership but is a superuser.** This is
          the platform-operator path (#345): creating an app for an organisation
          they do not belong to is a real workflow, and it needs the body to be
          able to name that organisation. Narrowing it is a separate decision,
          the same one ``get_queryset`` leaves open for an ordinary superuser.
        * **There is no authenticated user at all.** A few actions on tenant
          viewsets are deliberately unauthenticated webhook receivers (Gupshup
          delivery and billing callbacks), and ``TenantAccessKeyAuthentication``
          leaves ``request.user`` as ``None`` outright. There is no membership to
          compare a body against, and those handlers resolve the organisation
          from the payload themselves.

          (Named by description rather than by the permission class, because
          ``test_allowany_only_on_intended_endpoints`` greps source text for that
          class name and this file is not an endpoint. Widening its allow-list to
          admit a comment would also admit a real one here later.)

        Impersonation is read first and answers on its own. #300 refuses every
        non-safe method from a borrowed token at two independent layers, so this
        branch cannot be reached today — it is written anyway, and pinned by a
        test, because the alternative if that refusal is ever relaxed is the
        branch below: an impersonation token keeps ``is_superuser`` true and
        holds no membership, so it would fall straight into the unconstrained
        platform-operator case and be able to write into *any* organisation
        rather than the one its banner names. Deriving from the signed
        ``tenant_id`` claim instead keeps the write where #344 already confines
        the reads.
        """
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None

        impersonated = impersonated_tenant_id(request)
        if impersonated is not None:
            return frozenset({impersonated})

        from tenants.models import TenantUser

        memberships = frozenset(
            TenantUser.objects.filter(user=user, is_active=True).values_list("tenant_id", flat=True)
        )

        # Tested before the claim is applied, not after: "holds no membership
        # anywhere" is the platform operator, and that is the only caller the
        # escape is for. Testing afterwards would also let a superuser who *is* a
        # member of one organisation go unconstrained merely by carrying a token
        # naming a different one.
        if not memberships:
            return None if getattr(user, "is_superuser", False) else frozenset()

        # A token naming one organisation may write into that one only, even for
        # a user who belongs to several — the same narrowing ``_get_tenant_user``
        # applies to reads. Intersecting rather than trusting the claim matters:
        # a claim naming an organisation the user has since left yields the empty
        # set, which refuses, where trusting it would grant.
        claim = getattr(user, "tenant_id", None)
        if claim is not None:
            return memberships & frozenset({claim})
        return memberships

    def tenant_write_field_name(self, serializer):
        """The serializer field a request body could use to choose an organisation.

        None — the overwhelmingly common case — when there is nothing to police:
        the model has no tenant column of its own (eleven of the models behind
        these viewsets reach their tenant through a parent), or the serializer
        does not expose the column, or exposes it read-only. Each of those
        already means the body cannot decide where the row lands, so there is
        nothing here to force and nothing to refuse.
        """
        model = getattr(getattr(serializer, "Meta", None), "model", None)
        if model is None:
            queryset = getattr(self, "queryset", None)
            model = queryset.model if queryset is not None else None
        if model is None:
            return None

        name = tenant_write_field(model)
        if name is None:
            return None

        field = serializer.fields.get(name)
        if field is None or field.read_only:
            return None
        return name

    def scope_write_to_permitted_tenant(self, serializer):
        """Settle which organisation this write names, before it is validated (#346).

        Three outcomes, and the middle one is the point of the ticket:

        * **The body names an organisation the caller may write into** — left
          exactly as sent. Agreement is not an error, and a multi-tenant user
          naming which of their own organisations they mean is the reason the
          field is writable at all.
        * **The body names any other organisation** — ``PermissionDenied``.
          Quietly substituting the caller's own organisation would be the easier
          fix and the wrong one twice over: it turns a client's mistake into a
          row that silently appeared somewhere else, and it makes an attempt to
          plant a row in somebody else's organisation look like an ordinary
          success in every log and response.
        * **The body omits it** — filled in from the caller's own membership, so
          the ordinary client need not send it and cannot get it wrong.

        Why here, in ``get_serializer``, rather than in ``perform_create``: of
        the thirty-nine viewsets that inherit this, thirteen override
        ``create()`` and two of those (``WATemplateV2ViewSet``,
        ``WAMessageViewSet``) call ``serializer.save()`` directly and never reach
        ``perform_create`` at all. A control in ``perform_create`` would have
        been silently absent from exactly the viewsets that had already departed
        from the default — which is the failure mode #346 is a case of. Every
        write path in the project, overridden or not, goes through
        ``get_serializer(data=...)``, so this is the one place that cannot be
        stepped around by a subclass not thinking about tenants.

        Filling the value into ``initial_data`` rather than forcing it as a
        ``save()`` kwarg is what lets an omitted ``tenant`` work at all: on
        twelve of these serializers the field is ``required=True``, so
        ``is_valid()`` would 400 long before any ``save()`` kwarg could help.
        It also means the derived value is validated like any other.
        """
        # ``drf_yasg`` instantiates viewsets with no request to introspect their
        # serializers. It does not pass ``data``, so this should not be reachable
        # from there — guarded anyway, because a schema build that 500s takes the
        # whole API documentation down and this control has nothing to say about
        # a request that does not exist.
        request = getattr(self, "request", None)
        if request is None or request.method in SAFE_METHODS:
            return

        permitted = self.permitted_write_tenant_ids()
        if permitted is None:
            return

        name = self.tenant_write_field_name(serializer)
        if name is None:
            return

        data = getattr(serializer, "initial_data", None)
        # ``many=True`` hands us a list, and a handful of actions post something
        # that is not an object at all. Neither can name a tenant through this
        # field, so there is nothing to settle.
        if not hasattr(data, "get"):
            return

        submitted = data.get(name, None)
        if submitted not in (None, ""):
            try:
                submitted_id = int(submitted)
            except (TypeError, ValueError):
                # Not an id at all. The field's own validation says so far
                # better than this can, and refusing here would answer a
                # malformed request with the wrong error.
                return
            if submitted_id not in permitted:
                raise PermissionDenied(FOREIGN_TENANT_WRITE_MESSAGE)
            return

        # Absent. Derive it — but only when creating. On an update the row
        # already has an organisation, and deriving one would *move* it: a user
        # who belongs to both A and B has an arbitrary one of the two resolved
        # here, so a PATCH of one of their rows in B would quietly relocate it
        # to A. An omitted tenant on an update means "leave it alone".
        if serializer.instance is not None or request.method != "POST":
            return

        tenant_user = self._get_tenant_user()
        if tenant_user is None:
            return

        new_data = data.copy()
        new_data[name] = tenant_user.tenant_id
        serializer.initial_data = new_data

    def get_serializer(self, *args, **kwargs):
        """Build the serializer, then settle the organisation any write names.

        Gated on ``data`` because that is what separates an input serializer
        from the one rendering a response: a read has no body to police, and
        ``list`` builds one of these per row.
        """
        serializer = super().get_serializer(*args, **kwargs)
        if "data" in kwargs:
            self.scope_write_to_permitted_tenant(serializer)
        return serializer


class BaseTenantModelViewSet(BaseModelViewSet):
    """
    A base viewset that extends BaseModelViewSet to include tenant-specific functionality.
    This viewset overrides the `get_queryset` method to filter the queryset based on the tenant
    associated with the request user.

    **That is read scoping, and it was once all there was.** #346: a writable
    ``tenant`` on a create serializer sailed past it, because a queryset filter
    says nothing about where a new row may land — an owner of one organisation
    posted ``tenant: <another>`` to ``POST /wa/v2/apps/`` and got 201, with the
    row in the other organisation and invisible to them afterwards because reads
    *were* scoped. The write side now lives on ``BaseModelViewSet`` (see
    ``scope_write_to_permitted_tenant``) so it is the default for both classes
    rather than something each viewset has to remember.

    Subclasses may override ``get_role_scoped_queryset()`` to apply row-level
    filtering for agents (e.g. agents see only assigned records).
    """

    permission_classes = [IsAuthenticated, TenantRolePermission]

    # ── helpers ────────────────────────────────────────────────────────
    # ``_get_tenant_user`` moved up to ``BaseModelViewSet`` with #346: the write
    # scoping there needs the same answer, and ``RazorPayViewSet`` subclasses
    # that class directly.

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
