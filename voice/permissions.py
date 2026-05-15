"""DRF permissions for the voice REST API (#174).

Two gates layered on top of ``IsAuthenticated``:

  * ``IsVoiceEnabledForTenant`` — every voice endpoint requires the
    requesting user's tenant to have ``TenantVoiceApp.is_enabled``. A
    tenant without voice provisioning sees the same 403 a non-member
    would — no info-leak via differential responses.
  * ``IsVoiceAdmin`` — applied to endpoints that touch
    ``VoiceProviderConfig`` / ``VoiceRateCard``. Falls back to
    ``is_staff`` since the project's RBAC layer is per-channel and the
    voice-specific permission strings haven't been wired into the seed
    role yet — staff users keep working, regular tenant users can't
    poke at provider credentials.
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


class IsVoiceAdmin(BasePermission):
    """Restrict provider-credential / rate-card endpoints to staff.

    Staff bypass tenants entirely. Non-staff users that are
    ``TenantUser`` rows with an ``OWNER`` / ``ADMIN`` role on at least
    one tenant also qualify — keeps regular agents out of credential
    UIs without coupling the voice app to a specific RBAC seed.
    """

    message = "Voice provider configuration is admin-only."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        # Best-effort RBAC check — the role names vary across seed
        # data, but ADMIN / OWNER are the canonical "can manage
        # config" tiers.
        try:
            from tenants.models import TenantUser
        except ImportError:  # pragma: no cover — defensive
            return False
        return TenantUser.objects.filter(
            user=user,
            role__name__in=("OWNER", "ADMIN", "Owner", "Admin"),
        ).exists()
