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
    """
    limit = settings.PLATFORM_RATE_LIMITS.get("telegram", 30)
    key = f"tg_rate:{bot_app_id}"
    current = cache.get(key, 0)
    if current >= limit:
        logger.warning(
            "[rate_limiter] Bot %s hit rate limit (%s/%s per minute)",
            bot_app_id,
            current,
            limit,
        )
        return False
    cache.set(key, current + 1, timeout=60)
    return True
