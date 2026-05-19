"""Signal handlers that fire AttributionEvents on lead lifecycle (#197).

The CAPI Lead event must fire on the qualification transition, never
on first inbound message — the most common CAPI implementation
mistake. We watch ``CtwaLead`` post_save and emit an
``AttributionEvent(event_name='Lead')`` when ``qualification_status``
flips to ``qualified``.

For ``Purchase`` events, ``razorpay/`` (existing) wires a separate
post_save handler when a CTWA-attributed payment completes — wiring
left as a TODO for the razorpay maintainer once that integration is
in place. Stub provided here as :func:`enqueue_purchase` so flow
nodes can drive it manually before then.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from attribution.models import AttributionEvent
from ctwa.models import CtwaLead

logger = logging.getLogger(__name__)


@receiver(post_save, sender=CtwaLead)
def fire_lead_event_on_qualification(sender, instance: CtwaLead, created, **_kw):
    """When a ``CtwaLead.qualification_status`` flips to ``qualified``,
    create the corresponding ``AttributionEvent(Lead)`` once.

    Idempotent — guarded by the ``unique(lead, event_name, sequence)``
    constraint on AttributionEvent. The sequence starts at 1 for the
    first qualification and increments on each subsequent transition
    (a contact qualifying, going cold, qualifying again).
    """
    if created:
        # First save = lead just created in `new` status. No transition.
        return
    if instance.qualification_status != "qualified":
        return

    sequence = AttributionEvent.objects.filter(lead=instance, event_name="Lead").count() + 1
    event_id = f"{instance.id}-Lead-{sequence}"

    AttributionEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "lead": instance,
            "event_name": "Lead",
            "event_time": timezone.now(),
            "sequence": sequence,
        },
    )


def enqueue_purchase(*, lead: CtwaLead, value_minor: int, currency: str) -> AttributionEvent:
    """Explicit entry point for purchase events. Called by the razorpay
    post-payment hook once CTWA-attribution is wired into payments."""
    sequence = AttributionEvent.objects.filter(lead=lead, event_name="Purchase").count() + 1
    event, _ = AttributionEvent.objects.get_or_create(
        event_id=f"{lead.id}-Purchase-{sequence}",
        defaults={
            "lead": lead,
            "event_name": "Purchase",
            "event_time": timezone.now(),
            "event_value_minor": value_minor,
            "currency": currency,
            "sequence": sequence,
        },
    )
    return event
