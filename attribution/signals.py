"""Signal handlers that fire AttributionEvents on lead lifecycle (#197).

The CAPI Lead event must fire on the qualification transition, never
on first inbound message — the most common CAPI implementation
mistake. We watch ``CtwaLead`` post_save and emit an
``AttributionEvent(event_name='Lead')`` when ``qualification_status``
flips to ``qualified``.

Sequence allocation is serialised via ``select_for_update`` on the
``CtwaLead`` row inside an ``atomic`` block — without this lock two
concurrent qualification transitions both read ``count()=N`` and both
build ``event_id="…-Lead-N+1"``, then the second ``get_or_create`` sees
the existing event and silently no-ops. That undercounts CAPI events
with no error logged. (#201 review)

For ``Purchase`` events, ``razorpay/`` (existing) wires a separate
post_save handler when a CTWA-attributed payment completes — wiring
left as a TODO for the razorpay maintainer once that integration is
in place. Stub provided here as :func:`enqueue_purchase` so flow
nodes can drive it manually before then.
"""

from __future__ import annotations

import logging
import weakref

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from attribution.models import AttributionEvent
from ctwa.models import CtwaLead

logger = logging.getLogger(__name__)

# Pre-save status snapshot. ``WeakKeyDictionary`` keyed on the
# ``CtwaLead`` instance so Python's GC drops entries automatically
# when the instance is collected — no leaks on save() exceptions
# (v1 used a plain ``dict[id(instance), str]`` which both leaked
# entries and risked collisions when ``id()`` was reused after GC).
# (#201 second review Medium #7)
#
# Workers MUST NOT hot-reload this module — doing so reinitialises
# the table and breaks the pre/post pairing for any in-flight save.
_PRESAVE_QUALIFICATION_STATUS: "weakref.WeakKeyDictionary[CtwaLead, str | None]" = weakref.WeakKeyDictionary()


def _allocate_event(
    *,
    lead_pk,
    event_name: str,
    extra_defaults: dict | None = None,
) -> AttributionEvent | None:
    """Atomically allocate the next sequence number for ``(lead, event_name)``
    and create the ``AttributionEvent`` row. Returns the new event, or
    ``None`` when the lead row has vanished between caller and lock.

    The whole block runs inside ``transaction.atomic`` with a row-level
    lock on the ``CtwaLead`` to prevent the sequence race the v1
    implementation had. Other concurrent workers wait on the lock and
    see the correct count after release.
    """
    with transaction.atomic():
        # ``select_for_update`` blocks other transactions trying to
        # update the same lead — the lock is released on transaction
        # commit. count() then sees every previously-committed event.
        try:
            locked = CtwaLead.objects.select_for_update().only("id").get(pk=lead_pk)
        except CtwaLead.DoesNotExist:
            return None

        sequence = AttributionEvent.objects.filter(lead=locked, event_name=event_name).count() + 1
        event_id = f"{locked.id}-{event_name}-{sequence}"

        defaults = {
            "lead": locked,
            "event_name": event_name,
            "event_time": timezone.now(),
            "sequence": sequence,
        }
        if extra_defaults:
            defaults.update(extra_defaults)

        event, _ = AttributionEvent.objects.get_or_create(event_id=event_id, defaults=defaults)
        return event


@receiver(pre_save, sender=CtwaLead)
def _capture_prev_qualification(sender, instance: CtwaLead, **_kw):
    """Snapshot the persisted ``qualification_status`` before save so
    :func:`fire_lead_event_on_qualification` can tell whether this save
    is a real transition (e.g. ``new → qualified``) or a no-op re-save
    at the same value. Without this, re-saving a qualified lead would
    keep allocating new Lead events. (#201 review)"""
    if not instance.pk:
        _PRESAVE_QUALIFICATION_STATUS[instance] = None
        return
    try:
        prev = CtwaLead.objects.only("qualification_status").get(pk=instance.pk)
        _PRESAVE_QUALIFICATION_STATUS[instance] = prev.qualification_status
    except CtwaLead.DoesNotExist:
        _PRESAVE_QUALIFICATION_STATUS[instance] = None


@receiver(post_save, sender=CtwaLead)
def fire_lead_event_on_qualification(sender, instance: CtwaLead, created, **_kw):
    """When a ``CtwaLead.qualification_status`` flips to ``qualified``,
    create the corresponding ``AttributionEvent(Lead)`` once.

    Idempotent on re-save: relies on the pre_save snapshot to detect
    whether ``qualification_status`` actually changed. Resaving a
    qualified lead at the same value is a no-op; a true
    disqualify→qualify cycle allocates a fresh sequence.
    """
    prev = _PRESAVE_QUALIFICATION_STATUS.pop(instance, None)

    if created:
        return
    if instance.qualification_status != "qualified":
        return
    if prev == "qualified":
        # No transition — same value on both sides of the save.
        return

    _allocate_event(lead_pk=instance.pk, event_name="Lead")


def enqueue_purchase(*, lead: CtwaLead, value_minor: int, currency: str) -> AttributionEvent | None:
    """Explicit entry point for purchase events. Called by the razorpay
    post-payment hook once CTWA-attribution is wired into payments."""
    return _allocate_event(
        lead_pk=lead.pk,
        event_name="Purchase",
        extra_defaults={
            "event_value_minor": value_minor,
            "currency": currency,
        },
    )
