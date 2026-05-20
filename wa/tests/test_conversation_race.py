"""Concurrent first-message race test for ``WaConversation``
(#201 third review Blocker #2).

Two simultaneous first-inbound webhooks for the same
``(wa_app, contact)`` used to both see "no open conversation" and
both ``INSERT``, producing two open rows. The partial UniqueConstraint
on ``(wa_app, contact) WHERE closed_at IS NULL`` now rejects the
loser's INSERT, and ``resolve_or_create`` catches ``IntegrityError``
+ re-reads the winner.

Mirrors the existing ``attribution/tests/test_sequence_race_concurrent.py``
pattern — Postgres-only; SQLite skipped because it serialises writes.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from django.db import connection


@pytest.mark.django_db(transaction=True)
class TestConversationFirstMessageRace:
    def test_two_workers_share_one_open_conversation(self, db):
        if connection.vendor == "sqlite":
            pytest.skip("SQLite serialises writes; cannot reproduce race.")

        from contacts.models import TenantContact
        from tenants.models import Tenant, TenantWAApp
        from wa.models import WaConversation
        from wa.services.conversations import resolve_or_create

        tenant = Tenant.objects.create(name=f"ConvRace-{uuid.uuid4().hex[:8]}")
        wa_app = TenantWAApp.objects.create(
            tenant=tenant,
            app_name="t-wa",
            app_id=f"app-{uuid.uuid4().hex[:8]}",
            app_secret="s",
            wa_number=f"+1415555{uuid.uuid4().int % 10000:04d}",
        )
        contact = TenantContact.objects.create(
            tenant=tenant,
            first_name="X",
            phone=f"+1415555{uuid.uuid4().int % 10000:04d}",
        )

        results: list = []
        errors: list = []
        barrier = threading.Barrier(2)

        def _worker():
            try:
                barrier.wait()
                conv = resolve_or_create(wa_app=wa_app, contact=contact)
                results.append(conv.id)
            except Exception as exc:  # noqa: BLE001
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
        assert len(results) == 2, f"both workers must return; got {results!r}"
        # Both workers must end up with the SAME conversation id — one
        # winner, one re-reads the winner.
        assert results[0] == results[1], (
            f"workers returned different conversation ids {results!r}; "
            "partial unique constraint or IntegrityError catch missing."
        )
        # And the DB has exactly ONE open conversation.
        open_count = WaConversation.objects.filter(wa_app=wa_app, contact=contact, closed_at__isnull=True).count()
        assert open_count == 1, (
            f"expected 1 open conversation; got {open_count}. Partial UniqueConstraint missing or wrong condition."
        )
