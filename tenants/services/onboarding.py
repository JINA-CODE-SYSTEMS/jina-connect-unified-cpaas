"""Create a tenant together with a working owner login (#221, #220).

Both ways an operator could previously create a tenant — ``POST /tenants/``
and the auto-registered Django admin — produced a tenant nobody could log
into. Default roles were seeded by signal and the wallet defaulted correctly;
what was missing was the owner. Only self-service registration wired all
three together, and that asks the customer for a password an operator does
not have and should not handle.

This is the one place that does it, so the admin page and the API endpoint
cannot drift apart.

Decisions this encodes (recorded on #220, 2026-09-10):

* **The operator sets a temporary password.** Onboarding has to work when
  email is unreliable or handover happens by phone. The cost is that the
  operator knows the credentials, so the account is flagged
  ``must_change_password`` and cannot obtain a token until the holder
  replaces it.
* **An existing email is linked, not rejected.** One person can own several
  tenants — ``TenantUser`` already models that. A linked account keeps its
  own password and is *not* flagged; it is established, not new.
"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction as db_transaction

from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


@dataclass(frozen=True)
class OnboardingResult:
    tenant: Tenant
    owner: User
    created_owner: bool

    @property
    def summary(self) -> str:
        verb = "created" if self.created_owner else "linked existing user"
        return f"Tenant {self.tenant.name!r} created with owner {self.owner.email} ({verb})."


def validate_new_tenant(*, name: str, owner_email: str, owner_mobile: str, temporary_password: str) -> None:
    """Everything an operator can get wrong, checked without writing anything.

    Shared with the admin form so it can show these as field errors instead
    of letting them surface as a 500 — and shared rather than duplicated so
    the two cannot drift.
    """
    name = (name or "").strip()
    owner_email = (owner_email or "").strip().lower()

    if Tenant.objects.filter(name__iexact=name).exists():
        raise ValidationError({"name": f"A tenant named {name!r} already exists."})

    if User.objects.filter(email__iexact=owner_email).exists():
        # Linking is intended, not an error — nothing else to check, and the
        # linked account keeps its own password.
        return

    if not temporary_password:
        raise ValidationError({"temporary_password": "A temporary password is required for a new owner."})

    # mobile is unique across all users, so a clash belongs to somebody else
    # entirely. Emails link accounts; mobiles cannot.
    if owner_mobile and User.objects.filter(mobile=owner_mobile).exists():
        raise ValidationError(
            {"owner_mobile": "That mobile number already belongs to another user. Emails link; mobiles do not."}
        )


def create_tenant_with_owner(
    *,
    name: str,
    owner_email: str,
    owner_mobile: str,
    temporary_password: str,
    first_name: str = "",
    last_name: str = "",
    description: str = "",
) -> OnboardingResult:
    """Create *name* with a usable owner login. Atomic: all of it, or none.

    Raises ``ValidationError`` for anything an operator can fix — a duplicate
    tenant name, a mobile already belonging to someone else. Those are
    ordinary mistakes and must read as validation errors rather than a 500.
    """
    owner_email = (owner_email or "").strip().lower()
    name = (name or "").strip()

    if not name:
        raise ValidationError({"name": "A tenant name is required."})
    if not owner_email:
        raise ValidationError({"owner_email": "An owner email is required."})

    with db_transaction.atomic():
        validate_new_tenant(
            name=name,
            owner_email=owner_email,
            owner_mobile=owner_mobile,
            temporary_password=temporary_password,
        )

        existing = User.objects.filter(email__iexact=owner_email).first()

        if existing is None:
            try:
                owner = User.objects.create_user(
                    username=owner_email,
                    email=owner_email,
                    password=temporary_password,
                    mobile=owner_mobile,
                    first_name=first_name,
                    last_name=last_name,
                )
            except IntegrityError as exc:
                raise ValidationError({"owner": f"Could not create the owner account: {exc}"}) from exc
            owner.must_change_password = True
            owner.save(update_fields=["must_change_password"])
            created_owner = True
        else:
            # Established account: it keeps its own password, and is not
            # flagged. Overwriting either would be interfering with a login
            # that already works elsewhere.
            owner = existing
            created_owner = False

        tenant = Tenant.objects.create(name=name, description=description)

        # seed_default_roles_on_tenant_creation (post_save) has run by now.
        try:
            owner_role = TenantRole.objects.get(tenant=tenant, slug="owner")
        except TenantRole.DoesNotExist as exc:
            raise ValidationError(
                {"tenant": "Default roles were not seeded for this tenant; cannot assign an owner."}
            ) from exc

        TenantUser.objects.create(tenant=tenant, user=owner, role=owner_role)

    return OnboardingResult(tenant=tenant, owner=owner, created_owner=created_owner)
