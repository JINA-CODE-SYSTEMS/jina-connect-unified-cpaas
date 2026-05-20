"""Concurrent-qualification race test (#201 second review Tests gap).

Tapan's first review flagged a sequence-allocation race
(``count() + 1`` non-atomic). His second review acknowledged the
unit test for ``_allocate_event`` proved the sequential happy path
but called out that the actual concurrent race needs two DB
connections + ``threading.Thread``.

This test sets up exactly that: two threads, each opening its own
DB connection, simultaneously call ``_allocate_event`` on the same
lead. Without ``select_for_update`` the two would compute the same
``sequence`` and one event would be silently dropped via
``get_or_create``. With the lock, the threads serialise — one gets
sequence 1, the other gets sequence 2, both events land.

Requires ``django_db(transaction=True)`` because per-test transactions
isolate the lock from a second connection. Runs slow (seconds) so
isolated from the cheap unit-test suite. Skipped on SQLite — its
``BEGIN IMMEDIATE`` serialises every write so the race can't even
be reproduced.
"""

from __future__ import annotations

import threading

import pytest
from django.db import connection


@pytest.mark.django_db(transaction=True)
class TestSequenceRaceConcurrent:
    def test_two_threads_get_distinct_sequences(self, lead):
        if connection.vendor == "sqlite":
            pytest.skip("SQLite serialises writes; race cannot be reproduced.")

        from attribution.models import AttributionEvent
        from attribution.signals import _allocate_event

        results: list = []
        barrier = threading.Barrier(2)

        def _worker():
            # Wait at the barrier so both threads enter
            # ``_allocate_event`` as simultaneously as the scheduler
            # allows. select_for_update inside the helper serialises
            # them at the DB layer.
            barrier.wait()
            try:
                ev = _allocate_event(lead_pk=lead.pk, event_name="Lead")
                results.append(ev.sequence if ev is not None else None)
            finally:
                # Each thread must close its own connection or it
                # leaks into the test runner's connection pool.
                from django.db import connections

                connections.close_all()

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert sorted(results) == [1, 2], (
            f"Expected sequences [1, 2] after the race; got {results!r}. "
            "If both threads got the same sequence, select_for_update "
            "is missing from _allocate_event."
        )
        # Both rows must exist with distinct event_ids.
        events = AttributionEvent.objects.filter(lead=lead, event_name="Lead").order_by("sequence")
        assert events.count() == 2
        assert events[0].event_id != events[1].event_id
