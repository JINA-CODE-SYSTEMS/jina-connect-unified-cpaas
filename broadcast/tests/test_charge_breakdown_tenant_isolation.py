"""A task id is unique, but uniqueness is not authorisation (#320).

``compute_charge_breakdown_task`` writes its result to
``charge_breakdown:<celery task id>`` and ``charge_breakdown_status`` reads that
key back. The id is a UUID4, so the key needs no tenant discriminator to be
*unique* — and that is exactly why the missing ownership check was easy to miss.

It had also never mattered. With ``CACHES`` commented out the task wrote into a
Celery worker's own memory and the web process read its own, so **the read never
hit at all** (#315). The key was unreachable, so nobody could read anyone's
breakdown, their own included.

#315 points the cache at Redis. The read starts hitting, and in the same minute
the absent check starts mattering: any authenticated user holding
``broadcast.charge_breakdown`` who has a task id reads another tenant's contact
count, per-country rates and estimated spend. Task ids are not guessable, but
they are returned to clients by the endpoint that starts the computation, and
they land in logs — a reachable reference, not a secret.

The owning tenant now travels in the cached payload and the poll compares it to
the caller's, failing closed: an unresolvable caller, or a payload written before
this change and so carrying no tenant, reads as a miss.

These tests are meaningless against ``LocMemCache`` — there the read misses for
everyone and tenant B is "correctly" refused by accident. They assert the leak is
closed with the cache actually shared, which is the only state in which it exists.

HOW TO RUN:
    DB_NAME=jc320 python -m pytest broadcast/tests/test_charge_breakdown_tenant_isolation.py -v
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

POLL_URL = reverse("wa:wabroadcast-charge-breakdown-status")


# ─────────────────────────────────────────────────────────────────────────────
# Two tenants, each with an owner who holds broadcast.charge_breakdown
# ─────────────────────────────────────────────────────────────────────────────


def _make_tenant(label):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"Iso320-{label}-{uuid.uuid4().hex[:8]}")


def _make_wa_app(tenant):
    from tenants.models import TenantWAApp

    return TenantWAApp.objects.create(
        tenant=tenant,
        app_name="iso320",
        app_id=f"app-{uuid.uuid4().hex[:8]}",
        app_secret="secret",  # noqa: S106 — test fixture
        wa_number=f"+1415557{uuid.uuid4().int % 10000:04d}",
    )


def _make_owner_client(tenant):
    """An owner of *tenant*. The Tenant post_save signal seeds the five default
    roles, so the owner role already grants ``broadcast.charge_breakdown``."""
    from django.contrib.auth import get_user_model

    from tenants.models import TenantRole, TenantUser

    User = get_user_model()
    user = User.objects.create_user(
        username=f"iso320-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@iso320.local",
        mobile=f"+91911077{uuid.uuid4().int % 10000:04d}",
        password="testpass123",  # noqa: S106 — test fixture
    )
    role = TenantRole.objects.get(tenant=tenant, slug="owner")
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)

    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_contacts(tenant, n=3):
    from contacts.models import TenantContact

    return [
        TenantContact.objects.create(
            tenant=tenant,
            first_name=f"Iso{i}",
            phone=f"+91987630{i:04d}",
        )
        for i in range(n)
    ]


@pytest.fixture()
def two_tenants(db):
    a = _make_tenant("a")
    b = _make_tenant("b")
    return {
        "a": {"tenant": a, "wa_app": _make_wa_app(a), "client": _make_owner_client(a)},
        "b": {"tenant": b, "wa_app": _make_wa_app(b), "client": _make_owner_client(b)},
    }


def _run_breakdown(wa_app, contact_ids):
    """Run the real task in-process under a fresh id and return that id.

    In-process is deliberate: the cross-process half is #315's test. What is
    under test here is who may read the result, not where it was written.
    """
    from broadcast.tasks import compute_charge_breakdown_task

    task_id = f"iso320-{uuid.uuid4()}"
    compute_charge_breakdown_task.apply(
        kwargs={"wa_app_id": wa_app.id, "contact_ids": contact_ids},
        task_id=task_id,
        throw=True,
    )
    return task_id


# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db()
def test_the_owning_tenant_can_read_its_own_breakdown(two_tenants):
    """The happy path, so the refusal below is not simply everything failing."""
    a = two_tenants["a"]
    contacts = _make_contacts(a["tenant"])

    task_id = _run_breakdown(a["wa_app"], [c.id for c in contacts])

    response = a["client"].post(POLL_URL, {"task_id": task_id}, format="json")

    assert response.status_code == 200, response.data
    assert "balance" in response.data


@pytest.mark.django_db()
def test_another_tenant_cannot_read_it(two_tenants):
    """The leak, closed. Tenant B holds the permission and a valid task id."""
    a, b = two_tenants["a"], two_tenants["b"]
    contacts = _make_contacts(a["tenant"])

    task_id = _run_breakdown(a["wa_app"], [c.id for c in contacts])

    # The entry really is there — otherwise this test passes for the wrong reason.
    assert cache.get(f"charge_breakdown:{task_id}") is not None

    response = b["client"].post(POLL_URL, {"task_id": task_id}, format="json")

    assert response.status_code == 202, response.data
    assert response.data == {"status": "processing", "task_id": task_id}
    # None of A's figures came back under any key.
    assert "balance" not in response.data
    assert "total_cost" not in response.data


@pytest.mark.django_db()
def test_a_payload_written_before_this_change_reads_as_a_miss(two_tenants):
    """Fails closed. Entries already in Redis at deploy time carry no tenant."""
    a = two_tenants["a"]
    task_id = f"iso320-{uuid.uuid4()}"

    cache.set(
        f"charge_breakdown:{task_id}",
        json.dumps({"status": "completed", "result": {"total_cost": "999.00"}}),
        timeout=300,
    )

    response = a["client"].post(POLL_URL, {"task_id": task_id}, format="json")

    assert response.status_code == 202, response.data
    assert "total_cost" not in response.data


@pytest.mark.django_db()
def test_a_failure_is_tenant_checked_too(two_tenants):
    """The error branch caches as well, and its message can name internals."""
    a, b = two_tenants["a"], two_tenants["b"]
    task_id = f"iso320-{uuid.uuid4()}"

    cache.set(
        f"charge_breakdown:{task_id}",
        json.dumps(
            {
                "status": "failed",
                "tenant_id": a["tenant"].id,
                "error": "internal detail that is not B's business",
            }
        ),
        timeout=300,
    )

    # The owner sees its own failure.
    own = a["client"].post(POLL_URL, {"task_id": task_id}, format="json")
    assert own.status_code == 500
    assert own.data["status"] == "failed"

    # The other tenant does not.
    other = b["client"].post(POLL_URL, {"task_id": task_id}, format="json")
    assert other.status_code == 202
    assert "error" not in other.data


@pytest.mark.django_db()
def test_the_payload_carries_the_owning_tenant(two_tenants):
    """Pins the contract the poll depends on, so a future writer cannot quietly
    drop the field and silently reopen this — the read fails closed, so dropping
    it would look like "polling never completes" rather than like a leak."""
    a = two_tenants["a"]
    contacts = _make_contacts(a["tenant"])

    task_id = _run_breakdown(a["wa_app"], [c.id for c in contacts])

    payload = json.loads(cache.get(f"charge_breakdown:{task_id}"))
    assert payload["tenant_id"] == a["tenant"].id
