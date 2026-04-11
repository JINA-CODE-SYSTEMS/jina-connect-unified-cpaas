from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from tenants.filters import TenantFilter
from tenants.models import RolePermission, Tenant, TenantRole, TenantUser
from tenants.permissions import ALL_PERMISSIONS
from tenants.serializers import (
    MyPermissionsSerializer,
    TenantLimitedSerializer,
    TenantRegistrationSerializer,
    TenantSerializer,
    TransferOwnershipSerializer,
)


class TenantViewSet(BaseTenantModelViewSet):
    """
    A viewset for managing tenants with advanced filtering capabilities.
    """

    serializer_class = TenantSerializer
    filterset_class = TenantFilter
    required_permissions = {
        "list": "tenant.view",
        "retrieve": "tenant.view",
        "partial_update": "tenant.edit",
        "change_password": "tenant.edit",
        "notifications": "tenant.view",
        "transfer_ownership": "tenant.transfer",
        "default": "tenant.view",
    }

    def get_serializer_class(self):
        """
        #251: ADMIN/OWNER (priority >= 80) get full financial fields.
        Everyone else gets TenantLimitedSerializer.
        """
        tu = self._get_tenant_user()
        if tu and tu.role and tu.role.priority >= 80:
            return TenantSerializer
        return TenantLimitedSerializer

    def list(self, request, *args, **kwargs):
        """
        List tenants with filtering support.
        """
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """
        Get queryset with optimized prefetching and contacts count annotation.
        """
        return Tenant.objects.prefetch_related("wa_apps").annotate(contacts_count=Count("contacts")).all()

    def get_permissions(self):
        """
        Get permissions for the viewset.
        """
        if self.action == "create":
            self.permission_classes = [IsAdminUser]
        elif self.action in ["register", "verify_email", "resend_verification", "forgot_password", "reset_password"]:
            self.permission_classes = [AllowAny]
        elif self.action == "my_permissions":
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    @action(detail=False, methods=["get"], url_path="my-permissions", url_name="my-permissions")
    def my_permissions(self, request):
        """
        Return the current user's role and effective permissions map.

        Response:
        {
            "role": { "id": 42, "slug": "manager", "name": "Manager", ... },
            "permissions": { "tenant.view": true, "billing.manage": false, ... }
        }
        """
        from tenants.models import TenantUser

        tenant_user = TenantUser.objects.filter(user=request.user, is_active=True).select_related("role").first()
        if not tenant_user or not tenant_user.role:
            return Response(
                {"detail": "No active tenant membership or role found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        role = tenant_user.role

        # Build permissions map: every key from ALL_PERMISSIONS with true/false
        granted = set(RolePermission.objects.filter(role=role, allowed=True).values_list("permission", flat=True))
        permissions = {perm: perm in granted for perm in ALL_PERMISSIONS}

        serializer = MyPermissionsSerializer({"role": role, "permissions": permissions})
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="transfer-ownership", url_name="transfer-ownership")
    def transfer_ownership(self, request):
        """
        Transfer OWNER role to an ADMIN.

        The current OWNER becomes ADMIN, the target ADMIN becomes OWNER.
        Request:  { "target_user_id": 42 }
        """
        from django.db import transaction as db_transaction

        requester_tu = (
            TenantUser.objects.select_related("tenant", "role").filter(user=request.user, is_active=True).first()
        )
        if not requester_tu:
            return Response(
                {"detail": "You are not an active member of any tenant."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = TransferOwnershipSerializer(
            data=request.data,
            context={"request": request, "tenant": requester_tu.tenant},
        )
        serializer.is_valid(raise_exception=True)

        tenant = requester_tu.tenant
        owner_role = TenantRole.objects.get(tenant=tenant, slug="owner")
        admin_role = TenantRole.objects.get(tenant=tenant, slug="admin")

        with db_transaction.atomic():
            # Lock both rows to prevent concurrent transfers
            locked_requester = TenantUser.objects.select_for_update().get(
                tenant=tenant, user=request.user, is_active=True
            )
            locked_target = TenantUser.objects.select_for_update().get(
                tenant=tenant, user_id=serializer.validated_data["target_user_id"], is_active=True
            )

            # Re-validate inside lock: requester must still be OWNER
            if not locked_requester.role or locked_requester.role.slug != "owner":
                return Response(
                    {"detail": "You are no longer the OWNER."},
                    status=status.HTTP_409_CONFLICT,
                )

            # Target becomes OWNER
            locked_target.role = owner_role
            locked_target.updated_by = request.user
            locked_target.save(update_fields=["role", "updated_by", "updated_at"])

            # Previous OWNER becomes ADMIN
            locked_requester.role = admin_role
            locked_requester.updated_by = request.user
            locked_requester.save(update_fields=["role", "updated_by", "updated_at"])

        return Response(
            {
                "detail": "Ownership transferred successfully.",
                "new_owner": locked_target.user.email,
                "previous_owner_new_role": "admin",
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="register",
        url_name="register",
        serializer_class=TenantRegistrationSerializer,
        permission_classes=[AllowAny],
    )
    def register(self, request):
        """
        Anonymous action to register a new tenant and user.

        Creates a new user account and tenant organization, then links them together.
        User is inactive until email is verified.

        Request body:
        {
            "tenant_name": "My Company",
            "tenant_website": "https://mycompany.com",  // optional
            "tenant_address": "123 Main St, City",  // optional
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@mycompany.com",
            "phone": "+919876543210",
            "password": "securepassword123"
        }

        Returns:
        {
            "message": "Registration successful. Please check your email to verify your account.",
            "user_id": 1,
            "tenant_id": 1,
            "email": "john@mycompany.com",
            "tenant_name": "My Company",
            "email_sent": true
        }
        """
        serializer = TenantRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        if result["email_sent"]:
            message = "Registration successful. Please check your email to verify your account."
        else:
            message = "Registration successful, but we could not send the verification email. Please use the resend option to try again."

        return Response(
            {
                "message": message,
                "user_id": result["user"].id,
                "tenant_id": result["tenant"].id,
                "email": result["user"].email,
                "tenant_name": result["tenant"].name,
                "email_sent": result["email_sent"],
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get", "post"], url_path="verify-email", url_name="verify-email")
    def verify_email(self, request):
        """
        Verify user email with token.

        Supports both GET (web link click) and POST (API call).

        GET: /api/tenants/verify-email/?token=xxx
        - Used when user clicks verification link in email
        - Returns HTML redirect to login page on success

        POST: /api/tenants/verify-email/
        Request body:
        {
            "token": "verification_token_here"
        }
        - Returns JSON response

        Returns:
        {
            "message": "Email verified successfully. Your account is now active.",
            "email": "john@mycompany.com"
        }
        """
        from django.conf import settings
        from django.shortcuts import redirect

        from users.models import EmailVerificationToken

        # Get token from query params (GET) or request body (POST)
        if request.method == "GET":
            token_str = request.query_params.get("token")
            is_web_request = True
        else:
            token_str = request.data.get("token")
            is_web_request = False

        if not token_str:
            if is_web_request:
                # Redirect to login with error
                frontend_url = getattr(settings, "FRONTEND_URL", "https://app.jinaconnect.com")
                return redirect(f"{frontend_url}/login?error=invalid_token")
            return Response({"error": "Token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            token = EmailVerificationToken.objects.select_related("user").get(token=token_str)
        except EmailVerificationToken.DoesNotExist:
            if is_web_request:
                frontend_url = getattr(settings, "FRONTEND_URL", "https://app.jinaconnect.com")
                return redirect(f"{frontend_url}/login?error=invalid_token")
            return Response({"error": "Invalid verification token."}, status=status.HTTP_400_BAD_REQUEST)

        if token.is_used:
            if is_web_request:
                # Already verified - redirect to login with success message
                frontend_url = getattr(settings, "FRONTEND_URL", "https://app.jinaconnect.com")
                return redirect(f"{frontend_url}/login?verified=already")
            return Response(
                {"error": "This verification link has already been used."}, status=status.HTTP_400_BAD_REQUEST
            )

        if token.is_expired:
            if is_web_request:
                frontend_url = getattr(settings, "FRONTEND_URL", "https://app.jinaconnect.com")
                return redirect(f"{frontend_url}/login?error=token_expired")
            return Response(
                {"error": "This verification link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify the token (marks as used and activates user)
        token.verify()

        if is_web_request:
            # Redirect to login page with success message
            frontend_url = getattr(settings, "FRONTEND_URL", "https://app.jinaconnect.com")
            return redirect(f"{frontend_url}/login?verified=success&email={token.user.email}")

        return Response(
            {
                "message": "Email verified successfully. Your account is now active.",
                "email": token.user.email,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="resend-verification", url_name="resend-verification")
    def resend_verification(self, request):
        """
        Resend verification email.

        Request body:
        {
            "email": "john@mycompany.com"
        }

        Returns:
        {
            "message": "Verification email sent successfully."
        }
        """
        from users.models import User
        from users.services.email_verification import EmailVerificationService

        email = request.data.get("email")
        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            # Don't reveal if email exists or not for security
            return Response(
                {"message": "If an account exists with this email, a verification link has been sent."},
                status=status.HTTP_200_OK,
            )

        if user.is_active:
            return Response({"message": "This email is already verified."}, status=status.HTTP_200_OK)

        success, message = EmailVerificationService.resend_verification_email(user)

        return Response(
            {
                "message": message
                if success
                else "If an account exists with this email, a verification link has been sent."
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="forgot-password", url_name="forgot-password")
    def forgot_password(self, request):
        """
        Request password reset email.

        Request body:
        {
            "email": "john@mycompany.com"
        }

        Returns:
        {
            "message": "If an account exists with this email, a password reset link has been sent."
        }

        Note: For security, always returns success message regardless of whether email exists.
        """
        from users.models import PasswordResetToken, User
        from users.services.password_reset import PasswordResetService

        email = request.data.get("email")
        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Generic success message for security (don't reveal if email exists)
        success_message = "If an account exists with this email, a password reset link has been sent."

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            # Don't reveal if email exists or not for security
            return Response({"message": success_message}, status=status.HTTP_200_OK)

        # Check if user is active
        if not user.is_active:
            return Response({"message": success_message}, status=status.HTTP_200_OK)

        # Create password reset token
        token = PasswordResetToken.create_for_user(user)

        # Send password reset email
        PasswordResetService.send_password_reset_email(user, token)

        return Response({"message": success_message}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="validate-reset-token", url_name="validate-reset-token")
    def validate_reset_token(self, request):
        """
        Validate a password reset token without using it.

        Used by mobile app to check if token is valid before showing reset form.

        Request body:
        {
            "token": "reset_token_here"
        }

        Returns (on valid token):
        {
            "valid": true,
            "email": "john@mycompany.com",
            "message": "Token is valid. You can proceed to reset your password."
        }

        Returns (on invalid/expired token):
        {
            "valid": false,
            "error": "Invalid or expired reset token."
        }
        """
        from users.models import PasswordResetToken

        token_str = request.data.get("token")

        if not token_str:
            return Response({"valid": False, "error": "Token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            token = PasswordResetToken.objects.select_related("user").get(token=token_str)
        except PasswordResetToken.DoesNotExist:
            return Response(
                {"valid": False, "error": "Invalid or expired reset token."}, status=status.HTTP_400_BAD_REQUEST
            )

        if token.is_used:
            return Response(
                {"valid": False, "error": "This reset link has already been used. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if token.is_expired:
            return Response(
                {"valid": False, "error": "This reset link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Token is valid
        return Response(
            {
                "valid": True,
                "email": token.user.email,
                "message": "Token is valid. You can proceed to reset your password.",
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False, methods=["post"], url_path="validate-verification-token", url_name="validate-verification-token"
    )
    def validate_verification_token(self, request):
        """
        Validate an email verification token without using it.

        Used by mobile app to check if token is valid before auto-verifying.

        Request body:
        {
            "token": "verification_token_here"
        }

        Returns (on valid token):
        {
            "valid": true,
            "email": "john@mycompany.com",
            "message": "Token is valid. You can proceed to verify your email."
        }

        Returns (on invalid/expired token):
        {
            "valid": false,
            "error": "Invalid or expired verification token."
        }
        """
        from users.models import EmailVerificationToken

        token_str = request.data.get("token")

        if not token_str:
            return Response({"valid": False, "error": "Token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            token = EmailVerificationToken.objects.select_related("user").get(token=token_str)
        except EmailVerificationToken.DoesNotExist:
            return Response(
                {"valid": False, "error": "Invalid or expired verification token."}, status=status.HTTP_400_BAD_REQUEST
            )

        if token.is_used:
            return Response(
                {
                    "valid": False,
                    "already_verified": True,
                    "error": "This email has already been verified. You can login now.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if token.is_expired:
            return Response(
                {"valid": False, "error": "This verification link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Token is valid
        return Response(
            {
                "valid": True,
                "email": token.user.email,
                "message": "Token is valid. You can proceed to verify your email.",
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get", "post"], url_path="reset-password", url_name="reset-password")
    def reset_password(self, request):
        """
        Reset password using token from email.

        Supports both GET (token validation) and POST (password reset).

        GET: /api/tenants/reset-password/?token=xxx
        - Used by mobile app or frontend to validate token before showing reset form
        - Returns JSON with token validity and user email

        POST: /api/tenants/reset-password/
        Request body:
        {
            "token": "reset_token_here",
            "password": "newpassword123",
            "confirm_password": "newpassword123"
        }

        Returns:
        {
            "message": "Password reset successfully. You can now login with your new password."
        }
        """
        from users.models import PasswordResetToken

        # Handle GET request - validate token and return info
        if request.method == "GET":
            token_str = request.query_params.get("token")

            if not token_str:
                return Response({"valid": False, "error": "Token is required."}, status=status.HTTP_400_BAD_REQUEST)

            try:
                token = PasswordResetToken.objects.select_related("user").get(token=token_str)
            except PasswordResetToken.DoesNotExist:
                return Response(
                    {"valid": False, "error": "Invalid or expired reset token."}, status=status.HTTP_400_BAD_REQUEST
                )

            if token.is_used:
                return Response(
                    {"valid": False, "error": "This reset link has already been used. Please request a new one."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if token.is_expired:
                return Response(
                    {"valid": False, "error": "This reset link has expired. Please request a new one."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Token is valid - return user info for the reset form
            return Response(
                {
                    "valid": True,
                    "email": token.user.email,
                    "token": token_str,
                    "message": "Token is valid. You can proceed to reset your password.",
                },
                status=status.HTTP_200_OK,
            )

        # Handle POST request - actually reset the password
        token_str = request.data.get("token")
        password = request.data.get("password")
        confirm_password = request.data.get("confirm_password")

        # Validate required fields
        if not token_str:
            return Response({"error": "Token is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not password:
            return Response({"error": "Password is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not confirm_password:
            return Response({"error": "Confirm password is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate password match
        if password != confirm_password:
            return Response({"error": "Passwords do not match."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate password strength
        if len(password) < 8:
            return Response(
                {"error": "Password must be at least 8 characters long."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Find and validate token
        try:
            token = PasswordResetToken.objects.select_related("user").get(token=token_str)
        except PasswordResetToken.DoesNotExist:
            return Response({"error": "Invalid or expired reset token."}, status=status.HTTP_400_BAD_REQUEST)

        if token.is_used:
            return Response(
                {"error": "This reset link has already been used. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if token.is_expired:
            return Response(
                {"error": "This reset link has expired. Please request a new one."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Reset password
        user = token.user
        user.set_password(password)
        user.save()

        # Mark token as used
        token.use_token()

        return Response(
            {
                "message": "Password reset successfully. You can now login with your new password.",
                "email": user.email,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="change-password", url_name="change-password")
    def change_password(self, request):
        """
        Change password for authenticated user.

        Allows logged-in users to change their password by providing
        their current password and a new password.

        Request body:
        {
            "old_password": "current_password",
            "new_password": "new_password123",
            "confirm_password": "new_password123"
        }

        Returns:
        {
            "message": "Password changed successfully."
        }

        Errors:
        - 400: Missing required fields
        - 400: Passwords don't match
        - 400: Password too short (min 8 chars)
        - 400: Current password is incorrect
        - 401: User not authenticated
        """
        user = request.user

        if not user.is_authenticated:
            return Response({"error": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        # Validate required fields
        if not old_password:
            return Response({"error": "Current password is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not new_password:
            return Response({"error": "New password is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not confirm_password:
            return Response({"error": "Confirm password is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate password match
        if new_password != confirm_password:
            return Response({"error": "New passwords do not match."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate password strength
        if len(new_password) < 8:
            return Response(
                {"error": "Password must be at least 8 characters long."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Verify current password
        if not user.check_password(old_password):
            return Response({"error": "Current password is incorrect."}, status=status.HTTP_400_BAD_REQUEST)

        # Prevent setting same password
        if old_password == new_password:
            return Response(
                {"error": "New password must be different from current password."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Change password
        user.set_password(new_password)
        user.save()

        return Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="notifications")
    def notifications(self, request):
        """
        Get quick notifications for the tenant dashboard.

        Query Parameters:
            limit (int): Number of notifications to return (default: 10)

        Returns list of notifications like:
        - Template approved/rejected
        - Template submitted for review
        - Broadcast scheduled/completed
        - New contacts added
        - System alerts

        NOTE: This is a MOCK API returning static data for frontend development.
        """
        limit = int(request.query_params.get("limit", 10))

        # MOCK DATA - Replace with real implementation later
        return Response(
            {
                "notifications": [
                    {
                        "id": 1,
                        "type": "TEMPLATE_APPROVED",
                        "icon": "check_circle",
                        "title": "Template approved",
                        "message": None,
                        "timestamp": "2025-11-23T10:00:00Z",
                        "is_read": False,
                        "action_url": "/templates",
                    },
                    {
                        "id": 2,
                        "type": "TEMPLATE_SUBMITTED",
                        "icon": "description",
                        "title": "New template submitted for review",
                        "message": None,
                        "timestamp": "2025-11-23T10:00:00Z",
                        "is_read": False,
                        "action_url": "/templates",
                    },
                    {
                        "id": 3,
                        "type": "BROADCAST_SCHEDULED",
                        "icon": "schedule",
                        "title": "Broadcast scheduled for 5 PM",
                        "message": None,
                        "timestamp": "2025-11-23T10:00:00Z",
                        "is_read": True,
                        "action_url": "/broadcasts",
                    },
                    {
                        "id": 4,
                        "type": "CONTACT_ADDED",
                        "icon": "person_add",
                        "title": "New contact added",
                        "message": None,
                        "timestamp": "2025-11-23T10:00:00Z",
                        "is_read": True,
                        "action_url": "/contacts",
                    },
                ][:limit],
                "total_count": 4,
                "unread_count": 2,
            }
        )
