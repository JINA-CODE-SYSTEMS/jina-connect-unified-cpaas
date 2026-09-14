"""A platform operator's wallet view must not depend on ``is_staff``.

The wallet is three columns on the organisation row — ``balance``,
``credit_line``, ``threshold_alert`` — and ``TenantLimitedSerializer``
excludes all of them. Which serializer a caller gets is therefore the whole
question of whether they see a wallet at all.

Every other decision about "is this caller a platform operator" reads
``is_superuser``: the permission bypass in ``TenantRolePermission``,
``acting_as_platform_operator``, ``TenantTransactionViewSet.get_queryset``,
the impersonation guard, and the token claim itself.
``TenantViewSet.get_serializer_class`` read ``is_staff`` instead, so two
accounts that are both "platform admin, no organisation" could disagree about
whether the product has a wallet.

Every existing test in this repository creates its operators with
``is_superuser=True, is_staff=True`` together, which is exactly why nothing
caught it.

HOW TO RUN:
    python -m pytest tenants/tests/test_platform_operator_sees_the_wallet.py -v
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser
from users.models import User

pytestmark = pytest.mark.django_db

WALLET_FIELDS = ("balance", "credit_line", "threshold_alert")

_seq = itertools.count(1)


def _tenant(name="Wallet Co", balance="250.00"):
    tenant = Tenant.objects.create(name=f"{name}-{next(_seq)}")
    Tenant.objects.filter(pk=tenant.pk).update(balance=Decimal(balance), balance_currency="USD")
    return tenant


def _user(*, is_superuser=False, is_staff=False):
    n = next(_seq)
    return User.objects.create_user(
        username=f"u{n}",
        email=f"u{n}@test.invalid",
        password="x",
        mobile=f"+9190000{n:05d}",
        is_superuser=is_superuser,
        is_staff=is_staff,
    )


def _rows(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/tenants/")
    assert response.status_code == 200, response.data
    payload = response.data
    return payload["results"] if isinstance(payload, dict) and "results" in payload else payload


def _wallet_fields_present(row) -> set[str]:
    return {name for name in WALLET_FIELDS if name in row}


# ─────────────────────────────────────────────────────────────────────────────
# The report: two accounts, both "platform admin, no organisation"
# ─────────────────────────────────────────────────────────────────────────────


def test_a_superuser_who_is_not_staff_still_sees_the_wallet():
    """The defect. Nothing about holding a wallet is a question of ``is_staff``."""
    _tenant()
    operator = _user(is_superuser=True, is_staff=False)

    rows = _rows(operator)

    assert rows, "an operator outside every organisation still lists them"
    assert _wallet_fields_present(rows[0]) == set(WALLET_FIELDS), rows[0]


def test_two_platform_operators_are_shown_the_same_thing():
    """The symptom as reported: same standing, different wallet.

    Asserted as an equality between the two responses rather than against a
    list of field names, because the complaint was not "a field is missing" —
    it was that two accounts of the same standing disagreed.
    """
    _tenant()
    staff_operator = _user(is_superuser=True, is_staff=True)
    plain_operator = _user(is_superuser=True, is_staff=False)

    assert set(_rows(plain_operator)[0]) == set(_rows(staff_operator)[0])


def test_staff_alone_does_not_get_past_rbac_on_this_endpoint():
    """Why the old ``is_staff`` test could only ever change a superuser's answer.

    Written after asserting the opposite and being told otherwise by the
    endpoint. ``TenantRolePermission`` bypasses on ``is_superuser`` and nothing
    else, so a staff account with no membership is refused before any
    serializer is chosen — which means the ``is_staff`` branch decided the
    wallet *only* for callers who are also superusers, and for them it was
    deciding it wrongly.

    Pinned rather than deleted: it is the reason the ``or`` in the fix cannot
    be simplified to ``is_superuser`` alone without checking this first.
    """
    _tenant()
    host = _user(is_superuser=False, is_staff=True)

    client = APIClient()
    client.force_authenticate(user=host)

    assert client.get("/tenants/").status_code == 403


def test_staff_who_is_also_a_superuser_is_unaffected():
    """The account shape that worked before must still work."""
    _tenant()
    host = _user(is_superuser=True, is_staff=True)

    assert _wallet_fields_present(_rows(host)[0]) == set(WALLET_FIELDS)


# ─────────────────────────────────────────────────────────────────────────────
# What must NOT change: #251's role gate inside an organisation
# ─────────────────────────────────────────────────────────────────────────────


def test_a_member_below_admin_still_has_the_wallet_hidden():
    """#251. Widening the operator test must not widen the member test."""
    tenant = _tenant()
    member = _user()
    TenantUser.objects.create(tenant=tenant, user=member, role=TenantRole.objects.get(tenant=tenant, slug="agent"))
    member.tenant_id = tenant.pk

    rows = _rows(member)

    assert rows
    assert _wallet_fields_present(rows[0]) == set(), rows[0]


def test_an_owner_still_sees_their_own_wallet():
    tenant = _tenant()
    owner = _user()
    TenantUser.objects.create(tenant=tenant, user=owner, role=TenantRole.objects.get(tenant=tenant, slug="owner"))
    owner.tenant_id = tenant.pk

    assert _wallet_fields_present(_rows(owner)[0]) == set(WALLET_FIELDS)
