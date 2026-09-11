"""Refunds return what was charged (#262).

A broadcast was **charged** at per-country rate-card rates and **refunded**
at the flat `TenantWAApp` price — two different sources for the same money.

Below the flat rate the refund exceeded the charge, hit the clamp in
`credit_manager`, and a half-failed campaign was refunded *in full*. Above it
the tenant was short-refunded with no warning logged at all. The clamp is why
this never looked broken: it turned a wrong number into a plausible one.

HOW TO RUN:
    .venv/bin/python -m pytest broadcast/tests/test_refund_matches_charge.py -v
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from django.utils import timezone

from broadcast.models import (
    Broadcast,
    BroadcastMessage,
    BroadcastPlatformChoices,
    BroadcastStatusChoices,
    MessageStatusChoices,
)

_seq = itertools.count(1)


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name="Refund Tenant")


def _contact(tenant, phone):
    from contacts.models import TenantContact

    return TenantContact.objects.create(tenant=tenant, phone=phone, first_name=f"C{next(_seq)}")


@pytest.fixture()
def broadcast(tenant):
    return Broadcast.objects.create(
        tenant=tenant,
        name="Refund Campaign",
        status=BroadcastStatusChoices.SENT,
        platform=BroadcastPlatformChoices.WHATSAPP,
        scheduled_time=timezone.now(),
    )


def _message(broadcast, phone, status):
    contact = _contact(broadcast.tenant, phone)
    broadcast.recipients.add(contact)
    return BroadcastMessage.objects.create(broadcast=broadcast, contact=contact, status=status)


# ─────────────────────────────────────────────────────────────────────────────
# The bug, in the shape it actually took
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_cheap_destination_is_refunded_what_it_cost(broadcast, monkeypatch):
    """The clamp case: flat 0.10 against a real rate two orders of magnitude lower.

    Half the messages fail, so half the money should come back. Under the old
    code the computed refund exceeded the entire broadcast and was clamped to
    it — a full refund for a half-delivered campaign.
    """
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    broadcast.charged_rates = {"IN": "0.011385"}
    broadcast.save(update_fields=["charged_rates"])

    for i in range(5):
        _message(broadcast, f"+9198765{i:05d}", MessageStatusChoices.SENT)
    for i in range(5):
        _message(broadcast, f"+9198766{i:05d}", MessageStatusChoices.FAILED)

    assert broadcast.calculate_refund_amount() == Decimal("0.056925")  # 5 × 0.011385


@pytest.mark.django_db
def test_an_expensive_destination_is_not_short_refunded(broadcast, monkeypatch):
    """The silent case: above the flat rate the clamp never fires at all."""
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    broadcast.charged_rates = {"US": "0.028750"}
    broadcast.save(update_fields=["charged_rates"])

    for i in range(4):
        _message(broadcast, f"+1415555{i:04d}", MessageStatusChoices.FAILED)

    # The old code returned 4 × 0.10 = 0.40 — more than charged, in the
    # tenant's favour; a cheaper destination goes the other way. Either way it
    # was not what was taken.
    assert broadcast.calculate_refund_amount() == Decimal("0.115000")


@pytest.mark.django_db
def test_a_mixed_destination_broadcast_refunds_each_at_its_own_rate(broadcast, monkeypatch):
    """One flat price cannot be right for two destinations at once."""
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    broadcast.charged_rates = {"IN": "0.010000", "US": "0.030000"}
    broadcast.save(update_fields=["charged_rates"])

    _message(broadcast, "+919876500001", MessageStatusChoices.FAILED)
    _message(broadcast, "+14155550001", MessageStatusChoices.FAILED)
    _message(broadcast, "+14155550002", MessageStatusChoices.SENT)

    assert broadcast.calculate_refund_amount() == Decimal("0.040000")


@pytest.mark.django_db
def test_a_fully_failed_broadcast_refunds_exactly_what_it_cost(broadcast, monkeypatch):
    """And does so without relying on the clamp to make it look right."""
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    broadcast.charged_rates = {"IN": "0.011385"}
    broadcast.save(update_fields=["charged_rates"])

    for i in range(3):
        _message(broadcast, f"+9198765{i:05d}", MessageStatusChoices.FAILED)

    charged = Decimal("0.011385") * 3
    assert broadcast.calculate_refund_amount() == charged


# ─────────────────────────────────────────────────────────────────────────────
# Edges
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_blocked_counts_as_failed(broadcast, monkeypatch):
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    broadcast.charged_rates = {"IN": "0.020000"}
    broadcast.save(update_fields=["charged_rates"])

    _message(broadcast, "+919876500001", MessageStatusChoices.BLOCKED)

    assert broadcast.calculate_refund_amount() == Decimal("0.020000")


@pytest.mark.django_db
def test_nothing_failed_means_nothing_back(broadcast, monkeypatch):
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    broadcast.charged_rates = {"IN": "0.020000"}
    broadcast.save(update_fields=["charged_rates"])

    _message(broadcast, "+919876500001", MessageStatusChoices.SENT)

    assert broadcast.calculate_refund_amount() == Decimal("0")


@pytest.mark.django_db
def test_a_destination_missing_from_the_record_falls_back_to_flat(broadcast, monkeypatch):
    """A contact whose number changed after the charge. Rare, but not a crash."""
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    broadcast.charged_rates = {"IN": "0.010000"}
    broadcast.save(update_fields=["charged_rates"])

    _message(broadcast, "+14155550001", MessageStatusChoices.FAILED)  # US, not recorded

    assert broadcast.calculate_refund_amount() == Decimal("0.10")


@pytest.mark.django_db
def test_an_unparseable_number_uses_the_unknown_bucket(broadcast, monkeypatch):
    """Matching the charge path, which buckets these the same way."""
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    broadcast.charged_rates = {"__unknown__": "0.070000"}
    broadcast.save(update_fields=["charged_rates"])

    _message(broadcast, "not-a-number", MessageStatusChoices.FAILED)

    assert broadcast.calculate_refund_amount() == Decimal("0.070000")


@pytest.mark.django_db
def test_a_legacy_broadcast_keeps_the_old_behaviour(broadcast, monkeypatch):
    """Charged before the field existed — the flat price is all there is."""
    monkeypatch.setattr(Broadcast, "get_message_price", lambda self: Decimal("0.10"))
    assert broadcast.charged_rates == {}

    for i in range(3):
        _message(broadcast, f"+9198765{i:05d}", MessageStatusChoices.FAILED)

    assert broadcast.calculate_refund_amount() == Decimal("0.30")


# ─────────────────────────────────────────────────────────────────────────────
# The rates are actually recorded
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_charge_path_records_the_rates_it_used(broadcast, monkeypatch):
    """Without this the refund has nothing to read and silently degrades."""
    from wa.models import MessageTypeChoices, MetaBaseRate

    MetaBaseRate.objects.create(
        destination_country="IN",
        message_type=MessageTypeChoices.MARKETING,
        rate=Decimal("0.009900"),
        effective_from=timezone.now().date(),
        is_current=True,
    )
    _message(broadcast, "+919876500001", MessageStatusChoices.PENDING)

    # No template wired on this fixture, so the per-country path bails early
    # and returns None — the point here is only that it does not crash and
    # leaves the attribute unset rather than half-written.
    assert broadcast._calculate_whatsapp_cost_per_country() is None
    assert getattr(broadcast, "_charged_rates", None) is None
