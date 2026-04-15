"""Check and cache RCS capability for phone numbers."""

from __future__ import annotations

from django.core.cache import cache


class RCSCapabilityChecker:
    """Check and cache RCS capability for phone numbers."""

    CACHE_TTL = 3600  # 1 hour

    def __init__(self, provider):
        self.provider = provider

    def is_rcs_capable(self, phone: str) -> bool:
        return self.get_capability(phone).is_rcs_enabled

    def get_capability(self, phone: str):
        """Get full RCSCapability for a phone (including features). Uses cache."""
        cache_key = f"rcs:cap:full:{self.provider.rcs_app.agent_id}:{phone}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        result = self.provider.check_capability(phone)
        cache.set(cache_key, result, timeout=self.CACHE_TTL)
        return result

    def batch_check(self, phones: list) -> dict:
        """Batch check multiple phones. Returns {phone: RCSCapability}."""
        results = {}
        uncached = []
        for phone in phones:
            cache_key = f"rcs:cap:full:{self.provider.rcs_app.agent_id}:{phone}"
            cached = cache.get(cache_key)
            if cached is not None:
                results[phone] = cached
            else:
                uncached.append(phone)

        if uncached:
            batch_result = self.provider.batch_check_capability(uncached)
            for phone, cap in batch_result.items():
                results[phone] = cap
                cache_key = f"rcs:cap:full:{self.provider.rcs_app.agent_id}:{phone}"
                cache.set(cache_key, cap, timeout=self.CACHE_TTL)

        return results

    @staticmethod
    def invalidate(agent_id: str, phone: str):
        """Invalidate cached capability for a phone."""
        cache.delete(f"rcs:cap:full:{agent_id}:{phone}")
