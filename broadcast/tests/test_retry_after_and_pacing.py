"""A 429 delays the send, and dispatch is paced against the number (#271).

The two acceptance criteria #284 left open.

* **``Retry-After`` was not honoured.** A 429 went back to PENDING and waited
  for a fixed five-minute cron. Correct in direction, wrong in timing: the
  provider names the moment it will take traffic again, and re-queueing inside
  that window earns another 429 — the failure the whole retry path exists to
  avoid.
* **Nothing paced dispatch.** Batches of 1000 were walked as fast as one worker
  could post, against a number whose reported throughput was sitting unread in
  ``WABAInfo`` (#267 populates it, the hourly sync keeps it current).

Both are enforced in one place — the dispatch loop, which every send goes
through, including the ones the retry sweep re-queues — so there is a single
answer to "may this number send right now".

HOW TO RUN:
    .venv/bin/python -m pytest broadcast/tests/test_retry_after_and_pacing.py -v
"""

from __future__ import annotations

import itertools
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from broadcast.models import (
    Broadcast,
    BroadcastMessage,
    BroadcastPlatformChoices,
    BroadcastStatusChoices,
    MessageStatusChoices,
)
from broadcast.services import rate_limiter
from tenants.models import WABAInfo
from wa.adapters.base import AdapterResult

RATE_LIMITED = "Request failed with status code 429"


@pytest.fixture(autouse=True)
def _clean_windows():
    """Pacing counters and cooldowns live in the cache, which outlives a test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name="Pacing Tenant")


@pytest.fixture()
def wa_app(tenant):
    from tenants.models import TenantWAApp

    return TenantWAApp.objects.create(
        tenant=tenant,
        app_name=f"App {uuid.uuid4().hex[:6]}",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret=f"secret_{uuid.uuid4().hex[:8]}",
        wa_number=f"+27{uuid.uuid4().int % 10**9:09d}",
        waba_id=f"waba_{uuid.uuid4().hex[:8]}",
        phone_number_id=f"phone_{uuid.uuid4().hex[:8]}",
        is_verified=True,
        is_active=True,
    )


@pytest.fixture()
def broadcast(tenant):
    return Broadcast.objects.create(
        tenant=tenant,
        name="Paced Campaign",
        status=BroadcastStatusChoices.SENDING,
        platform=BroadcastPlatformChoices.WHATSAPP,
        scheduled_time=timezone.now(),
    )


_contact_seq = itertools.count(500)


def _message(broadcast):
    """A PENDING message to a fresh recipient.

    ``BroadcastMessage`` is unique on (broadcast, contact), so every message in
    a batch needs its own contact.
    """
    from contacts.models import TenantContact

    n = next(_contact_seq)
    contact = TenantContact.objects.create(tenant=broadcast.tenant, phone=f"+2782{n:07d}", first_name=f"C{n}")
    return BroadcastMessage.objects.create(broadcast=broadcast, contact=contact, status=MessageStatusChoices.PENDING)


def _tier(wa_app, throughput=None, messaging_limit=None):
    """Set the number's reported tier.

    ``update_or_create`` rather than ``create``: a signal already gives every
    new ``TenantWAApp`` an empty ``WABAInfo``, which the sync then fills in.
    """
    info, _ = WABAInfo.objects.update_or_create(
        wa_app=wa_app, defaults={"throughput": throughput, "messaging_limit": messaging_limit}
    )
    wa_app.refresh_from_db()  # drop the cached relation so the new values are read
    return info


def _rate_limited(retry_after: str | None = "120") -> AdapterResult:
    """What the adapter returns for a 429, header included or not."""
    return AdapterResult(
        success=False,
        provider="meta_direct",
        error_message=RATE_LIMITED,
        response_headers={"retry-after": retry_after} if retry_after is not None else {},
    )


def _sent(message_id="wamid.OK") -> AdapterResult:
    return AdapterResult(success=True, provider="meta_direct", data={"message_id": message_id})


def _run_batch(message_ids, wa_app, adapter_results, monkeypatch, settings):
    """Run the real batch task against a stub provider.

    Only two seams are stubbed: which number the broadcast sends from, and what
    the provider answers. Everything between them — the loop, the limiter, the
    cooldown, the re-queue — is the real code.

    Returns ``(task result, provider calls, re-queue calls)``.
    """
    from broadcast import tasks

    # A deployment with a broker: eager mode runs apply_async inline and drops
    # the countdown, which is the one thing these tests are about.
    settings.CELERY_TASK_ALWAYS_EAGER = False

    adapter = MagicMock()
    adapter.send_template.side_effect = adapter_results

    requeued = []
    monkeypatch.setattr(
        tasks.process_broadcast_messages_batch,
        "apply_async",
        lambda *args, **kwargs: requeued.append(kwargs),
    )

    with patch.object(tasks, "_wa_app_for_broadcast", return_value=(wa_app, None)):
        with patch("wa.adapters.get_bsp_adapter", return_value=adapter):
            result = tasks.process_broadcast_messages_batch(list(message_ids))

    return result, adapter.send_template.call_args_list, requeued


# ─────────────────────────────────────────────────────────────────────────────
# A 429 becomes a delayed send
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_429_is_re_queued_for_the_interval_the_provider_asked_for(broadcast, wa_app, monkeypatch, settings):
    """The headline: delayed by the provider's window, not by a fixed cron."""
    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD)
    message = _message(broadcast)

    result, sends, requeued = _run_batch([message.id], wa_app, [_rate_limited("120")], monkeypatch, settings)

    assert len(sends) == 1
    assert requeued == [{"args": [[message.id]], "countdown": 120}]
    assert result["deferred_countdown"] == 120

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.PENDING, "a 429 must not be terminal"
    assert message.retry_count == 1
    assert result["failed"] == 0, "a rate limit that is refunded is a billing event"


@pytest.mark.django_db
def test_the_rest_of_the_batch_waits_instead_of_collecting_more_429s(broadcast, wa_app, monkeypatch, settings):
    """One 429 takes the number out of action, so the remaining 999 are not
    offered to a provider that has just said no."""
    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD)
    messages = [_message(broadcast), _message(broadcast), _message(broadcast)]

    result, sends, requeued = _run_batch(
        [m.id for m in messages],
        wa_app,
        [_rate_limited("300"), _sent(), _sent()],
        monkeypatch,
        settings,
    )

    assert len(sends) == 1, "the provider was asked again inside its own window"
    assert result["deferred"] == 3
    assert len(requeued) == 1
    assert requeued[0]["countdown"] == 300
    # The loop's order is the queryset's, so compare membership, not sequence.
    assert set(requeued[0]["args"][0]) == {m.id for m in messages}

    for message in messages:
        message.refresh_from_db()
        assert message.status == MessageStatusChoices.PENDING
    spent = [m.retry_count for m in messages]
    assert sorted(spent) == [0, 0, 1], "only the message the provider answered may spend a retry"


@pytest.mark.django_db
def test_a_429_without_a_header_falls_back_to_the_sweep(broadcast, wa_app, monkeypatch, settings):
    """No header, no interval to honour — the old behaviour, unchanged: PENDING
    with a spent retry, for the sweep to pick up."""
    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD)
    message = _message(broadcast)

    result, sends, requeued = _run_batch([message.id], wa_app, [_rate_limited(None)], monkeypatch, settings)

    assert len(sends) == 1
    assert requeued == [], "nothing asked us to wait, so nothing should be scheduled"
    assert result["retryable"] == 1

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.PENDING
    assert message.retry_count == 1


@pytest.mark.django_db
def test_an_unrecognised_rate_limit_is_still_retried_when_an_interval_arrives(broadcast, wa_app, monkeypatch, settings):
    """The transient check matches substrings of the provider's prose. A header
    saying "come back in 60 seconds" is a stronger statement than any wording,
    so it counts on its own — otherwise the interval is honoured and the message
    is marked FAILED anyway."""
    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD)
    message = _message(broadcast)
    unfamiliar = AdapterResult(
        success=False,
        provider="gupshup",
        error_message="(#130429) quota exhausted for this number",
        response_headers={"retry-after": "60"},
    )

    result, _sends, requeued = _run_batch([message.id], wa_app, [unfamiliar], monkeypatch, settings)

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.PENDING
    assert result["failed"] == 0
    assert requeued == [{"args": [[message.id]], "countdown": 60}]


@pytest.mark.django_db
def test_an_absurd_interval_is_clamped(broadcast, wa_app, monkeypatch, settings):
    """A provider asking for an hour is usually naming a daily cap. Parking a
    broadcast behind one header for that long is worse than letting the sweep
    own it, so the wait is capped."""
    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD)
    message = _message(broadcast)

    result, _sends, requeued = _run_batch(
        [message.id], wa_app, [_rate_limited(str(6 * 60 * 60))], monkeypatch, settings
    )

    assert requeued[0]["countdown"] == rate_limiter.MAX_COOLDOWN_SECONDS
    assert result["deferred_countdown"] == rate_limiter.MAX_COOLDOWN_SECONDS


@pytest.mark.django_db
def test_a_cooldown_still_running_defers_without_touching_the_provider(broadcast, wa_app, monkeypatch, settings):
    """The sweep re-queues on a fixed interval and cannot know about the window.
    It does not have to: the send path checks before every request, so an early
    re-queue is deferred again rather than sent."""
    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD)
    message = _message(broadcast)
    rate_limiter.start_cooldown(wa_app, 240)

    result, sends, requeued = _run_batch([message.id], wa_app, [_sent()], monkeypatch, settings)

    assert sends == [], "sent inside a window the provider closed"
    assert result["deferred"] == 1
    assert 230 <= requeued[0]["countdown"] <= 240, "re-queued for what is left of the window"

    message.refresh_from_db()
    assert message.status == MessageStatusChoices.PENDING
    assert message.retry_count == 0


@pytest.mark.django_db
def test_eager_mode_leaves_the_rows_pending_rather_than_sending_now(broadcast, wa_app, monkeypatch, settings):
    """Eager mode has no broker: apply_async runs inline and ignores the
    countdown, which would re-attempt the send inside the window and recurse
    doing it. A slower retry is the right trade; an immediate one is not."""
    from broadcast import tasks

    # Pinned rather than inherited. The root conftest only turns eager mode on
    # when CELERY_BROKER_URL is unset, so a dev box with no broker reads True
    # and CI — which runs a real Redis and sets the variable — reads False.
    # Leaving it ambient made this test pass locally and fail in CI. Its
    # siblings pin the opposite value in ``_run_batch`` for the same reason;
    # this is the other half of that pair.
    settings.CELERY_TASK_ALWAYS_EAGER = True

    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD)
    message = _message(broadcast)

    adapter = MagicMock()
    adapter.send_template.return_value = _rate_limited("120")
    calls = []
    monkeypatch.setattr(tasks.process_broadcast_messages_batch, "apply_async", lambda *a, **kw: calls.append(kw))

    with patch.object(tasks, "_wa_app_for_broadcast", return_value=(wa_app, None)):
        with patch("wa.adapters.get_bsp_adapter", return_value=adapter):
            result = tasks.process_broadcast_messages_batch([message.id])

    assert calls == []
    assert result["deferred_countdown"] == 0
    message.refresh_from_db()
    assert message.status == MessageStatusChoices.PENDING


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch is paced against the number's tier
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "throughput,messaging_limit,expected",
    [
        (WABAInfo.Throughput.HIGH, WABAInfo.MessagingLimit.TIER_UNLIMITED, 3000),
        (WABAInfo.Throughput.STANDARD, WABAInfo.MessagingLimit.TIER_100K, 600),
        (WABAInfo.Throughput.NOT_APPLICABLE, WABAInfo.MessagingLimit.TIER_10K, 60),
        # The tier wins where it is the smaller of the two: a number allowed 50
        # conversations a day has no use for a 600/minute pace.
        (WABAInfo.Throughput.STANDARD, WABAInfo.MessagingLimit.TIER_50, 50),
        (WABAInfo.Throughput.HIGH, WABAInfo.MessagingLimit.TIER_250, 250),
        # Never synced: the conservative default, not the optimistic one.
        (None, None, 50),
    ],
    ids=["high", "standard", "not-applicable", "tier-floors-it", "tier-250", "unsynced"],
)
def test_the_pace_comes_from_the_numbers_tier(wa_app, throughput, messaging_limit, expected):
    _tier(wa_app, throughput=throughput, messaging_limit=messaging_limit)
    wa_app.refresh_from_db()

    assert rate_limiter.sends_per_minute(wa_app) == expected


@pytest.mark.django_db
def test_a_number_with_no_waba_info_at_all_still_has_a_pace(wa_app):
    """The signal normally creates the row, but it can be absent — an app
    imported before the signal existed, or deleted state. That must read as
    "unknown tier" — the 50 an unsynced tier scores — not blow up the send."""
    WABAInfo.objects.filter(wa_app=wa_app).delete()
    wa_app.refresh_from_db()

    assert rate_limiter.sends_per_minute(wa_app) == 50


@pytest.mark.django_db
def test_dispatch_stops_at_the_pace_and_re_queues_the_remainder(broadcast, wa_app, monkeypatch, settings):
    """The budget itself is data; this is the mechanism. Two sends allowed, three
    messages offered — the third waits for the next window instead of going out
    in the same burst."""
    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD, messaging_limit=WABAInfo.MessagingLimit.TIER_1K)
    monkeypatch.setitem(rate_limiter.SENDS_PER_MINUTE_BY_THROUGHPUT, WABAInfo.Throughput.STANDARD, 2)
    messages = [_message(broadcast), _message(broadcast), _message(broadcast)]

    result, sends, requeued = _run_batch(
        [m.id for m in messages],
        wa_app,
        [_sent("wamid.1"), _sent("wamid.2"), _sent("wamid.3")],
        monkeypatch,
        settings,
    )

    assert len(sends) == 2, "the pace was not enforced"
    assert result["successful"] == 2
    assert result["deferred"] == 1
    assert result["failed"] == 0, "a paced message must not be refunded as a failure"

    for message in messages:
        message.refresh_from_db()
    waiting = [m for m in messages if m.status == MessageStatusChoices.PENDING]
    assert len(waiting) == 1
    assert requeued == [{"args": [[waiting[0].id]], "countdown": rate_limiter.PACE_WINDOW_SECONDS}]
    assert waiting[0].retry_count == 0, "pacing is not a failed attempt and must not spend a retry"


@pytest.mark.django_db
def test_the_pace_is_per_number_not_per_tenant(tenant, wa_app, monkeypatch, settings):
    """Two numbers on one tenant have their own limits at the provider, so a
    busy one must not throttle a quiet one."""
    from tenants.models import TenantWAApp

    other = TenantWAApp.objects.create(
        tenant=tenant,
        app_name="Second number",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret="secret",
        wa_number="+27820000999",
        waba_id="waba_other",
        phone_number_id="phone_other",
        is_verified=True,
        is_active=True,
    )
    _tier(wa_app, throughput=WABAInfo.Throughput.STANDARD)
    _tier(other, throughput=WABAInfo.Throughput.STANDARD)
    monkeypatch.setitem(rate_limiter.SENDS_PER_MINUTE_BY_THROUGHPUT, WABAInfo.Throughput.STANDARD, 1)

    assert rate_limiter.reserve_send_slot(wa_app) is True
    assert rate_limiter.reserve_send_slot(wa_app) is False, "budget is spent for this number"
    assert rate_limiter.reserve_send_slot(other) is True, "another number's budget was consumed"


@pytest.mark.django_db
def test_a_cooldown_is_per_number_too(tenant, wa_app):
    from tenants.models import TenantWAApp

    other = TenantWAApp.objects.create(
        tenant=tenant,
        app_name="Third number",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret="secret",
        wa_number="+27820000998",
        waba_id="waba_third",
        phone_number_id="phone_third",
        is_verified=True,
        is_active=True,
    )

    assert rate_limiter.start_cooldown(wa_app, 120) == 120
    assert rate_limiter.cooldown_seconds_remaining(wa_app) > 0
    assert rate_limiter.cooldown_seconds_remaining(other) == 0


@pytest.mark.django_db
@pytest.mark.parametrize("asked", [None, 0, -30, "", "soon"])
def test_an_unusable_interval_starts_no_cooldown(wa_app, asked):
    """``retry_after_seconds`` already returns None for these; the limiter
    refuses them as well, because a cooldown of nonsense length is worse than
    none."""
    assert rate_limiter.start_cooldown(wa_app, asked) == 0
    assert rate_limiter.cooldown_seconds_remaining(wa_app) == 0
