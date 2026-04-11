"""
Member Management ViewSet (RBAC-12 to RBAC-17).

Provides list, add, role-change, remove, and resend-verification endpoints
at /tenants/members/.
Ownership transfer lives on TenantViewSet at /tenants/transfer-ownership/.
"""

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from tenants.models import TenantRole, TenantUser
from tenants.rbac_validators import check_target_priority, get_requester_tenant_user
from tenants.serializers import (
    AddMemberSerializer,
    ChangeRoleSerializer,
    MemberSerializer,
)
from tenants.services.member_service import add_member_to_tenant


class MemberManagementViewSet(BaseTenantModelViewSet):
    """
    Manage tenant members: list, add, change role, remove, resend verification.

    Endpoints:
        GET    /tenants/members/                          → list members
        GET    /tenants/members/{id}/                     → retrieve member
        POST   /tenants/members/add/                      → add a member (create user if new)
        PATCH  /tenants/members/{id}/role/                → change a member's role
        DELETE /tenants/members/{id}/                     → soft-remove a member
        POST   /tenants/members/{id}/resend-verification/ → resend verification email
    """

    queryset = TenantUser.objects.select_related("user", "role").all()
    serializer_class = MemberSerializer
    http_method_names = ["get", "post", "patch", "delete"]
    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
        "user__username",
    ]
    required_permissions = {
        "list": "users.view",
        "retrieve": "users.view",
        "add_member": "users.invite",
        "change_role": "users.change_role",
        "destroy": "users.remove",
        "resend_verification": "users.invite",
        "default": "users.view",
    }

    # ------------------------------------------------------------------
    # Block unintended ModelViewSet actions (create, partial_update)
    # ------------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        """Use /tenants/members/add/ instead."""
        return Response(
            {"detail": "Use POST /tenants/members/add/ to add members."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def partial_update(self, request, *args, **kwargs):
        """Use /tenants/members/{id}/role/ instead."""
        return Response(
            {"detail": "Use PATCH /tenants/members/{id}/role/ to change roles."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # Add Member (RBAC-13)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["post"], url_path="add", url_name="add-member")
    def add_member(self, request):
        """
        Add a member to this tenant.

        Two paths (per PRD §4.1.4):
        1. Email belongs to an existing user → create TenantUser (201).
        2. No account → create User (is_active=False) + TenantUser +
           EmailVerificationToken + send verification email (201).

        Request:  { "email": "bob@example.com", "password": "Str0ng!Pass",
                    "first_name": "Bob", "last_name": "Smith", "role_id": 5 }
        """
        requester_tu = get_requester_tenant_user(request)
        if not requester_tu:
            return Response(
                {"detail": "You are not an active member of any tenant."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AddMemberSerializer(
            data=request.data,
            context={"request": request, "tenant": requester_tu.tenant},
        )
        serializer.is_valid(raise_exception=True)

        tenant = requester_tu.tenant
        role = TenantRole.objects.get(
            id=serializer.validated_data["role_id"],
            tenant=tenant,
        )

        try:
            tenant_user, is_new_user = add_member_to_tenant(
                tenant=tenant,
                email=serializer.validated_data["email"],
                role=role,
                password=serializer.validated_data.get("password"),
                first_name=serializer.validated_data.get("first_name"),
                last_name=serializer.validated_data.get("last_name", ""),
                created_by=request.user,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        data = MemberSerializer(TenantUser.objects.select_related("user", "role").get(pk=tenant_user.pk)).data

        if is_new_user:
            data["message"] = "User created. Verification email sent — user must verify before logging in."
        else:
            data["message"] = "Existing user added to tenant."

        return Response(data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Change Role (RBAC-15)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["patch"], url_path="role", url_name="change-role")
    def change_role(self, request, pk=None):
        """
        Change a member's role.

        Request:  { "role_id": 7 }
        """
        tenant_user = self.get_object()

        # Cannot change the OWNER's role (must use transfer-ownership)
        if tenant_user.role and tenant_user.role.slug == "owner":
            return Response(
                {"detail": "Cannot change the OWNER's role. Use transfer-ownership instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve requester — guard against missing membership
        requester_tu = get_requester_tenant_user(request)
        if not requester_tu or not requester_tu.role:
            return Response(
                {"detail": "You are not an active member with a role in this tenant."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Cannot change your own role
        if tenant_user.user == request.user:
            return Response(
                {"detail": "You cannot change your own role."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cannot change role of someone with >= your priority
        err = check_target_priority(requester_tu, tenant_user.role, action_verb="change the role of a member with")
        if err:
            return Response({"detail": err}, status=status.HTTP_403_FORBIDDEN)

        serializer = ChangeRoleSerializer(
            data=request.data,
            context={"request": request, "tenant": requester_tu.tenant},
        )
        serializer.is_valid(raise_exception=True)

        role = TenantRole.objects.get(
            id=serializer.validated_data["role_id"],
            tenant=tenant_user.tenant,
        )
        tenant_user.role = role
        tenant_user.updated_by = request.user
        tenant_user.save(update_fields=["role", "updated_by", "updated_at"])

        return Response(MemberSerializer(tenant_user).data)

    # ------------------------------------------------------------------
    # Remove (RBAC-16) — soft delete
    # ------------------------------------------------------------------
    def destroy(self, request, *args, **kwargs):
        """
        Remove a member from the tenant (soft delete — sets is_active=False).
        Cannot remove the OWNER. Cannot remove yourself.
        """
        tenant_user = self.get_object()

        # Cannot remove the OWNER
        if tenant_user.role and tenant_user.role.slug == "owner":
            return Response(
                {"detail": "Cannot remove the OWNER. Use transfer-ownership first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cannot remove yourself
        if tenant_user.user == request.user:
            return Response(
                {"detail": "You cannot remove yourself from the tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve requester — guard against missing membership
        requester_tu = get_requester_tenant_user(request)
        if not requester_tu or not requester_tu.role:
            if not request.user.is_superuser:
                return Response(
                    {"detail": "You are not an active member with a role in this tenant."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        elif tenant_user.role:
            err = check_target_priority(requester_tu, tenant_user.role, action_verb="remove a member with")
            if err:
                return Response({"detail": err}, status=status.HTTP_403_FORBIDDEN)

        # Soft delete
        tenant_user.is_active = False
        tenant_user.updated_by = request.user
        tenant_user.save(update_fields=["is_active", "updated_by", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Resend Verification (RBAC-17)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="resend-verification", url_name="resend-verification")
    def resend_verification(self, request, pk=None):
        """
        Resend the email verification for a member whose email is still unverified.
        """
        tenant_user = self.get_object()
        user = tenant_user.user

        # Must be an unverified user (is_active=False means email not yet verified)
        if user.is_active:
            return Response(
                {"detail": "This member's email is already verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from users.models import EmailVerificationToken
        from users.services.email_verification import EmailVerificationService

        # Invalidate old tokens and create a new one
        token = EmailVerificationToken.create_for_user(user)

        try:
            EmailVerificationService.send_verification_email(user, token)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Failed to send verification email to %s",
                user.email,
            )
            return Response(
                {"detail": "Verification token created but email sending failed. Try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"detail": "Verification email resent.", "email": user.email},
            status=status.HTTP_200_OK,
        )
