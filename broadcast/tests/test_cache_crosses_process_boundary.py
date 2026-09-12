"""One cache, not one per process (#315).

``CACHES`` was commented out in settings, so Django fell back to LocMemCache: a
dict in each process's own memory. Nothing announced that — every ``cache.set``
succeeded, and every ``cache.get`` in the *same* process read it back, so the
cache looked like it worked everywhere anyone tested it in one process.

The charge breakdown is the call site where that is a defect rather than a
degradation. ``compute_charge_breakdown_task`` runs in a Celery worker and
writes its result to ``charge_breakdown:<task id>``; ``charge_breakdown_status``
reads that key from the *web* process. Two processes, two dicts — so the read
never hit, the endpoint answered "processing" for as long as anyone cared to
poll, and the computed breakdown expired unread five minutes later.

These tests do the thing the bug is about: they run the work in a **real second
OS process** and read the result from this one. The cache is not mocked, because
a mocked cache is precisely the thing that cannot fail this way. With ``CACHES``
reverted to the default, the first test fails on the poll still answering 202.

HOW TO RUN:
    DB_NAME=jc315 python -m pytest broadcast/tests/test_cache_crosses_process_boundary.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.db import connection
from django.urls import reverse
from rest_framework.test import APIClient

REPO_ROOT = Path(__file__).resolve().parents[2]

POLL_URL = reverse("wa:wabroadcast-charge-breakdown-status")


# ─────────────────────────────────────────────────────────────────────────────
# Running code in another process, which is the whole point
# ─────────────────────────────────────────────────────────────────────────────

#: Stands in for a Celery worker. ``Task.apply(task_id=...)`` runs the task body
#: with that id in ``self.request``, which is what the task keys its cache entry
#: on — so this process writes exactly the key the web process will look for. A
#: real worker adds a broker hop and nothing else that matters here; what matters
#: is that the memory it writes into is not this test's memory.
_WORKER = """
import os, sys

os.environ["DJANGO_SETTINGS_MODULE"] = "jina_connect.settings"
# The test database, not the development one. decouple reads os.environ before
# any .env file, so this wins.
os.environ["DB_NAME"] = sys.argv[1]

import django
django.setup()

from broadcast.tasks import compute_charge_breakdown_task

compute_charge_breakdown_task.apply(
    kwargs={
        "wa_app_id": int(sys.argv[3]),
        "contact_ids": [int(c) for c in sys.argv[4].split(",") if c],
    },
    task_id=sys.argv[2],
    throw=True,
)
"""

#: A second process spending from the same per-app send budget.
_PACER = """
import json, os, sys

os.environ["DJANGO_SETTINGS_MODULE"] = "jina_connect.settings"
os.environ["DB_NAME"] = sys.argv[1]

import django
django.setup()

from django.test import override_settings
from sms.services.rate_limiter import check_rate_limit

app_id, limit, attempts = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
with override_settings(PLATFORM_RATE_LIMITS={"sms": limit}):
    print(json.dumps([check_rate_limit(app_id) for _ in range(attempts)]))
"""


def _in_another_process(script: str, *args: str) -> str:
    """Run *script* in a fresh interpreter and return its stdout.

    The child inherits this process's environment — same REDIS_URL, same
    FIELD_ENCRYPTION_KEY — and is handed the test database name, so it is a
    faithful stand-in for a worker on the same deployment.

    ``CACHE_KEY_PREFIX`` is passed across explicitly because the child is told to
    use the *test* database, and the prefix defaults to the database name. In
    production a worker and the web process read the same ``DB_NAME`` and so
    agree on the prefix for free; here the parent's was frozen at settings-import
    time, before Django renamed the database for the test run. Saying it out loud
    is what makes the child the same deployment rather than a neighbouring one.
    """
    env = os.environ.copy()
    # ``.get`` rather than ``[...]``: with CACHES reverted to the default there
    # is no prefix, and this helper must not be what fails — the poll assertion
    # downstream is the one that should say what broke.
    env["CACHE_KEY_PREFIX"] = django_settings.CACHES["default"].get("KEY_PREFIX", "")
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c", script, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"worker process failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    return proc.stdout


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"Cache315-{uuid.uuid4().hex[:8]}")


@pytest.fixture()
def wa_app(tenant):
    from tenants.models import TenantWAApp

    return TenantWAApp.objects.create(
        tenant=tenant,
        app_name="cache315",
        app_id=f"app-{uuid.uuid4().hex[:8]}",
        app_secret="secret",
        wa_number=f"+1415556{uuid.uuid4().int % 10000:04d}",
    )


@pytest.fixture()
def contacts(tenant):
    from contacts.models import TenantContact

    return [
        TenantContact.objects.create(
            tenant=tenant,
            first_name=f"Cache{i}",
            phone=f"+91987650{i:04d}",
        )
        for i in range(3)
    ]


@pytest.fixture()
def owner_client(tenant):
    """An owner of *tenant*, who holds ``broadcast.charge_breakdown``.

    The Tenant post_save signal seeds the five default roles and their
    permission rows, so the owner role already grants everything.
    """
    from django.contrib.auth import get_user_model

    from tenants.models import TenantRole, TenantUser

    User = get_user_model()
    user = User.objects.create_user(
        username=f"cache315-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@cache315.local",
        mobile=f"+91911099{uuid.uuid4().int % 10000:04d}",
        password="testpass123",  # noqa: S106 — test fixture
    )
    role = TenantRole.objects.get(tenant=tenant, slug="owner")
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)

    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# The defect: a result computed in a worker was unreadable from the web process
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_a_breakdown_computed_in_another_process_is_returned_by_the_poll(wa_app, contacts, owner_client):
    """End to end across a process boundary, which is where this used to die.

    ``transaction=True`` because the rows have to be *committed* for another
    connection to see them — the worker is not inside this test's transaction.
    """
    task_id = f"cache315-{uuid.uuid4()}"
    cache_key = f"charge_breakdown:{task_id}"
    cache.delete(cache_key)

    # Nothing there yet, so the poll says "still working on it" — which is also
    # the answer it used to give forever.
    pending = owner_client.post(POLL_URL, {"task_id": task_id}, format="json")
    assert pending.status_code == 202, pending.data
    assert pending.data["status"] == "processing"

    # The worker computes and caches.
    _in_another_process(
        _WORKER,
        connection.settings_dict["NAME"],
        task_id,
        str(wa_app.id),
        ",".join(str(c.id) for c in contacts),
    )

    try:
        response = owner_client.post(POLL_URL, {"task_id": task_id}, format="json")

        # Under LocMemCache this is the 202 from above: the worker's write went
        # into a dict that died with the worker.
        assert response.status_code == 200, f"the poll did not see the worker's result: {response.data}"
        assert response.data["contact_summary"]["total_contacts"] == len(contacts)
        assert response.data["summary"]["total_countries"] == 1
        assert response.data["country_breakdown"][0]["country"] == "IN"
        # The endpoint enriches the worker's payload with live wallet state; an
        # owner is above the role priority that gates it.
        assert "balance" in response.data
    finally:
        cache.delete(cache_key)


@pytest.mark.django_db(transaction=True)
def test_a_failed_breakdown_surfaces_as_failed_rather_than_processing_forever(wa_app, owner_client):
    """The task's error branch caches too, and that write has to cross over as well.

    A task that died used to be indistinguishable from one still running, for
    the same reason a finished one was: its "failed" marker never left the
    worker either.
    """
    task_id = f"cache315-{uuid.uuid4()}"
    cache_key = f"charge_breakdown:{task_id}"
    cache.delete(cache_key)

    # A wa_app id that does not exist makes the task take its except branch,
    # cache {"status": "failed"} and re-raise; ``throw=False`` keeps the
    # re-raise from failing the child process, the way a worker would log it.
    _in_another_process(
        _WORKER.replace("throw=True", "throw=False"),
        connection.settings_dict["NAME"],
        task_id,
        str(wa_app.id + 10_000_000),
        "",
    )

    try:
        response = owner_client.post(POLL_URL, {"task_id": task_id}, format="json")
        assert response.status_code == 500, response.data
        assert response.data["status"] == "failed"
    finally:
        cache.delete(cache_key)


# ─────────────────────────────────────────────────────────────────────────────
# The degradation: a limit of N was enforced as N × workers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_rate_limit_of_n_is_n_in_total_and_not_n_per_process(settings):
    """Two processes, one budget.

    The SMS limiter stands in for the family — sms, rcs, telegram and the
    broadcast pacing from #271 all use the same ``cache.add`` + ``cache.incr``
    pattern against a key scoped to the sending app. Per process, three workers
    under a limit of two sent six.
    """
    from sms.services.rate_limiter import check_rate_limit

    app_id = f"cache315-{uuid.uuid4().hex}"
    limit = 2
    settings.PLATFORM_RATE_LIMITS = {**django_settings.PLATFORM_RATE_LIMITS, "sms": limit}

    try:
        # This process spends the whole budget.
        assert [check_rate_limit(app_id) for _ in range(limit)] == [True, True]

        # A second process finds nothing left. Under LocMemCache it found a
        # fresh counter and happily allowed another ``limit`` sends.
        elsewhere = json.loads(_in_another_process(_PACER, connection.settings_dict["NAME"], app_id, str(limit), "3"))
        assert elsewhere == [False, False, False], f"a second process got its own budget: {elsewhere}"

        # And this process still agrees the window is spent.
        assert check_rate_limit(app_id) is False
    finally:
        cache.delete(f"sms:rate:{app_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Where the cache lives
# ─────────────────────────────────────────────────────────────────────────────


def test_the_default_cache_is_redis():
    assert django_settings.CACHES["default"]["BACKEND"] == "django_redis.cache.RedisCache"
    assert django_settings.CACHES["default"]["LOCATION"].startswith(("redis://", "rediss://", "unix://"))


def test_the_cache_has_its_own_redis_database():
    """A FLUSHDB on the channel layer or the broker must not take the cache with it.

    Both of those live on the database ``REDIS_URL`` names; the cache must not.
    """
    cache_db = urlsplit(django_settings.CACHE_URL)
    shared_db = urlsplit(django_settings.REDIS_URL)

    assert cache_db.path != shared_db.path
    assert cache_db.path == f"/{django_settings.CACHE_REDIS_DB}"
    # Same server, so the separation is a database number and not a second box
    # somebody has to remember to provision.
    assert cache_db.netloc == shared_db.netloc


def test_a_cache_write_does_not_appear_on_the_channel_layers_database():
    """The separation asserted above, observed rather than parsed."""
    import redis

    name = f"cache315-probe-{uuid.uuid4().hex}"
    cache.set(name, "present", 60)
    try:
        assert cache.get(name) == "present"
        assert redis.Redis.from_url(django_settings.CACHE_URL).keys(f"*{name}*")
        assert not redis.Redis.from_url(django_settings.REDIS_URL).keys(f"*{name}*")
    finally:
        cache.delete(name)


def test_cache_keys_carry_a_deployment_discriminator():
    """``broadcast:pace:1`` means a different number in staging than in production.

    Nothing in any cache key said which deployment wrote it — the process
    boundary used to. Two stacks on one Redis would otherwise share rate-limit
    counters and capability caches.
    """
    import redis

    prefix = django_settings.CACHES["default"].get("KEY_PREFIX")
    assert prefix, "the default cache has no KEY_PREFIX"

    name = f"cache315-prefix-{uuid.uuid4().hex}"
    cache.set(name, 1, 60)
    try:
        keys = redis.Redis.from_url(django_settings.CACHE_URL).keys(f"*{name}*")
        assert keys, "key not found on the cache database"
        assert prefix.encode() in keys[0]
    finally:
        cache.delete(name)
