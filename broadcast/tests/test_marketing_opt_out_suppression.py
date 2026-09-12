"""Opted-out contacts are dropped before the spend, and before the quote (#276).

`process_broadcast_messages_batch` sent to every recipient row it was handed.
Nothing consulted a suppression list because there was none, so a contact who
had asked to stop kept receiving marketing — which Meta answers by lowering the
number's quality rating and its messaging tier, and on a shared business
portfolio that cost lands on every tenant using the number.

Two halves have to agree, and each is tested here:

  * the dispatch loop must not hand a marketing template to an opted-out
    contact, and must not touch utility or authentication traffic;
  * the charge estimate must leave exactly the same contacts out, so the price
    quoted is the price the send actually spends — and a message that was never
    charged must not come back as a refund.

HOW TO RUN:
    .venv/bin/python -m pytest broadcast/tests/test_marketing_opt_out_suppression.py -v
"""

from __future__ import annotations

import itertools
import uuid
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
from contacts.models import MarketingOptOutSource, TenantContact
from wa.models import TemplateCategory

_seq = itertools.count(1)


@pytest.fixture()
def wa_app(db):
    from tenants.models import Tenant, TenantWAApp

    tenant = Tenant.objects.create(name=f"SuppressionTenant-{uuid.uuid4().hex[:6]}")
    return TenantWAApp.objects.create(
        tenant=tenant,
        app_name="t-wa",
        app_id=f"app-{uuid.uuid4().hex[:8]}",
        app_secret="s",
        wa_number=f"+1415555{uuid.uuid4().int % 10000:04d}",
    )


def _broadcast(wa_app, category):
    """A broadcast whose template carries *category* — the only thing that
    decides whether an opt-out applies."""
    from message_templates.models import TemplateNumber
    from wa.models import WATemplate

    number = TemplateNumber.objects.create(name=f"tn-{next(_seq)}")
    WATemplate.objects.create(
        tenant=wa_app.tenant,
        wa_app=wa_app,
        number=number,
        name=f"tmpl-{next(_seq)}",
        element_name=f"tmpl_{next(_seq)}",
        category=category,
        content="Hello {{first_name}}",
    )
    return Broadcast.objects.create(
        tenant=wa_app.tenant,
        name=f"Campaign {next(_seq)}",
        status=BroadcastStatusChoices.SENDING,
        platform=BroadcastPlatformChoices.WHATSAPP,
        template_number=number,
        scheduled_time=timezone.now(),
    )


def _recipient(broadcast, *, opted_out=False):
    contact = TenantContact.objects.create(
        tenant=broadcast.tenant,
        first_name=f"C{next(_seq)}",
        phone=f"+1415777{next(_seq):04d}",
    )
    if opted_out:
        contact.set_marketing_opt_out(opted_out=True, source=MarketingOptOutSource.KEYWORD)
    broadcast.recipients.add(contact)
    return contact


def _queue(broadcast, contact):
    return BroadcastMessage.objects.create(broadcast=broadcast, contact=contact, status=MessageStatusChoices.PENDING)


@pytest.fixture()
def sends(monkeypatch):
    """Stand in for the provider and record who we actually tried to message."""
    import broadcast.tasks as tasks

    attempted = []

    def _handler(message):
        attempted.append(message.contact_id)
        return {"success": True, "message_id": f"wamid.{len(attempted)}", "response": "ok"}

    monkeypatch.setattr(tasks, "route_to_platform_handler", _handler)
    return attempted


def _run(*messages):
    from broadcast.tasks import process_broadcast_messages_batch

    return process_broadcast_messages_batch.apply(args=([m.id for m in messages],)).get()


# ─────────────────────────────────────────────────────────────────────────────
# The dispatch loop
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_contact_who_opted_out_gets_no_further_marketing(wa_app, sends):
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)
    quiet = _recipient(broadcast, opted_out=True)
    willing = _recipient(broadcast)
    quiet_msg, willing_msg = _queue(broadcast, quiet), _queue(broadcast, willing)

    result = _run(quiet_msg, willing_msg)

    assert sends == [willing.id]
    quiet_msg.refresh_from_db()
    willing_msg.refresh_from_db()
    assert quiet_msg.status == MessageStatusChoices.SUPPRESSED
    assert willing_msg.status == MessageStatusChoices.SENT
    assert result["suppressed_opted_out"] == 1


@pytest.mark.django_db
def test_a_suppressed_message_says_why_on_the_row(wa_app, sends):
    """Whoever asks "why did this one not go out" reads the row, not the log."""
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)
    message = _queue(broadcast, _recipient(broadcast, opted_out=True))

    _run(message)

    message.refresh_from_db()
    assert "opted out" in message.response


@pytest.mark.django_db
@pytest.mark.parametrize("category", [TemplateCategory.UTILITY, TemplateCategory.AUTHENTICATION])
def test_transactional_traffic_is_unaffected(wa_app, sends, category):
    """An order update or a login code is not what anyone unsubscribed from."""
    broadcast = _broadcast(wa_app, category)
    contact = _recipient(broadcast, opted_out=True)
    message = _queue(broadcast, contact)

    _run(message)

    assert sends == [contact.id]
    message.refresh_from_db()
    assert message.status == MessageStatusChoices.SENT


@pytest.mark.django_db
def test_marketing_still_reaches_everyone_who_did_not_opt_out(wa_app, sends):
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)
    messages = [_queue(broadcast, _recipient(broadcast)) for _ in range(3)]

    result = _run(*messages)

    assert len(sends) == 3
    assert result["suppressed_opted_out"] == 0
    assert result["successful"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# The charge estimate — the other half of the same decision
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_quote_leaves_out_the_contacts_the_send_will_skip(wa_app):
    """Quoting for messages that dispatch refuses to send bills for nothing."""
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)
    _recipient(broadcast)
    _recipient(broadcast)
    _recipient(broadcast, opted_out=True)

    assert broadcast.billable_recipients().count() == 2
    assert broadcast.calculate_initial_cost() == 2 * broadcast.get_message_price()


@pytest.mark.django_db
@pytest.mark.parametrize("category", [TemplateCategory.UTILITY, TemplateCategory.AUTHENTICATION])
def test_transactional_broadcasts_are_still_quoted_for_everyone(wa_app, category):
    broadcast = _broadcast(wa_app, category)
    _recipient(broadcast)
    _recipient(broadcast)
    _recipient(broadcast, opted_out=True)

    assert broadcast.billable_recipients().count() == 3
    assert broadcast.calculate_initial_cost() == 3 * broadcast.get_message_price()


@pytest.mark.django_db
def test_the_per_country_quote_skips_them_too(wa_app, monkeypatch):
    """The rate-card path groups recipients by destination itself (#262), so it
    has to be told about opt-outs separately from the flat-rate path."""
    from datetime import date

    from wa.models import MessageTypeChoices, MetaBaseRate
    from wa.services.rate_card_service import RateCardService

    MetaBaseRate.objects.create(
        name="us-marketing",
        destination_country="US",
        message_type=MessageTypeChoices.MARKETING,
        rate=Decimal("0.025000"),
        effective_from=date(2026, 1, 1),
        is_current=True,
    )
    monkeypatch.setattr(RateCardService, "get_send_time_rate", lambda self, country, kind: Decimal("0.030000"))

    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)
    _recipient(broadcast)
    _recipient(broadcast)
    _recipient(broadcast, opted_out=True)

    assert broadcast._calculate_whatsapp_cost_per_country() == Decimal("0.060000")


# ─────────────────────────────────────────────────────────────────────────────
# Charge and refund stay symmetrical (#262 / #263)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_suppressed_message_is_not_refunded(wa_app, sends):
    """It was never charged. Refunding it would credit the tenant for traffic
    that was never bought — the mirror of the bug #262 fixed."""
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)
    messages = [
        _queue(broadcast, _recipient(broadcast, opted_out=True)),
        _queue(broadcast, _recipient(broadcast)),
    ]

    _run(*messages)

    assert broadcast.get_failed_message_count() == 0
    assert broadcast.calculate_refund_amount() == Decimal("0")


@pytest.mark.django_db
def test_a_real_failure_alongside_a_suppression_is_still_refunded(wa_app):
    """Suppression must not swallow the refund a genuine failure earns."""
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)
    broadcast.charged_rates = {"US": "0.020000"}
    broadcast.save(update_fields=["charged_rates"])

    BroadcastMessage.objects.create(
        broadcast=broadcast,
        contact=_recipient(broadcast, opted_out=True),
        status=MessageStatusChoices.SUPPRESSED,
    )
    BroadcastMessage.objects.create(
        broadcast=broadcast,
        contact=_recipient(broadcast),
        status=MessageStatusChoices.FAILED,
    )

    assert broadcast.get_failed_message_count() == 1
    assert broadcast.calculate_refund_amount() == Decimal("0.020000")


@pytest.mark.django_db
def test_a_broadcast_cancelled_before_sending_refunds_only_what_it_was_charged(wa_app):
    """With no message rows the refund falls back to counting recipients, and
    that count has to be the billable one or the tenant is over-credited."""
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)
    _recipient(broadcast)
    _recipient(broadcast)
    _recipient(broadcast, opted_out=True)

    assert broadcast.get_failed_message_count() == 2


# ─────────────────────────────────────────────────────────────────────────────
# Completion accounting — a suppression is neither a success nor a failure
# ─────────────────────────────────────────────────────────────────────────────


def _completed(broadcast, *statuses):
    """Age the broadcast past the cron's 10-minute grace and give it rows."""
    from datetime import timedelta

    from broadcast.cron import update_broadcast_status

    for status in statuses:
        BroadcastMessage.objects.create(broadcast=broadcast, contact=_recipient(broadcast), status=status)
    broadcast.scheduled_time = timezone.now() - timedelta(minutes=30)
    broadcast.save(update_fields=["scheduled_time"])

    update_broadcast_status()
    broadcast.refresh_from_db()
    return broadcast.status


@pytest.mark.django_db
def test_a_broadcast_is_complete_once_everything_left_has_been_sent(wa_app):
    """Counting a suppressed row as outstanding leaves a finished campaign
    reading "partially sent" for good."""
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)

    status = _completed(broadcast, MessageStatusChoices.SENT, MessageStatusChoices.SUPPRESSED)

    assert status == BroadcastStatusChoices.SENT


@pytest.mark.django_db
def test_a_broadcast_whose_audience_all_opted_out_is_not_a_failure(wa_app):
    """Nothing went out, but nothing went wrong and nothing was charged —
    FAILED would send someone looking for an outage."""
    broadcast = _broadcast(wa_app, TemplateCategory.MARKETING)

    status = _completed(broadcast, MessageStatusChoices.SUPPRESSED, MessageStatusChoices.SUPPRESSED)

    assert status == BroadcastStatusChoices.SENT
    assert not broadcast.reason_for_cancellation
