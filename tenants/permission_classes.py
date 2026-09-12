"""
DRF Permission Classes for RBAC.

Provides ``TenantRolePermission`` — the main class that maps a viewset's
``required_permissions`` dict to the requesting user's ``TenantRole`` — and
four priority-based shortcut classes.

Usage on a viewset::

    class BroadcastViewSet(BaseTenantModelViewSet):
        permission_classes = [IsAuthenticated, TenantRolePermission]
        required_permissions = {
            "list":     "broadcast.view",
            "create":   "broadcast.create",
            "destroy":  "broadcast.cancel",
            "default":  "broadcast.view",
        }

Reference: docs/PRD_RBAC.md — Section 4.2
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from tenants.permissions import has_permission as _check_permission
from users.impersonation import impersonation_write_denial

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_tenant_user(request):
    """
    Return the ``TenantUser`` (with ``role`` pre-fetched) for the current
    request user in their active tenant.  Returns ``None`` when the user
    has no tenant membership.
    """
    from tenants.models import TenantUser

    user = request.user
    tenant = getattr(user, "tenant", None)
    if tenant is None:
        return None

    return TenantUser.objects.select_related("role").filter(user=user, tenant=tenant, is_active=True).first()


# ---------------------------------------------------------------------------
# Main permission class
# ---------------------------------------------------------------------------


class TenantRolePermission(BasePermission):
    """
    Check the requesting user's ``TenantRole`` against the permission key
    declared on the viewset for the current action.

    The viewset must define a ``required_permissions`` dict that maps DRF
    action names to permission keys from ``ALL_PERMISSIONS``.

    **Reads may fall back to "default"; writes may not.** A request using an
    unsafe method (POST, PUT, PATCH, DELETE) must have an explicit entry for
    its action, or it is denied. The ``"default"`` key exists so a viewset
    need not enumerate every read action, and in practice it is always a
    ``*.view`` permission — allowing it to cover writes as well grants write
    access to every role that can read.

    A viewset that declares no ``required_permissions`` at all is treated as
    having opted out of RBAC entirely and is allowed.

    Superusers always pass — **unless** they are impersonating an organisation
    and the method is not safe (#300).
    """

    message = "You do not have the required role permission to perform this action."

    def has_permission(self, request, view):
        user = request.user

        # Unauthenticated → deny
        if not user or not user.is_authenticated:
            return False

        # A "view as organisation" session is read-only (#300). This runs
        # before the superuser bypass below, and has to: the bypass is what
        # makes the read possible at all — the platform admin has no role in
        # the organisation being viewed — so without this check the same line
        # would also hand them every write in it.
        denial = impersonation_write_denial(request)
        if denial:
            self.message = denial
            return False

        # Superusers bypass RBAC
        if user.is_superuser:
            return True

        # Determine the permission key for the current action
        action = getattr(view, "action", None) or request.method.lower()
        required_perms = getattr(view, "required_permissions", {})

        # Viewset has no required_permissions at all → it chose not to restrict.
        if not required_perms:
            return True

        # If the view does not support this method at all, permissions are
        # not the reason the request fails. Let it through so DRF returns
        # 405 Method Not Allowed rather than a 403 that misdescribes why —
        # DRF runs check_permissions before it resolves the handler, so
        # denying here would mask the real answer.
        supported = [m.lower() for m in getattr(view, "http_method_names", [])]
        if supported and request.method.lower() not in supported:
            return True

        explicit = required_perms.get(action)

        # A write must be mapped explicitly. The "default" key exists so a
        # viewset does not have to enumerate every read action, and it is
        # almost always a *.view permission — letting it also authorise POST,
        # PATCH, PUT or DELETE silently grants write access to every role that
        # can read. That is how a read-only viewer came to be able to credit
        # any tenant's wallet through the transactions endpoint.
        #
        # So: reads may fall back to "default"; writes may not. An unmapped
        # write is denied, which fails closed — a new viewset that forgets to
        # map its writes returns 403 rather than quietly exposing them.
        if request.method not in SAFE_METHODS:
            if not explicit:
                self.message = (
                    f"Permission denied: action '{action}' modifies data but has no explicit "
                    f"permission mapping on {view.__class__.__name__}. Writes are never covered "
                    f"by the 'default' key; add an entry to required_permissions."
                )
                return False
            perm_key = explicit
        else:
            perm_key = explicit or required_perms.get("default")
            if not perm_key:
                self.message = (
                    f"Permission denied: no permission mapping for action '{action}'. Access denied by default."
                )
                return False

        # Resolve user's role within their tenant
        tenant_user = _resolve_tenant_user(request)
        if not tenant_user or not tenant_user.role:
            self.message = "You are not assigned a role in this tenant. Contact your tenant admin."
            return False

        # Check the DB-backed permission
        if not _check_permission(tenant_user.role, perm_key):
            self.message = (
                f"Permission denied: your role '{tenant_user.role.name}' does not have '{perm_key}' permission."
            )
            return False

        return True


# ---------------------------------------------------------------------------
# Priority-based shortcut classes
# ---------------------------------------------------------------------------


class _PriorityPermission(BasePermission):
    """
    Base class for priority-based role checks.
    Subclasses set ``min_priority`` and ``role_label``.
    """

    min_priority: int = 0
    role_label: str = ""

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # Read-only while impersonating (#300) — before the superuser bypass,
        # for the same reason as in TenantRolePermission.
        denial = impersonation_write_denial(request)
        if denial:
            self.message = denial
            return False

        if user.is_superuser:
            return True

        tenant_user = _resolve_tenant_user(request)
        if not tenant_user or not tenant_user.role:
            return False

        if tenant_user.role.priority < self.min_priority:
            self.message = (
                f"This action requires {self.role_label} role or above. Your current role is '{tenant_user.role.name}'."
            )
            return False

        return True


class IsOwner(_PriorityPermission):
    """Only users with the OWNER role (priority = 100)."""

    min_priority = 100
    role_label = "Owner"


class IsAdminOrAbove(_PriorityPermission):
    """ADMIN (priority >= 80) or OWNER."""

    min_priority = 80
    role_label = "Admin"


class IsManagerOrAbove(_PriorityPermission):
    """MANAGER (priority >= 60), ADMIN, or OWNER."""

    min_priority = 60
    role_label = "Manager"


class IsAgentOrAbove(_PriorityPermission):
    """AGENT (priority >= 40), MANAGER, ADMIN, or OWNER."""

    min_priority = 40
    role_label = "Agent"
