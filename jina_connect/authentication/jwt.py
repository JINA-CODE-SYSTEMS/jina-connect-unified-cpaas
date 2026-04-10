from rest_framework_simplejwt.authentication import JWTAuthentication


class CustomJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        user.tenant_id = validated_token.get("tenant_id")
        user.is_superuser = validated_token.get("is_superuser", False)

        # ── RBAC role claims ────────────────────────────────────
        user.role_slug = validated_token.get("role")
        user.role_name = validated_token.get("role_name")
        user.role_priority = validated_token.get("role_priority")

        return user
