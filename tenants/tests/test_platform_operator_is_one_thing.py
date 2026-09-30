"""A platform operator is one thing, and the host dashboard should agree.

Reported from the live host panel (jain-t/jina-connect-web#691): an operator
opened Add organisation, filled it in, and got "You do not have permission to
perform this action." — DRF's own wording for a failed ``IsAdminUser``.

Django's two flags are independent. ``is_superuser`` does not set
``is_staff``, and ``IsAdminUser`` reads ``is_staff`` alone. Everything else in
this codebase that asks "is this caller the platform?" reads ``is_superuser``:
the RBAC bypass in ``TenantRolePermission``, ``acting_as_platform_operator``,
the impersonation guard, the token claim — and the frontend, whose middleware
sends ``is_superuser`` accounts to /host and everyone else away from it.

So an account granted ``is_superuser`` alone is shown the entire host
dashboard and refused by every write in it. ``createsuperuser`` sets both
flags, which is why this held for as long as the only operators were made on
the command line.

The same split was already found and fixed once, a few lines away, in
``get_serializer_class`` — where reading only ``is_staff`` served the limited
serializer and showed every tenant's balance as zero. The write path kept the
narrow reading.

HOW TO RUN:
    .venv/bin/python -m pytest tenants/tests/test_platform_operator_is_one_thing.py -v
"""

from __future__ import annotations

import itertools

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser

pytestmark = pytest.mark.django_db

User = get_user_model()

_seq = itertools.count(1)

ADMIN_CREATE_URL = "/tenants/admin-create/"
HOST_WALLET_URL = "/tenants/host-wallet/dashboard/"
BRANDING_URL = "/tenants/branding/"


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _user(*, superuser=False, staff=False):
    n = next(_seq)
    user = User.objects.create_user(
        username=f"u{n}",
        email=f"u{n}@test.invalid",
        password="x",  # noqa: S106
        mobile=f"+9190000{n:05d}",
    )
    user.is_superuser = superuser
    user.is_staff = staff
    user.save(update_fields=["is_superuser", "is_staff"])
    return user


def _tenant_owner():
    """Someone with the highest role inside an organisation — still not the host."""
    n = next(_seq)
    tenant = Tenant.objects.create(name=f"Org {n}")
    user = _user()
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)
    return user


def _client(user=None):
    client = APIClient()
    if user:
        client.force_authenticate(user=user)
    return client


def _payload(**over):
    n = next(_seq)
    body = {
        "name": f"Wired Co {n}",
        "owner_email": f"owner{n}@acme.test",
        "owner_mobile": f"+9190001{n:05d}",
        "temporary_password": "TempPass!2026",  # noqa: S106
    }
    body.update(over)
    return body


# ─────────────────────────────────────────────────────────────────────────────
# The report
# ─────────────────────────────────────────────────────────────────────────────


def test_a_superuser_can_onboard_an_organisation():
    """The reported failure: shown the host panel, refused by the host API."""
    response = _client(_user(superuser=True)).post(ADMIN_CREATE_URL, _payload(), format="json")

    assert response.status_code == 201


def test_a_staff_account_still_can():
    """Preserved. ``createsuperuser`` sets both flags; nothing here narrows that."""
    response = _client(_user(staff=True)).post(ADMIN_CREATE_URL, _payload(), format="json")

    assert response.status_code == 201


def test_an_account_carrying_both_flags_still_can():
    response = _client(_user(superuser=True, staff=True)).post(ADMIN_CREATE_URL, _payload(), format="json")

    assert response.status_code == 201


# ─────────────────────────────────────────────────────────────────────────────
# And nobody else
# ─────────────────────────────────────────────────────────────────────────────


def test_an_organisation_owner_cannot_create_organisations():
    """The highest role inside a tenant is still a tenant, not the platform."""
    response = _client(_tenant_owner()).post(ADMIN_CREATE_URL, _payload(), format="json")

    assert response.status_code == 403
    assert not Tenant.objects.filter(name__startswith="Wired Co").exists()


def test_an_ordinary_account_cannot():
    response = _client(_user()).post(ADMIN_CREATE_URL, _payload(), format="json")

    assert response.status_code == 403


def test_an_anonymous_caller_cannot():
    response = _client().post(ADMIN_CREATE_URL, _payload(), format="json")

    assert response.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# The rest of the host dashboard the same account is shown
# ─────────────────────────────────────────────────────────────────────────────


def test_the_host_dashboard_is_readable_by_a_superuser():
    """Every /host page loads through these; a 403 here is a wall of failures."""
    response = _client(_user(superuser=True)).get(HOST_WALLET_URL)

    assert response.status_code != 403


def test_white_labelling_can_be_changed_by_a_superuser():
    """Reading branding is deliberately public — the login page needs the
    favicon and product name before anyone has signed in. Changing it is the
    host operation, and it was behind the same narrow flag."""
    response = _client(_user(superuser=True)).post(BRANDING_URL, {"product_name": "Renamed"}, format="json")

    assert response.status_code != 403


def test_white_labelling_cannot_be_changed_by_an_organisation_owner():
    response = _client(_tenant_owner()).post(BRANDING_URL, {"product_name": "Renamed"}, format="json")

    assert response.status_code == 403
