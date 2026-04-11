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

from rest_framework.permissions import BasePermission

from tenants.permissions import has_permission as _check_permission

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
    action names to permission keys from ``ALL_PERMISSIONS``.  If a
    ``"default"`` key is present it is used as fallback for unmapped
    actions.  If no mapping is found at all, access is granted (the
    viewset chose not to restrict that action).

    Superusers always pass.
    """

    message = "You do not have the required role permission to perform this action."

    def has_permission(self, request, view):
        user = request.user

        # Unauthenticated → deny
        if not user or not user.is_authenticated:
            return False

        # Superusers bypass RBAC
        if user.is_superuser:
            return True

        # Determine the permission key for the current action
        action = getattr(view, "action", None) or request.method.lower()
        required_perms = getattr(view, "required_permissions", {})
        perm_key = required_perms.get(action) or required_perms.get("default")

        # Deny-by-default: if viewset declares required_permissions but
        # this action has no mapping (and no "default" fallback), block.
        if not perm_key:
            if required_perms:
                self.message = (
                    f"Permission denied: no permission mapping for action '{action}'. Access denied by default."
                )
                return False
            # Viewset has no required_permissions at all → allow
            return True

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
