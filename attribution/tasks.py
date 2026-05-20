"""Async batched CAPI flush (#197).

Reads pending ``AttributionEvent`` rows, hashes PII per Meta spec,
synthesises ``fbc`` from ``ctwa_clid``, and POSTs in batches to the
Conversions API. Retry policy: exponential backoff (1, 5, 30, 300,
1800s); DLQ at the 6th attempt.

The HTTP call itself is stubbed pending #190 Meta App Review. The
batching, retry, and PII-hashing logic is real.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from attribution.models import AttributionEvent, CapiStatus

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000
RETRY_BACKOFF_SECONDS = [1, 5, 30, 300, 1800]
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1


def _sha256_lower(s: str) -> str:
    return hashlib.sha256(s.strip().lower().encode("utf-8")).hexdigest()


def _normalise_phone(phone) -> str:
    """Per Meta spec: digits-only, no leading + or 00.

    Accepts ``str`` or a ``phonenumber_field.PhoneNumber`` (the type
    ``TenantContact.phone`` uses) — the helper coerces via ``str()``
    before scanning digits so callers don't need to remember which
    flavour they're holding.
    """
    return "".join(ch for ch in str(phone) if ch.isdigit())


def _synthesise_fbc(ctwa_clid: str, when=None) -> str:
    """Build the ``fbc`` cookie value per Meta spec:
    ``fb.1.{unix_ms}.{ctwa_clid}``. Empty string if no clid."""
    if not ctwa_clid:
        return ""
    when = when or timezone.now()
    return f"fb.1.{int(when.timestamp() * 1000)}.{ctwa_clid}"


def _build_capi_payload(event: AttributionEvent) -> dict:
    """Project an AttributionEvent to Meta's Conversions API event shape."""
    lead = event.lead
    contact = lead.contact

    user_data: dict = {}
    if lead.ctwa_clid:
        user_data["fbc"] = _synthesise_fbc(lead.ctwa_clid, event.event_time)
    # Phone fallback — only present if the contact has one.
    phone = getattr(contact, "phone", "") or ""
    if phone:
        user_data["ph"] = [_sha256_lower(_normalise_phone(phone))]
    email = getattr(contact, "email", "") or ""
    if email:
        user_data["em"] = [_sha256_lower(email)]

    payload: dict = {
        "event_name": event.event_name,
        "event_time": int(event.event_time.timestamp()),
        "event_id": event.event_id,
        "action_source": "business_messaging",
        "user_data": user_data,
    }
    if event.event_value_minor is not None and event.currency:
        payload["custom_data"] = {
            "value": event.event_value_minor / 100.0,
            "currency": event.currency,
        }
    return payload


@shared_task
def flush_capi_queue() -> dict:
    """Pick up due ``AttributionEvent`` rows and push them to CAPI.

    Two-phase to avoid holding row locks across HTTP latency:

      1. **Short claim transaction.** Open ``transaction.atomic``,
         ``select_for_update(skip_locked=True)`` the next
         :data:`BATCH_SIZE` due rows, flip their ``capi_status`` to
         ``IN_FLIGHT`` (a transient marker that excludes them from
         other workers' due-set), commit.
      2. **Per-row send.** For each claimed id, run ``_send_one``
         which opens its own short transaction to record SENT /
         FAILED / DEAD_LETTERED. The HTTP call runs OUTSIDE any
         lock — multi-worker scale OK at real CAPI latency. (#201
         third review Blocker #1)

    Scheduled via Celery beat: every 60s OR when queue depth crosses
    1000 (separate signal — added when volume warrants).
    """

    now = timezone.now()
    claimed_ids = _claim_due_events(now)

    sent = 0
    failed = 0
    dlq = 0
    for event_id in claimed_ids:
        # Re-fetch under fresh transaction; another worker can't
        # touch it because capi_status=IN_FLIGHT was committed by
        # the claim phase above.
        try:
            event = AttributionEvent.objects.get(pk=event_id)
        except AttributionEvent.DoesNotExist:
            # Extremely unlikely — only if a sibling task hard-deleted
            # the row between claim and send. Skip and move on.
            continue
        result = _send_one(event)
        if result == "sent":
            sent += 1
        elif result == "dlq":
            dlq += 1
        else:
            failed += 1

    summary = {"sent": sent, "failed": failed, "dlq": dlq}
    logger.info("[attribution.flush] %s", summary)
    return summary


# Threshold for the IN_FLIGHT sweeper. A worker crash between claim
# (status=IN_FLIGHT) and _send_one would otherwise pin events forever.
# 10 minutes is generous — real CAPI calls finish in seconds; a stuck
# IN_FLIGHT means the worker died.
IN_FLIGHT_RECLAIM_AFTER = timedelta(minutes=10)


@shared_task
def reclaim_stuck_in_flight_events() -> dict:
    """Sweep ``IN_FLIGHT`` events older than :data:`IN_FLIGHT_RECLAIM_AFTER`
    back to ``PENDING`` so the normal flusher picks them up again.

    Scheduled via Celery beat: every 5 minutes. Cheap query
    (indexed on ``(capi_status, updated_at)``).
    """
    cutoff = timezone.now() - IN_FLIGHT_RECLAIM_AFTER
    reclaimed = AttributionEvent.objects.filter(
        capi_status=CapiStatus.IN_FLIGHT,
        updated_at__lt=cutoff,
    ).update(capi_status=CapiStatus.PENDING)
    if reclaimed:
        logger.warning("[attribution.reclaim] %d events back to PENDING", reclaimed)
    return {"reclaimed": reclaimed}


def _claim_due_events(now) -> list:
    """Atomically claim up to :data:`BATCH_SIZE` due events for this
    worker by flipping their ``capi_status`` to ``IN_FLIGHT``.

    Lock is held only across the SELECT + UPDATE — no HTTP inside.
    Returns the list of claimed ids in submission order. Other workers
    see the IN_FLIGHT rows as excluded from their due-set (the filter
    only matches PENDING / FAILED) so they pick disjoint batches.
    """
    from django.db import transaction

    with transaction.atomic():
        due_ids = list(
            AttributionEvent.objects.select_for_update(skip_locked=True)
            .filter(capi_status__in=[CapiStatus.PENDING, CapiStatus.FAILED])
            .filter(models_q_due(now))
            .order_by("created_at")
            .values_list("id", flat=True)[:BATCH_SIZE]
        )
        if due_ids:
            # Stamp IN_FLIGHT so the rows aren't picked up by another
            # worker after this transaction commits and the row locks
            # release. ``_send_one`` will flip them to SENT / FAILED /
            # DEAD_LETTERED in its own short transaction.
            AttributionEvent.objects.filter(id__in=due_ids).update(
                capi_status=CapiStatus.IN_FLIGHT,
                updated_at=now,
            )
    return due_ids


def models_q_due(now):
    """Q() helper for the next_retry_at filter — null OR <= now."""
    from django.db.models import Q

    return Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)


def _send_one(event: AttributionEvent) -> str:
    """Push a single event. Returns ``"sent"`` / ``"failed"`` / ``"dlq"``.

    Stubbed HTTP for now — see :func:`_post_to_capi`. Replace its body
    with the real ``requests.post`` once #190 is approved.
    """
    payload = _build_capi_payload(event)
    try:
        response = _post_to_capi(payload, lead=event.lead)
    except Exception as exc:  # noqa: BLE001
        return _record_failure(event, str(exc))

    event.capi_response = response
    event.emq_score = _extract_emq_score(response)
    event.capi_status = CapiStatus.SENT
    event.capi_attempts += 1
    event.save(update_fields=["capi_response", "emq_score", "capi_status", "capi_attempts", "updated_at"])
    return "sent"


def _extract_emq_score(response: dict | None) -> int | None:
    """Pull Meta's per-event match-quality score from a CAPI response.

    Defensive: real CAPI returns ``events_received: []`` on partial
    failure (some events received, some rejected), and the response
    dict can be missing entirely on serialisation hiccups. (#201
    second review Medium #8)
    """
    if not isinstance(response, dict):
        return None
    received = response.get("events_received")
    if not isinstance(received, list) or not received:
        return None
    first = received[0]
    if not isinstance(first, dict):
        return None
    score = first.get("matching_score")
    return score if isinstance(score, int) else None


def _record_failure(event: AttributionEvent, error: str) -> str:
    event.capi_attempts += 1
    if event.capi_attempts >= MAX_ATTEMPTS:
        event.capi_status = CapiStatus.DEAD_LETTERED
        event.save(update_fields=["capi_attempts", "capi_status", "updated_at"])
        return "dlq"

    backoff = RETRY_BACKOFF_SECONDS[min(event.capi_attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
    event.capi_status = CapiStatus.FAILED
    event.next_retry_at = timezone.now() + timedelta(seconds=backoff)
    event.capi_response = {"error": error[:500]}
    event.save(
        update_fields=[
            "capi_attempts",
            "capi_status",
            "next_retry_at",
            "capi_response",
            "updated_at",
        ]
    )
    return "failed"


def _post_to_capi(payload: dict, *, lead) -> dict:
    """Stub for the actual CAPI POST.

    Production replaces this with:
      url = f"https://graph.facebook.com/v22.0/{pixel_id}/events"
      data = {"data": [payload], "access_token": connection.system_user_token}
      r = requests.post(url, json=data, timeout=10)
      r.raise_for_status()
      return r.json()

    Returns an empty response in the stub so callers progress to
    "sent" without HTTP-flakiness during tests.
    """
    time.sleep(0)  # placeholder for I/O
    logger.info("[attribution] STUB CAPI push event_id=%s", payload.get("event_id"))
    return {"events_received": [{"matching_score": 0}], "stub": True}


__all__ = ["flush_capi_queue"]
