"""
Reusable RBAC validation helpers (RBAC-30).

Centralises privilege-escalation checks so every serializer and viewset
uses the same logic.  If a rule changes, it changes in one place.
"""

from rest_framework import serializers

from tenants.models import RolePermission, TenantRole, TenantUser
from tenants.permissions import ALL_PERMISSIONS


# ─── Requester Lookup ─────────────────────────────────────────────────────


def get_requester_tenant_user(request, tenant=None):
    """
    Return the active TenantUser for *request.user*, or None.

    Parameters
    ----------
    request : DRF Request
    tenant  : Tenant instance (optional) – if given, filters by that tenant.
    """
    qs = TenantUser.objects.select_related("tenant", "role").filter(
        user=request.user, is_active=True,
    )
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    return qs.first()


# ─── Priority Escalation ─────────────────────────────────────────────────


def validate_priority_escalation(priority_value, request, tenant):
    """
    Raise ``serializers.ValidationError`` if *priority_value* >= the
    requester's own role priority.

    Used by CreateRoleSerializer and UpdateRoleSerializer.
    """
    requester_tu = get_requester_tenant_user(request, tenant)
    if requester_tu and requester_tu.role and priority_value >= requester_tu.role.priority:
        raise serializers.ValidationError(
            f"Cannot set priority >= your own ({requester_tu.role.priority})."
        )
    return priority_value


# ─── Permission Escalation ────────────────────────────────────────────────


def validate_permission_escalation(permissions_dict, request, tenant):
    """
    Raise ``serializers.ValidationError`` when trying to grant unknown
    permission keys or permissions the requester doesn't have.

    Parameters
    ----------
    permissions_dict : dict[str, bool]  – ``{"broadcast.create": True, ...}``
    request          : DRF Request
    tenant           : Tenant instance

    Returns
    -------
    dict – the validated (unchanged) permissions dict.
    """
    if not permissions_dict:
        return permissions_dict

    # Unknown keys
    unknown = set(permissions_dict.keys()) - set(ALL_PERMISSIONS)
    if unknown:
        raise serializers.ValidationError(
            f"Unknown permission keys: {', '.join(sorted(unknown))}"
        )

    # Escalation check – can't grant what you don't have
    requester_tu = get_requester_tenant_user(request, tenant)
    if requester_tu and requester_tu.role:
        requester_perms = dict(
            RolePermission.objects.filter(role=requester_tu.role)
            .values_list("permission", "allowed")
        )
        escalation = [
            k for k, v in permissions_dict.items()
            if v and not requester_perms.get(k, False)
        ]
        if escalation:
            raise serializers.ValidationError(
                f"Cannot grant permissions you don't have: "
                f"{', '.join(sorted(escalation))}"
            )
    return permissions_dict


# ─── Role Assignment ──────────────────────────────────────────────────────


def validate_role_assignment(role_id, request, tenant):
    """
    Validate that *role_id* can be assigned by the requester.

    Rules:
    1. Role must exist in the tenant.
    2. Cannot assign OWNER directly.
    3. Target role priority must be < requester's priority.

    Returns
    -------
    int – the validated role_id.

    Raises
    ------
    serializers.ValidationError
    """
    try:
        target_role = TenantRole.objects.get(id=role_id, tenant=tenant)
    except TenantRole.DoesNotExist:
        raise serializers.ValidationError("Role not found in this tenant.")

    if target_role.slug == "owner":
        raise serializers.ValidationError(
            "OWNER role cannot be assigned directly. Use the transfer-ownership flow."
        )

    requester_tu = get_requester_tenant_user(request, tenant)
    if not requester_tu or not requester_tu.role:
        raise serializers.ValidationError("You do not have a role in this tenant.")

    if target_role.priority >= requester_tu.role.priority:
        raise serializers.ValidationError(
            f"You cannot assign a role with priority >= your own "
            f"({requester_tu.role.priority})."
        )
    return role_id


# ─── Viewset-level Guards ────────────────────────────────────────────────


def check_target_priority(requester_tu, target_role, action_verb="manage"):
    """
    Return an error message if the *target_role* has priority >= the
    requester's role.  Return ``None`` if the action is allowed.

    Parameters
    ----------
    requester_tu : TenantUser – the requester
    target_role  : TenantRole – the role being acted on
    action_verb  : str        – used in the error message (e.g. "edit", "delete", "remove")
    """
    if (
        requester_tu
        and requester_tu.role
        and target_role
        and target_role.priority >= requester_tu.role.priority
    ):
        return (
            f"You cannot {action_verb} a role with equal or higher "
            f"priority than your own."
        )
    return None
