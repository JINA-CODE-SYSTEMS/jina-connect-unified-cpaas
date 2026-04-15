"""RCS per-tenant rate limiting."""

from django.conf import settings
from django.core.cache import cache


def check_rate_limit(app_id: str) -> bool:
    """Return True when request can proceed, False when rate limited.

    Uses atomic add+incr to avoid TOCTOU race conditions under concurrent load.
    """
    limit = (getattr(settings, "PLATFORM_RATE_LIMITS", {}) or {}).get("rcs", 300)
    key = f"rcs:rate:{app_id}"

    cache.add(key, 0, timeout=60)
    current = cache.incr(key)
    return current <= int(limit)
