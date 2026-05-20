"""Concurrent ``flush_capi_queue`` worker-dedup test (#201 round-2 Low #3).

Tapan's round-2 review noted that the unit suite for
``flush_capi_queue`` covered only the single-worker happy path — the
actual race the High #3 fix prevents (two Celery workers both picking
up the same pending row and double-POSTing to CAPI) had no direct
test.

This test mirrors ``test_sequence_race_concurrent.py``: two threads
each open their own DB connection and invoke ``flush_capi_queue``
under a barrier. Without ``select_for_update(skip_locked=True)`` they
would both pick up the same pending event and double-call the CAPI
stub. With the lock, the second worker's SELECT skips the locked row
and one POST happens.

Requires Postgres + ``django_db(transaction=True)``. Skipped on
SQLite — SQLite serialises writes so the race can't be reproduced.
"""

from __future__ import annotations

import threading
from collections import Counter
from unittest.mock import patch

import pytest
from django.db import connection

from attribution.models import AttributionEvent, CapiStatus
from attribution.signals import _allocate_event


@pytest.mark.django_db(transaction=True)
class TestFlushCapiQueueConcurrent:
    def test_two_workers_each_post_exactly_once(self, lead):
        if connection.vendor == "sqlite":
            pytest.skip("SQLite serialises writes; cannot reproduce race.")

        # Seed two pending events so each worker has something to pick
        # up — proves they pick disjoint sets, not that one steals from
        # the other and the second worker idles.
        a = _allocate_event(lead_pk=lead.pk, event_name="Lead")
        # Force a second event for the same lead by allocating against
        # event_name="Purchase".
        b = _allocate_event(
            lead_pk=lead.pk,
            event_name="Purchase",
            extra_defaults={"event_value_minor": 1000, "currency": "USD"},
        )
        assert {a.capi_status, b.capi_status} == {CapiStatus.PENDING}

        from attribution.tasks import flush_capi_queue

        # Count how many times the CAPI HTTP stub is invoked across
        # both workers. With the lock, total == 2 (each event hit
        # exactly once). Without the lock, two workers can each grab
        # both events and total can hit 4.
        call_count = Counter()
        call_lock = threading.Lock()

        def fake_post(payload, *, lead):
            with call_lock:
                call_count[payload["event_id"]] += 1
            return {"events_received": [{"matching_score": 8}]}

        barrier = threading.Barrier(2)
        errors: list = []

        def _worker():
            try:
                barrier.wait()
                with patch("attribution.tasks._post_to_capi", side_effect=fake_post):
                    flush_capi_queue()
            except Exception as exc:  # noqa: BLE001 — surface to assertion
                errors.append(exc)
            finally:
                from django.db import connections

                connections.close_all()

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors, f"worker(s) raised: {errors!r}"

        # Each event must have been POSTed at most once. The two
        # workers together should account for 2 POST calls; if either
        # event was POSTed twice the lock is broken.
        assert max(call_count.values(), default=0) == 1, (
            f"At least one event was POSTed more than once: {dict(call_count)!r}. "
            "select_for_update(skip_locked=True) missing or broken in flush_capi_queue."
        )
        assert sum(call_count.values()) == 2, (
            f"Expected 2 total POST calls across both workers; got {sum(call_count.values())}. "
            f"Per-event: {dict(call_count)!r}"
        )

        # Both events must end in SENT.
        for event in AttributionEvent.objects.filter(pk__in=[a.pk, b.pk]):
            assert event.capi_status == CapiStatus.SENT
