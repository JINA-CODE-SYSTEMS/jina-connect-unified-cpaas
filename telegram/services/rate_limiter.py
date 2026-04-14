"""
Redis-backed per-bot rate limiter for Telegram message sending.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def check_rate_limit(bot_app_id: str) -> bool:
    """
    Return True if the bot is under the per-minute rate limit, False if throttled.

    Uses Django's cache framework (backed by Redis in production).
    Atomic: ``cache.add`` creates the key only if missing, ``cache.incr`` is
    an atomic Redis INCR so concurrent workers can never double-count.
    """
    limit = settings.PLATFORM_RATE_LIMITS.get("telegram", 30)
    key = f"tg_rate:{bot_app_id}"

    # Initialise key atomically; add() is a no-op when the key exists.
    cache.add(key, 0, timeout=60)

    try:
        current = cache.incr(key)
    except ValueError:
        # Key expired between add() and incr() — extremely unlikely but safe.
        cache.set(key, 1, timeout=60)
        return True

    if current > limit:
        logger.warning(
            "[rate_limiter] Bot %s hit rate limit (%s/%s per minute)",
            bot_app_id,
            current,
            limit,
        )
        return False
    return True
