"""SMS per-tenant rate limiting."""

from django.conf import settings
from django.core.cache import cache


def check_rate_limit(app_id: str) -> bool:
    """Return True when request can proceed, False when rate limited.

    Uses atomic add+incr to avoid TOCTOU race conditions under concurrent load.
    """
    limit = (getattr(settings, "PLATFORM_RATE_LIMITS", {}) or {}).get("sms", 100)
    key = f"sms:rate:{app_id}"

    # cache.add is atomic: sets key=0 only if it doesn't already exist
    cache.add(key, 0, timeout=60)
    # cache.incr is atomic: increment and return the new value
    current = cache.incr(key)
    return current <= int(limit)
