"""Check and cache RCS capability for phone numbers."""

from __future__ import annotations

from django.core.cache import cache


class RCSCapabilityChecker:
    """Check and cache RCS capability for phone numbers."""

    CACHE_TTL = 604800  # 7 days (#109)

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

    def batch_check(self, phones: list, *, persist: bool = True) -> dict:
        """Batch check multiple phones. Returns {phone: RCSCapability}.

        When *persist* is True (default), also updates TenantContact.rcs_capable
        and rcs_checked_at for matching contacts (#110).
        """
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

        # Persist RCS capability on TenantContact records (#110)
        if persist and results:
            tenant = self.provider.rcs_app.tenant
            self._persist_capability(results, tenant)

        return results

    @staticmethod
    def _persist_capability(results: dict, tenant):
        """Write rcs_capable + rcs_checked_at to matching TenantContact rows (#110)."""
        from django.utils import timezone

        from contacts.models import TenantContact

        now = timezone.now()
        contacts = TenantContact.objects.filter(tenant=tenant, phone__in=list(results.keys()))
        to_update = []
        for contact in contacts:
            phone_str = str(contact.phone)
            cap = results.get(phone_str)
            if cap is None:
                continue
            contact.rcs_capable = getattr(cap, "is_rcs_enabled", False)
            contact.rcs_checked_at = now
            to_update.append(contact)
        if to_update:
            TenantContact.objects.bulk_update(to_update, fields=["rcs_capable", "rcs_checked_at"], batch_size=500)

    @staticmethod
    def invalidate(agent_id: str, phone: str):
        """Invalidate cached capability for a phone."""
        cache.delete(f"rcs:cap:full:{agent_id}:{phone}")
