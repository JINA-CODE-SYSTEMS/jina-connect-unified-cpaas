from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
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
            # `str(None)` is the string "None", which would ship to clients as a
            # phone number made of four letters. Absent is "", as it was before
            # the column became nullable, so the response shape is unchanged.
            "mobile": str(user.mobile) if user.mobile else "",
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

    def validate(self, attrs):
        """Authenticate, then refuse a token while a temporary password stands.

        An operator sets the first password when onboarding a tenant (#221),
        so until the holder replaces it the operator knows their credentials.
        Blocking token issuance is what stops "temporary" becoming permanent.

        The check runs after super().validate(), so a wrong password still
        fails as a wrong password and this never reveals that an account
        exists or is in a pending state.
        """
        data = super().validate(attrs)

        if getattr(self.user, "must_change_password", False):
            raise AuthenticationFailed(
                detail={
                    "detail": (
                        "This password was set by an operator and must be replaced before "
                        "the account can be used. Set a new one at /users/set-initial-password/."
                    ),
                    "code": "password_change_required",
                },
                code="password_change_required",
            )

        return data

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


class PlatformAdministratorSerializer(serializers.ModelSerializer):
    """One row of ``GET /users/platform-admins/`` — who, when granted, by whom.

    ``granted_at`` and ``granted_by`` are **nullable and often null**, and that
    is not a defect to paper over. Every administrator who predates #358 was
    made with ``manage.py createsuperuser`` or in the Django admin, and no
    record of it exists to report. Null says "we do not know"; substituting
    ``date_joined`` would say something false about the exact question this
    endpoint exists to answer.

    Both come from annotations the viewset attaches (see
    ``PlatformAdminViewSet.get_queryset``) rather than from a per-row query, so
    a page of ten administrators costs two queries and not twenty-one.

    ``is_active`` is on the list on purpose: an invited administrator has not
    verified their email yet, cannot obtain a token, and so is an administrator
    in name only. A client that drops the column shows two identical-looking
    rows for two very different states.
    """

    granted_at = serializers.DateTimeField(read_only=True, allow_null=True)
    granted_by = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "granted_at",
            "granted_by",
        ]
        read_only_fields = fields

    def get_granted_by(self, obj):
        """The administrator who granted this one, or ``None`` if unrecorded.

        ``id`` can be null while ``username`` is not: the audit row keeps the
        granter's username after their account is deleted, and a name with no
        account to link to is still the answer to "who did this".
        """
        username = getattr(obj, "granted_by_username", None)
        if not username:
            return None
        return {"id": getattr(obj, "granted_by_user_id", None), "username": username}


class InvitePlatformAdministratorSerializer(serializers.Serializer):
    """Body of ``POST /users/platform-admins/invite/``.

    Deliberately shaped like ``AddMemberSerializer`` minus ``role_id`` — there
    is no role to choose, which is the whole of #358's deferred decision (see
    ``users.viewsets.platform_admin``). Same two branches, same field names,
    same password rule, so an operator who has invited an organisation member
    already knows this form.

    ``password`` and ``first_name`` are required only when the address has no
    account yet, for the reason ``AddMemberSerializer`` gives: an existing user
    keeps the password they already have, and being handed a new one by whoever
    granted them platform rights would be worse than not.
    """

    email = serializers.EmailField()
    password = serializers.CharField(required=False, write_only=True)
    first_name = serializers.CharField(required=False, max_length=150)
    last_name = serializers.CharField(required=False, max_length=150, default="")

    def validate_email(self, value):
        return value.lower()

    def validate_password(self, value):
        from users.services.account_provisioning import validate_password_strength

        return validate_password_strength(value)

    def validate(self, attrs):
        """If the email is new, an account has to be creatable from this body."""
        from users.services.account_provisioning import find_user_by_email

        if find_user_by_email(attrs.get("email", "")) is None:
            if not attrs.get("password"):
                raise serializers.ValidationError({"password": "Password is required for new users."})
            if not attrs.get("first_name"):
                raise serializers.ValidationError({"first_name": "First name is required for new users."})
        return attrs
