"""SMS per-tenant rate limiting."""

from django.conf import settings
from django.core.cache import cache


def check_rate_limit(app_id: str) -> bool:
    """Return True when request can proceed, False when rate limited."""
    limit = (getattr(settings, "PLATFORM_RATE_LIMITS", {}) or {}).get("sms", 100)
    key = f"sms:rate:{app_id}"

    current = cache.get(key)
    if current is None:
        cache.set(key, 1, timeout=60)
        return True

    if int(current) >= int(limit):
        return False

    cache.incr(key)
    return True
