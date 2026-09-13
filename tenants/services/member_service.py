"""
Member Service — reusable add-member business logic (RBAC-14).

Keeps viewset thin; logic reusable from management commands, signals, admin actions.
"""

import logging

from django.db import transaction

from tenants.models import TenantUser
from users.services.account_provisioning import (
    create_pending_user,
    find_user_by_email,
    send_account_verification,
)

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
    user = find_user_by_email(email)

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
        # Account creation and the verification mail are shared with the
        # platform-administrator invite (#358) rather than written twice — see
        # ``users.services.account_provisioning``. The ordering here is
        # unchanged and load-bearing: the membership is created before the mail
        # goes out, so an invitee who clicks the link immediately finds the
        # organisation already waiting for them.
        user = create_pending_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        tenant_user = TenantUser.objects.create(
            tenant=tenant,
            user=user,
            role=role,
            created_by=created_by,
        )

        send_account_verification(user)

        return tenant_user, True
