"""DRF permissions for the voice REST API (#174).

Two gates layered on top of ``IsAuthenticated``:

  * ``IsVoiceEnabledForTenant`` — every voice endpoint requires the
    requesting user's tenant to have ``TenantVoiceApp.is_enabled``. A
    tenant without voice provisioning sees the same 403 a non-member
    would — no info-leak via differential responses.
  * ``IsVoiceAdmin`` — applied to endpoints that touch
    ``VoiceProviderConfig`` / ``VoiceRateCard``. Checks the RBAC
    permission key ``voice.provider.edit`` (B2 #182). Staff users bypass.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission, IsAuthenticated  # noqa: F401


class IsVoiceEnabledForTenant(BasePermission):
    """Block all voice endpoints unless ``TenantVoiceApp.is_enabled``.

    The user can belong to multiple tenants via ``TenantUser`` — we
    accept the request if *any* of their tenants has voice enabled and
    rely on the per-view queryset for the per-tenant scoping. That
    matches the pattern other channels use.
    """

    message = "Voice is not enabled for any of your tenants."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        from tenants.models import TenantVoiceApp

        return TenantVoiceApp.objects.filter(
            tenant__tenant_users__user=user,
            is_enabled=True,
        ).exists()


def _user_has_voice_perm(user, perm_key: str) -> bool:
    """True if ``user`` has *perm_key* granted on at least one active
    tenant role.

    The RBAC layer (``tenants.permissions.has_permission``) operates on
    a single ``TenantRole`` so this helper iterates the user's
    memberships and short-circuits on the first allow. Avoids the
    ``user.tenant`` shortcut used by ``TenantRolePermission`` so it
    works for users whose tenant is not pinned on the request.
    """
    from tenants.models import TenantUser
    from tenants.permissions import has_permission

    tenant_users = TenantUser.objects.filter(user=user, is_active=True).select_related("role")
    for tu in tenant_users:
        if tu.role and has_permission(tu.role, perm_key):
            return True
    return False


class IsVoiceAdmin(BasePermission):
    """Restrict provider-credential / rate-card endpoints to admins.

    A user passes if either:

      * ``request.user.is_staff`` (superuser bypass), or
      * any of the user's active tenant roles grants
        ``voice.provider.edit`` (the canonical voice-admin RBAC key
        seeded by tenants migration 0019).

    Falling back to staff keeps the gate working when a tenant's role
    rows haven't been re-seeded — voice.* keys are auto-granted to
    OWNER/ADMIN on tenant creation via the ``Tenant.post_save`` signal,
    and the data migration back-fills existing tenants.
    """

    message = "Voice provider configuration is admin-only."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        return _user_has_voice_perm(user, "voice.provider.edit")


class HasVoicePermission(BasePermission):
    """Generic gate that reads the required key from the viewset.

    The viewset declares a ``voice_required_permission`` attribute (or
    a per-action dict via ``voice_required_permissions``). The gate
    resolves the active action, looks up the matching key, and checks
    it against the user's tenant roles.

    Drop-in replacement for ``IsVoiceAdmin`` when the viewset needs
    per-action granularity (e.g. recordings: ``play`` vs. ``download``).
    """

    message = "You do not have the required voice permission for this action."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if user.is_staff:
            return True

        per_action = getattr(view, "voice_required_permissions", None)
        if per_action:
            action = getattr(view, "action", None) or request.method.lower()
            perm_key = per_action.get(action) or per_action.get("default")
        else:
            perm_key = getattr(view, "voice_required_permission", None)

        if not perm_key:
            # No key declared → fall through to the rest of the
            # permission stack (deny-only-when-keyed).
            return True
        return _user_has_voice_perm(user, perm_key)
