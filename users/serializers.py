from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from tenants.models import DefaultRoleSlugs, TenantRole, TenantUser
from users.models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = "__all__"
        extra_kwargs = {"password": {"write_only": True}}


class UserSafeSerializer(serializers.ModelSerializer):
    """Read-only serializer that hides sensitive fields from peer users."""

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "image"]
        read_only_fields = fields


class UserSelfSerializer(serializers.ModelSerializer):
    """Serializer for a user viewing/editing their own profile."""

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "mobile",
            "image",
            "birth_date",
            "address",
        ]
        read_only_fields = ["id", "username"]
        extra_kwargs = {
            "email": {"required": False},
            "mobile": {"required": False},
        }


class LoginPatchUserSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, required=True)
    mobile = PhoneNumberField(required=True)

    def create(self, validated_data):
        tenant = self.context["tenant"]

        user, created = User.objects.get_or_create(
            mobile=validated_data["mobile"],
            defaults={
                "username": str(validated_data["mobile"]),
                "first_name": validated_data["first_name"],
                "last_name": validated_data.get("last_name", ""),
                "mobile": validated_data["mobile"],
            },
        )

        if created:
            user.set_password(validated_data["password"])
            user.save()

        # Link user to tenant with default AGENT role
        default_role = TenantRole.objects.filter(
            tenant=tenant,
            slug=DefaultRoleSlugs.AGENT,
        ).first()
        if default_role is None:
            # Safety net: seed default roles if they're missing, then retry
            from tenants.permissions import seed_default_roles

            seed_default_roles(tenant)
            default_role = TenantRole.objects.filter(
                tenant=tenant,
                slug=DefaultRoleSlugs.AGENT,
            ).first()
        tenant_user, _tu_created = TenantUser.objects.get_or_create(
            tenant=tenant,
            user=user,
            defaults={"role": default_role},
        )
        # Back-fill role if existing TenantUser has NULL (legacy data)
        if not _tu_created and tenant_user.role is None and default_role:
            tenant_user.role = default_role
            tenant_user.save(update_fields=["role"])

        return user, created

    def to_representation(self, instance):
        user = instance[0] if isinstance(instance, tuple) else instance
        return {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "mobile": str(user.mobile),
        }


class JwtUserSerializer(TokenObtainPairSerializer):
    username = serializers.CharField(
        help_text="Username or email for authentication", style={"placeholder": "Enter your username or email"}
    )
    password = serializers.CharField(
        write_only=True,
        help_text="Password for authentication",
        style={"input_type": "password", "placeholder": "Enter your password"},
    )

    class Meta:
        fields = ["username", "password"]

    def get_token(self, user):
        token = super().get_token(user)

        # Add username
        token["username"] = user.username

        # Add groups (list of group names)
        token["groups"] = list(user.groups.values_list("name", flat=True))

        # Add superuser flag
        token["is_superuser"] = user.is_superuser

        # Get tenant from serializer context
        tenant = self.context.get("tenant")  # context is passed from view
        token["tenant_id"] = tenant.id if tenant else None

        # ── RBAC role claims ────────────────────────────────────────
        if tenant:
            from tenants.models import TenantUser

            tenant_user = (
                TenantUser.objects.filter(user=user, tenant=tenant, is_active=True).select_related("role").first()
            )
            if tenant_user and tenant_user.role:
                token["role"] = tenant_user.role.slug
                token["role_name"] = tenant_user.role.name
                token["role_priority"] = tenant_user.role.priority

        return token
