from rest_framework_simplejwt.authentication import JWTAuthentication

from users.impersonation import IMPERSONATED_BY_CLAIM, enforce_impersonation


class CustomJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        """Authenticate, then apply the bounds an impersonated token carries (#300).

        This is the chokepoint every JWT-bearing HTTP request passes through:
        it is the project's only JWT authenticator, and it runs before
        ``check_permissions`` whatever a viewset declares — including the
        unauthenticated viewsets, which have no permission class that could
        refuse anything. A permission class alone would cover only the viewsets
        that remembered to list it, which is why the read-only refusal is here
        as well as in ``TenantRolePermission``.
        """
        result = super().authenticate(request)
        if result is None:
            return None

        _user, validated_token = result
        enforce_impersonation(request, validated_token)
        return result

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        user.tenant_id = validated_token.get("tenant_id")
        user.is_superuser = validated_token.get("is_superuser", False)

        # ── RBAC role claims ────────────────────────────────────
        user.role_slug = validated_token.get("role")
        user.role_name = validated_token.get("role_name")
        user.role_priority = validated_token.get("role_priority")

        # The real user behind a "view as organisation" session (#300), or None
        # for an ordinary token. Carried on the user so the permission layer can
        # see it without re-reading the token.
        setattr(user, IMPERSONATED_BY_CLAIM, validated_token.get(IMPERSONATED_BY_CLAIM))

        return user
