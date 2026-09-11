"""Batch retry, resends and transient failures (#271).

Three faults sat on the dispatch loop, all of which surface as money:

* A batch retry re-ran the whole ``message_ids`` list with no status guard,
  so one late failure re-sent every message before it — duplicate WhatsApp
  messages to real customers, billed twice, and a quality-rating hit from the
  spam reports that follow.
* On exhausting retries the batch was blanket-marked FAILED, *including*
  messages the provider had already accepted. Failures are refunded, so the
  tenant was credited for traffic that really went out.
* ``retry_count`` was incremented in two places and read by nothing, so a 429
  or transient 5xx burned the recipient permanently — and, again, refunded it.

HOW TO RUN:
    .venv/bin/python -m pytest broadcast/tests/test_retry_and_resend.py -v
"""

from __future__ import annotations

import itertools

import pytest
from django.utils import timezone

from broadcast.models import (
    Broadcast,
    BroadcastMessage,
    BroadcastPlatformChoices,
    BroadcastStatusChoices,
    MessageStatusChoices,
)
from broadcast.tasks import MAX_MESSAGE_RETRIES, _already_sent, _is_transient


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name="Retry Tenant")


@pytest.fixture()
def contact(tenant):
    from contacts.models import TenantContact

    return TenantContact.objects.create(tenant=tenant, phone="+27820000001", first_name="Thandi")


@pytest.fixture()
def broadcast(tenant, contact):
    bc = Broadcast.objects.create(
        tenant=tenant,
        name="Retry Campaign",
        status=BroadcastStatusChoices.SENDING,
        platform=BroadcastPlatformChoices.WHATSAPP,
        scheduled_time=timezone.now(),
    )
    bc.recipients.add(contact)
    return bc


_contact_seq = itertools.count(100)


def _extra_contact(tenant):
    """A fresh recipient.

    ``BroadcastMessage`` is unique on (broadcast, contact), so a test wanting
    several messages on one broadcast needs several contacts.
    """
    from contacts.models import TenantContact

    n = next(_contact_seq)
    return TenantContact.objects.create(tenant=tenant, phone=f"+2782000{n:04d}", first_name=f"C{n}")


def _message(broadcast, contact, **overrides):
    fields = {"broadcast": broadcast, "contact": contact}
    fields.update(overrides)
    return BroadcastMessage.objects.create(**fields)


# ─────────────────────────────────────────────────────────────────────────────
# Which messages must never be sent again
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("status", [MessageStatusChoices.SENT, MessageStatusChoices.DELIVERED, MessageStatusChoices.READ])
def test_a_terminal_status_means_do_not_resend(broadcast, contact, status):
    assert _already_sent(_message(broadcast, contact, status=status)) is True


@pytest.mark.django_db
def test_a_provider_message_id_means_do_not_resend(broadcast, contact):
    """The id is only ever set from a response the provider actually returned.

    A row can carry one while its status is stale — a status webhook that has
    not landed, a crash between the send and the save — so the id is checked
    independently of status.
    """
    message = _message(broadcast, contact, status=MessageStatusChoices.SENDING, message_id="wamid.abc")
    assert _already_sent(message) is True


@pytest.mark.django_db
@pytest.mark.parametrize("status", [MessageStatusChoices.PENDING, MessageStatusChoices.QUEUED, MessageStatusChoices.FAILED])
def test_unsent_messages_remain_sendable(broadcast, contact, status):
    assert _already_sent(_message(broadcast, contact, status=status)) is False


def test_the_sent_statuses_match_the_enum():
    """``ALREADY_SENT_STATUSES`` is spelled with literals to dodge a circular
    import, so something has to notice if the enum moves under it."""
    from broadcast.tasks import ALREADY_SENT_STATUSES

    assert ALREADY_SENT_STATUSES == {
        MessageStatusChoices.SENT.value,
        MessageStatusChoices.DELIVERED.value,
        MessageStatusChoices.READ.value,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Which failures deserve another attempt
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        "Request failed with status code 429",
        "Request failed with status code 503",
        "(#131026) Too Many Requests",
        "HTTPSConnectionPool: Read timed out",
        "Connection aborted",
        "Service temporarily unavailable",
    ],
)
def test_transient_failures_are_recognised(error):
    assert _is_transient(error) is True


@pytest.mark.parametrize(
    "error",
    [
        "Request failed with status code 400",
        "(#132000) Template param count mismatch",
        "Recipient has blocked the business",
        "Invalid phone number",
        "",
    ],
)
def test_permanent_failures_are_not_retried(error):
    """A rate limit says 'not now'; a bad template says 'not ever'."""
    assert _is_transient(error) is False


# ─────────────────────────────────────────────────────────────────────────────
# The batch loop
# ─────────────────────────────────────────────────────────────────────────────


def _run_batch(message_ids, handler):
    """Run the batch task with the provider call replaced."""
    from unittest.mock import patch

    from broadcast import tasks

    with patch.object(tasks, "route_to_platform_handler", side_effect=handler):
        return tasks.process_broadcast_messages_batch(message_ids)


@pytest.mark.django_db
def test_an_already_sent_message_is_skipped_not_resent(broadcast, contact):
    """The headline: a batch retry must not re-send what already went out."""
    sent = _message(broadcast, contact, status=MessageStatusChoices.SENT, message_id="wamid.1")
    pending = _message(broadcast, _extra_contact(broadcast.tenant), status=MessageStatusChoices.PENDING)

    attempted = []

    def handler(message):
        attempted.append(message.id)
        return {"success": True, "message_id": "wamid.new"}

    result = _run_batch([sent.id, pending.id], handler)

    assert attempted == [pending.id], "the sent message was handed to the provider again"
    assert result["skipped_already_sent"] == 1
    sent.refresh_from_db()
    assert sent.message_id == "wamid.1", "the original provider id was overwritten"


@pytest.mark.django_db
def test_a_whole_batch_of_sent_messages_sends_nothing(broadcast, contact):
    """The real shape of the bug: a late failure re-running everything before it."""
    ids = [
        _message(
            broadcast,
            _extra_contact(broadcast.tenant),
            status=MessageStatusChoices.SENT,
            message_id=f"wamid.{i}",
        ).id
        for i in range(5)
    ]

    attempted = []
    _run_batch(ids, lambda m: attempted.append(m.id) or {"success": True})

    assert attempted == []


@pytest.mark.django_db
def test_a_transient_failure_returns_to_pending_for_another_attempt(broadcast, contact):
    message = _message(broadcast, contact, status=MessageStatusChoices.PENDING)

    result = _run_batch([message.id], lambda m: {"success": False, "error": "Request failed with status code 429"})

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.PENDING
    assert message.retry_count == 1
    assert result["retryable"] == 1
    assert result["failed"] == 0


@pytest.mark.django_db
def test_a_permanent_failure_fails_immediately(broadcast, contact):
    message = _message(broadcast, contact, status=MessageStatusChoices.PENDING)

    result = _run_batch([message.id], lambda m: {"success": False, "error": "(#132000) Template param mismatch"})

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.FAILED
    assert result["failed"] == 1


@pytest.mark.django_db
def test_retrying_forever_is_not_an_option(broadcast, contact):
    """Past the ceiling a transient failure becomes terminal."""
    message = _message(broadcast, contact, status=MessageStatusChoices.PENDING, retry_count=MAX_MESSAGE_RETRIES)

    _run_batch([message.id], lambda m: {"success": False, "error": "status code 429"})

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# The sweep that consumes retry_count
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_sweep_requeues_a_message_awaiting_retry(broadcast, contact, monkeypatch):
    from broadcast import cron, tasks

    message = _message(broadcast, contact, status=MessageStatusChoices.PENDING, retry_count=1)
    BroadcastMessage.objects.filter(pk=message.pk).update(
        updated_at=timezone.now() - timezone.timedelta(minutes=10)
    )

    queued = []
    monkeypatch.setattr(tasks.process_broadcast_messages_batch, "delay", lambda ids: queued.append(list(ids)))

    assert cron.retry_transient_message_failures() == {"requeued": 1}
    assert queued == [[message.id]]


@pytest.mark.django_db
def test_the_sweep_leaves_fresh_messages_alone(broadcast, contact, monkeypatch):
    """A batch still in flight must not be raced."""
    from broadcast import cron, tasks

    _message(broadcast, contact, status=MessageStatusChoices.PENDING, retry_count=1)

    queued = []
    monkeypatch.setattr(tasks.process_broadcast_messages_batch, "delay", lambda ids: queued.append(list(ids)))

    assert cron.retry_transient_message_failures() == {"requeued": 0}
    assert queued == []


@pytest.mark.django_db
def test_the_sweep_ignores_messages_that_never_failed(broadcast, contact, monkeypatch):
    """retry_count == 0 means this is a first attempt someone else owns."""
    from broadcast import cron, tasks

    message = _message(broadcast, contact, status=MessageStatusChoices.PENDING, retry_count=0)
    BroadcastMessage.objects.filter(pk=message.pk).update(
        updated_at=timezone.now() - timezone.timedelta(minutes=10)
    )

    monkeypatch.setattr(tasks.process_broadcast_messages_batch, "delay", lambda ids: None)
    assert cron.retry_transient_message_failures() == {"requeued": 0}


@pytest.mark.django_db
def test_both_sweeps_are_scheduled():
    """They were registered only in celery beat, which this deployment does not run."""
    from django.conf import settings

    scheduled = {entry[1] for entry in settings.CRONJOBS}
    assert "broadcast.cron.run_scheduled_broadcasts" in scheduled
    assert "broadcast.cron.retry_transient_message_failures" in scheduled
