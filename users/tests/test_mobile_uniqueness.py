"""Two people with no number are not the same person (#360).

``mobile`` was ``unique=True`` on a NOT NULL column, so "no number known" was the
empty string and the empty string fits exactly once. The second account ever
created without a number raised ``IntegrityError`` on ``users_user_mobile_key``.

Three things made that hard to see:

* **Only the new-account branch.** Inviting somebody who already has an account
  creates no ``User`` and works fine, so a team inviting existing colleagues
  never hits it.
* **It is global, not per-organisation.** The constraint is on ``users_user``,
  so organisation B's first-ever new-account invitation fails because
  organisation A already spent the one empty slot.
* **A fresh deployment gets exactly one before it breaks**, which reads as a
  one-off rather than a rule.

**Every test here invites twice.** A single-invite test passes against the
broken code and proves nothing — that is the entire reason this file exists.

HOW TO RUN:
    DB_NAME=... python -m pytest users/tests/test_mobile_uniqueness.py -v
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

_seq = itertools.count(1)


def _tenant(tag: str = "org"):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{tag}-{uuid.uuid4().hex[:6]}", is_active=True)


def _role(tenant, slug: str = "manager"):
    from tenants.models import TenantRole

    return TenantRole.objects.get(tenant=tenant, slug=slug)


def _invite(tenant, *, first_name: str):
    """Invite somebody with no existing account, through the real service."""
    from tenants.services.member_service import add_member_to_tenant

    return add_member_to_tenant(
        tenant=tenant,
        email=f"{first_name.lower()}-{uuid.uuid4().hex[:8]}@example.invalid",
        role=_role(tenant),
        password="Str0ng!Passw0rd",  # noqa: S106 — throwaway test credential
        first_name=first_name,
        last_name="Tester",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. The defect
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_two_people_without_a_number_can_both_be_invited():
    """#360 itself. Before the fix the second call raised IntegrityError on
    ``users_user_mobile_key`` and the endpoint answered 500."""
    org = _tenant("client")

    _invite(org, first_name="Ada")
    _invite(org, first_name="Grace")

    from tenants.models import TenantUser

    assert TenantUser.objects.filter(tenant=org).count() == 2


@pytest.mark.django_db
def test_the_collision_was_global_so_two_organisations_are_tested_too():
    """The constraint is on ``users_user``, not scoped to a tenant. So the
    failure crossed customers: organisation B's first-ever invitation failed
    because organisation A had already spent the one empty string."""
    org_a = _tenant("a")
    org_b = _tenant("b")

    _invite(org_a, first_name="Ada")
    _invite(org_b, first_name="Grace")

    from tenants.models import TenantUser

    assert TenantUser.objects.filter(tenant=org_a).count() == 1
    assert TenantUser.objects.filter(tenant=org_b).count() == 1


@pytest.mark.django_db
def test_a_third_invitation_works_too():
    """Two could be a fluke of ordering; the column either holds many unknowns
    or it does not."""
    org = _tenant("client")

    for name in ("Ada", "Grace", "Katherine"):
        _invite(org, first_name=name)

    from tenants.models import TenantUser

    assert TenantUser.objects.filter(tenant=org).count() == 3


# ─────────────────────────────────────────────────────────────────────────────
# 2. What the fix must not have broken
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_unknown_number_is_null_and_not_an_empty_string():
    """The distinction the whole fix rests on. Postgres treats two NULLs as
    distinct and two empty strings as equal, so storing ``""`` would reintroduce
    the bug the moment a second row appeared."""
    org = _tenant("client")
    _invite(org, first_name="Ada")

    user = User.objects.get(first_name="Ada")
    assert user.mobile is None, f"expected NULL, got {user.mobile!r}"


@pytest.mark.django_db
def test_a_real_number_is_still_unique():
    """Nullable is not the same as unconstrained. Two people may both have no
    number; they may not both have the *same* number."""
    from django.db import IntegrityError

    number = f"+9198{next(_seq):08d}"
    User.objects.create(username=f"u1-{uuid.uuid4().hex[:6]}", email="u1@example.invalid", mobile=number)

    with pytest.raises(IntegrityError):
        User.objects.create(username=f"u2-{uuid.uuid4().hex[:6]}", email="u2@example.invalid", mobile=number)


@pytest.mark.django_db
def test_the_duplicate_mobile_refusal_on_tenant_creation_still_refuses():
    """``validate_new_tenant`` refuses a number that belongs to somebody else,
    on the grounds that "emails link accounts; mobiles cannot". Making the
    column nullable must not soften that for a real number."""
    # Django's ValidationError, not DRF's — ``validate_new_tenant`` is a service
    # function, not a serializer, and raises the framework-agnostic one.
    from django.core.exceptions import ValidationError

    from tenants.services.onboarding import validate_new_tenant

    number = f"+9198{next(_seq):08d}"
    User.objects.create(username=f"held-{uuid.uuid4().hex[:6]}", email="held@example.invalid", mobile=number)

    with pytest.raises(ValidationError) as caught:
        validate_new_tenant(
            name=f"New-{uuid.uuid4().hex[:6]}",
            owner_email=f"new-{uuid.uuid4().hex[:8]}@example.invalid",
            owner_mobile=number,
            temporary_password="Str0ng!Passw0rd",  # noqa: S106 — throwaway test credential
        )

    assert "owner_mobile" in str(caught.value)


@pytest.mark.django_db
def test_an_absent_number_serialises_as_empty_not_as_the_word_none():
    """``str(None)`` is the string "None", which would ship to a client as a
    phone number made of four letters. The response shape is unchanged from
    before the column became nullable."""
    from users.serializers import LoginPatchUserSerializer

    org = _tenant("client")
    _invite(org, first_name="Ada")
    user = User.objects.get(first_name="Ada")

    body = LoginPatchUserSerializer().to_representation(user)

    assert body["mobile"] == ""
    assert body["mobile"] != "None"
