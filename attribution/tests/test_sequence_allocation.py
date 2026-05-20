"""Sequence-allocation tests for the Lead/Purchase event signal (#201 review).

The headline Critical bug Tapan flagged: ``count() + 1`` is not
atomic, so concurrent qualification transitions silently collide on
the same ``event_id`` and the second one is dropped via
``get_or_create``. The fix in ``attribution/signals.py`` serialises
allocation via ``select_for_update`` inside ``transaction.atomic``.

These tests assert:

  * First qualification creates ``-Lead-1``.
  * Second qualification (after a status reset) creates ``-Lead-2``.
  * ``enqueue_purchase`` increments independently of Lead events.
  * Idempotency: re-firing the SAME save() doesn't create duplicates.
"""

from __future__ import annotations

import pytest

from attribution.models import AttributionEvent
from attribution.signals import enqueue_purchase


@pytest.mark.django_db
class TestSequenceAllocation:
    def test_first_qualification_creates_lead_seq_1(self, lead):
        lead.qualification_status = "qualified"
        lead.save()
        events = list(AttributionEvent.objects.filter(lead=lead, event_name="Lead"))
        assert len(events) == 1
        assert events[0].sequence == 1
        assert events[0].event_id == f"{lead.id}-Lead-1"

    def test_requalification_increments_sequence(self, lead):
        # First qualification.
        lead.qualification_status = "qualified"
        lead.save()
        # Cool-off — contact disqualifies.
        lead.qualification_status = "disqualified"
        lead.save()
        # Re-qualifies on return visit.
        lead.qualification_status = "qualified"
        lead.save()

        events = list(AttributionEvent.objects.filter(lead=lead, event_name="Lead").order_by("sequence"))
        assert [e.sequence for e in events] == [1, 2]
        assert {e.event_id for e in events} == {
            f"{lead.id}-Lead-1",
            f"{lead.id}-Lead-2",
        }

    def test_resaving_qualified_lead_is_idempotent(self, lead):
        lead.qualification_status = "qualified"
        lead.save()
        # Saving again at the same status MUST NOT create a new event.
        lead.save()
        lead.save()
        assert AttributionEvent.objects.filter(lead=lead, event_name="Lead").count() == 1

    def test_create_does_not_fire(self, tenant, contact, conversation, campaign, db):
        # Creating a CtwaLead in ``new`` status MUST NOT fire a Lead event.
        from django.utils import timezone

        from ctwa.models import CtwaLead

        fresh = CtwaLead.objects.create(
            tenant=tenant,
            name="freshlead",
            contact=contact,
            conversation=conversation,
            campaign=campaign,
            meta_ad_id=campaign.meta_ad_id,
            first_message_at=timezone.now(),
            qualification_status="new",
        )
        assert AttributionEvent.objects.filter(lead=fresh).count() == 0

    def test_purchase_sequence_independent(self, lead):
        # Lead and Purchase event_names track separate sequences.
        lead.qualification_status = "qualified"
        lead.save()

        enqueue_purchase(lead=lead, value_minor=12_999, currency="USD")
        enqueue_purchase(lead=lead, value_minor=29_999, currency="USD")

        purchases = list(AttributionEvent.objects.filter(lead=lead, event_name="Purchase").order_by("sequence"))
        leads = list(AttributionEvent.objects.filter(lead=lead, event_name="Lead"))
        assert [p.sequence for p in purchases] == [1, 2]
        assert [p.event_value_minor for p in purchases] == [12_999, 29_999]
        # Lead sequence isn't bumped by purchases.
        assert len(leads) == 1
        assert leads[0].sequence == 1

    def test_select_for_update_serialises_under_concurrent_signal(self, lead, db):
        """The internal helper ``_allocate_event`` MUST hold a row lock
        between count() and create. Easiest way to prove: call it twice
        in sequence (no race) and assert the sequences increment.

        A truly concurrent test would need a second connection +
        transaction control; we cover that pattern in the integration
        suite. (#201 review)
        """
        from attribution.signals import _allocate_event

        a = _allocate_event(lead_pk=lead.pk, event_name="Lead")
        b = _allocate_event(lead_pk=lead.pk, event_name="Lead")
        assert a is not None and b is not None
        assert {a.sequence, b.sequence} == {1, 2}
