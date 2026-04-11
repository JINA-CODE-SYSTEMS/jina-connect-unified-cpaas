"""
Member Service — reusable add-member business logic (RBAC-14).

Keeps viewset thin; logic reusable from management commands, signals, admin actions.
"""

import logging

from django.db import transaction

from tenants.models import TenantUser
from users.models import EmailVerificationToken, User

logger = logging.getLogger(__name__)


def add_member_to_tenant(
    tenant,
    email,
    role,
    password=None,
    first_name=None,
    last_name=None,
    created_by=None,
):
    """
    Add a member to a tenant.

    Two paths:
        1. Email matches existing User → create TenantUser only.
        2. Email is new → create User (is_active=False) + TenantUser + EmailVerificationToken + send email.

    Args:
        tenant: Tenant instance.
        email: Email address (lowercase).
        role: TenantRole instance.
        password: Required if user is new.
        first_name: Required if user is new.
        last_name: Optional.
        created_by: User who initiated the action.

    Returns:
        tuple: (tenant_user, is_new_user)

    Raises:
        ValueError: If user is already an active member, or if new-user fields are missing.
    """
    email = email.lower()
    user = User.objects.filter(email__iexact=email).first()

    with transaction.atomic():
        if user:
            # --- Path 1: existing user ---
            existing = TenantUser.objects.filter(tenant=tenant, user=user).first()
            if existing:
                if existing.is_active:
                    raise ValueError("This user is already an active member of this tenant.")
                # Re-activate a previously deactivated member
                existing.is_active = True
                existing.role = role
                existing.updated_by = created_by
                existing.save(update_fields=["is_active", "role", "updated_by", "updated_at"])
                return existing, False

            tenant_user = TenantUser.objects.create(
                tenant=tenant,
                user=user,
                role=role,
                created_by=created_by,
            )
            return tenant_user, False

        # --- Path 2: new user ---
        if not password:
            raise ValueError("Password is required for new users.")
        if not first_name:
            raise ValueError("First name is required for new users.")

        user = User.objects.create(
            username=email,  # Use email as username
            email=email,
            first_name=first_name,
            last_name=last_name or "",
            password=password,  # User.save() auto-hashes via identify_hasher
            is_active=False,  # Pending email verification
        )

        tenant_user = TenantUser.objects.create(
            tenant=tenant,
            user=user,
            role=role,
            created_by=created_by,
        )

        # Create verification token and send email
        token = EmailVerificationToken.create_for_user(user)
        try:
            from users.services.email_verification import EmailVerificationService

            EmailVerificationService.send_verification_email(user, token)
        except Exception:
            logger.exception("Failed to send verification email to %s", email)

        return tenant_user, True
