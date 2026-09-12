"""Per-number send pacing and provider-requested cooldowns for broadcasts (#271).

Two questions, one store:

* **May this number send right now?** ``BroadcastService`` queues batches of
  1000 and the dispatch loop walked them with no pacing at all, so a broadcast
  went out as fast as one worker could post — which is how a number earns the
  429 that the retry path then has to clean up. Sends are metered against the
  tier the BSP reports for the number (#267 populates it, the hourly sync keeps
  it current).
* **Has the provider told us to stop?** A 429 carrying ``Retry-After`` is the
  provider naming the moment it will take traffic again. Until then this number
  sends nothing, because asking sooner just collects another 429.

Backed by Django's cache, like the SMS, RCS and Telegram limiters. Where the
cache is a shared Redis both questions are answered fleet-wide. Where it is
not, the state is per-process — and the case that matters most still holds,
because the rest of the batch that earned the 429 is walked by the very worker
that recorded it, so those messages defer instead of re-sending. Every window
is re-checked at send time, so a limiter that forgets makes a send later than
it had to, never twice.
"""

from __future__ import annotations

import logging
import time

from django.core.cache import cache

from tenants.models import WABAInfo

logger = logging.getLogger(__name__)

#: Length of the pacing window. Sends are counted per number per minute.
PACE_WINDOW_SECONDS = 60

#: Sends per minute allowed for a number, by the throughput tier the BSP reports.
#: Meta rates throughput per *second* (STANDARD ≈ 80/s, HIGH ≈ 1000/s), so these
#: sit far below the documented ceiling — deliberately. The limit that bites
#: first is not the one Meta enforces but the number's quality rating, which a
#: burst of marketing traffic damages well before the API starts refusing; and a
#: broadcast that paces itself out over a few extra minutes is not one anybody
#: notices is late.
SENDS_PER_MINUTE_BY_THROUGHPUT = {
    WABAInfo.Throughput.HIGH: 3000,
    WABAInfo.Throughput.STANDARD: 600,
    WABAInfo.Throughput.NOT_APPLICABLE: 60,
}

#: Pace for a number whose throughput has not been synced yet. Conservative on
#: purpose: an unknown tier is not an excuse to send at the fastest one.
DEFAULT_SENDS_PER_MINUTE = 60

#: Ceiling on a provider-requested cooldown. An interval longer than this is
#: usually a daily cap rather than a throughput blip, and parking a broadcast
#: behind a single header for an hour is worse than letting the retry sweep own
#: it — the sweep is visible in the database, a celery countdown is not.
MAX_COOLDOWN_SECONDS = 15 * 60


def _pace_key(wa_app) -> str:
    """Keyed on the app, which is 1:1 with the WhatsApp number — and the number
    is what the provider's limits apply to, not the tenant."""
    return f"broadcast:pace:{wa_app.pk}"


def _cooldown_key(wa_app) -> str:
    return f"broadcast:cooldown:{wa_app.pk}"


def sends_per_minute(wa_app) -> int:
    """This number's send budget for one pacing window.

    Two inputs, because the two fields answer different questions:
    ``throughput`` is how fast the provider will take messages from this number,
    ``messaging_limit`` is how many conversations it may open in 24 hours. A
    TIER_50 number has no use for a 600/minute pace whatever its throughput
    says, so the smaller of the two wins.
    """
    waba_info = getattr(wa_app, "waba_info", None)
    throughput = getattr(waba_info, "throughput", None)
    tier = getattr(waba_info, "messaging_limit", None)

    per_minute = SENDS_PER_MINUTE_BY_THROUGHPUT.get(throughput, DEFAULT_SENDS_PER_MINUTE)
    # ``get_limit`` answers TIER_UNLIMITED with infinity, which min() handles.
    return int(min(per_minute, WABAInfo.MessagingLimit.get_limit(tier)))


def reserve_send_slot(wa_app) -> bool:
    """Claim one send from this number's budget for the current window.

    ``cache.add`` then ``cache.incr`` is the house pattern (see
    ``telegram/services/rate_limiter``): add is a no-op when the key already
    exists and incr is atomic, so two workers cannot both think they have the
    last slot.

    Returns ``False`` when the window's budget is spent — the caller must defer
    rather than send.
    """
    key = _pace_key(wa_app)
    budget = sends_per_minute(wa_app)

    cache.add(key, 0, timeout=PACE_WINDOW_SECONDS)
    try:
        used = cache.incr(key)
    except ValueError:
        # The key expired between the add and the incr — the window has just
        # rolled over, so this send is the first one in the new one.
        cache.set(key, 1, timeout=PACE_WINDOW_SECONDS)
        used = 1

    if used > budget:
        logger.warning(
            "[broadcast.rate_limiter] wa_app %s at its pace (%s/%s per %ss) — deferring",
            wa_app.pk,
            used,
            budget,
            PACE_WINDOW_SECONDS,
        )
        return False
    return True


def start_cooldown(wa_app, seconds) -> int:
    """Take this number out of action for the interval the provider asked for.

    Returns the interval actually applied — 0 when there was nothing usable to
    honour, which is the caller's signal to fall back to its own retry timing.
    Clamped at :data:`MAX_COOLDOWN_SECONDS`.

    Setting the cooldown here is also what stops the *rest of the current
    batch*: every message after this one finds the window closed and defers
    without spending a request, instead of collecting 999 more 429s.
    """
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return 0
    if seconds <= 0:
        return 0

    seconds = min(seconds, MAX_COOLDOWN_SECONDS)
    cache.set(_cooldown_key(wa_app), time.time() + seconds, timeout=seconds)
    logger.warning(
        "[broadcast.rate_limiter] wa_app %s cooling down for %ss at the provider's request",
        wa_app.pk,
        seconds,
    )
    return seconds


def cooldown_seconds_remaining(wa_app) -> int:
    """Seconds left on a provider-requested cooldown for this number, 0 if none.

    Stored as the wall-clock instant the window opens rather than as a bare
    flag, so the caller can re-queue for that moment instead of guessing.
    """
    until = cache.get(_cooldown_key(wa_app))
    if not until:
        return 0
    return max(0, int(until - time.time()))
