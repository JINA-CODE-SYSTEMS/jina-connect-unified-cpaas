"""
Rate Card Service for WhatsApp messaging.

Handles:
- Margin resolution with fallback hierarchy
- Tenant rate card generation (MetaBaseRate × FX × margin)
- Recent-changes detection
- Send-time rate lookup (for CreditManager)
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.db.models import F, Q
from django.utils import timezone
from djmoney.contrib.exchange.models import convert_money
from djmoney.money import Money
from tenants.models import Tenant
from wa.models import (MessageTypeChoices, MetaBaseRate, RateCardMargin,
                       TenantRateCard)


class RateCardService:
    """
    Core service for rate card operations.

    Usage:
        svc = RateCardService(tenant)
        svc.generate_rate_cards()               # recompute all rates
        changed = svc.get_recent_changes()       # rows that changed
        rate = svc.get_send_time_rate("IN", "MARKETING")  # billing lookup
    """

    # Default margin when no RateCardMargin row exists at all
    DEFAULT_MARGIN_PERCENT = Decimal("15.00")

    def __init__(self, tenant: Tenant):
        self.tenant = tenant
        self.wallet_currency = str(tenant.balance.currency)

    # =========================================================================
    # MARGIN RESOLUTION
    # =========================================================================

    def resolve_margin(
        self,
        destination_country: str,
        message_type: str,
    ) -> Decimal:
        """
        Resolve the effective margin for a (country, message_type) pair
        using the fallback hierarchy.

        Priority (highest → lowest specificity):
            1. tenant + country + type
            2. tenant + country + NULL
            3. tenant + NULL   + type
            4. tenant + NULL   + NULL    (tenant-wide)
            5. NULL   + country + type
            6. NULL   + country + NULL
            7. NULL   + NULL   + type
            8. NULL   + NULL   + NULL    (global default)

        Returns the margin_percent of the most specific match,
        or DEFAULT_MARGIN_PERCENT if nothing is configured.
        """
        # Fetch all candidate rows in one query (at most 8 rows per lookup)
        candidates = list(
            RateCardMargin.objects.filter(
                Q(tenant=self.tenant) | Q(tenant__isnull=True),
                Q(destination_country=destination_country) | Q(destination_country__isnull=True),
                Q(message_type=message_type) | Q(message_type__isnull=True),
            ).only("tenant_id", "destination_country", "message_type", "margin_percent")
        )

        if not candidates:
            return self.DEFAULT_MARGIN_PERCENT

        # Sort by specificity (descending) and return the most specific
        candidates.sort(key=lambda m: m.specificity, reverse=True)
        return candidates[0].margin_percent

    # =========================================================================
    # FX RATE LOOKUP
    # =========================================================================

    def _get_fx_rate(self) -> Decimal:
        """
        Get the current USD → wallet-currency exchange rate.
        Returns Decimal('1') if wallet currency is USD.
        """
        if self.wallet_currency == "USD":
            return Decimal("1.000000")

        # Use django-money's exchange backend
        one_usd = Money(1, "USD")
        converted = convert_money(one_usd, self.wallet_currency)
        return Decimal(str(converted.amount)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )

    # =========================================================================
    # RATE COMPUTATION
    # =========================================================================

    @staticmethod
    def compute_reference_rate(
        meta_base_rate: Decimal,
        fx_rate: Decimal,
        margin_percent: Decimal,
    ) -> Decimal:
        """
        reference_rate = meta_base_rate × fx_rate × (1 + margin / 100)

        All values are Decimal for precision.
        """
        margin_multiplier = Decimal("1") + margin_percent / Decimal("100")
        raw = meta_base_rate * fx_rate * margin_multiplier
        return raw.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    # =========================================================================
    # RATE CARD GENERATION
    # =========================================================================

    def generate_rate_cards(self, effective_from=None) -> int:
        """
        Generate / refresh all TenantRateCard rows for this tenant
        from current MetaBaseRate rows + FX + margins.

        - Skips rows flagged ``is_custom=True`` (manually overridden).
        - Stores the old reference_rate as ``previous_rate`` for change tracking.

        Args:
            effective_from: Date for the new rate period. Defaults to 1st of current month.

        Returns:
            Number of rate-card rows created or updated.
        """
        from django.utils.timezone import now as tz_now

        if effective_from is None:
            today = tz_now().date()
            effective_from = today.replace(day=1)

        fx_rate = self._get_fx_rate()
        current_base_rates = MetaBaseRate.objects.filter(is_current=True)

        if not current_base_rates.exists():
            return 0

        count = 0
        for base in current_base_rates.iterator():
            margin = self.resolve_margin(base.destination_country, base.message_type)
            reference_rate = self.compute_reference_rate(base.rate, fx_rate, margin)

            # Check for existing row (current period)
            existing = TenantRateCard.objects.filter(
                tenant=self.tenant,
                destination_country=base.destination_country,
                message_type=base.message_type,
                effective_from=effective_from,
            ).first()

            if existing and existing.is_custom:
                # Don't overwrite custom rates
                continue

            previous_rate = existing.reference_rate if existing else None

            # If no existing row for this period, check previous period
            if previous_rate is None:
                prev_row = (
                    TenantRateCard.objects.filter(
                        tenant=self.tenant,
                        destination_country=base.destination_country,
                        message_type=base.message_type,
                        effective_from__lt=effective_from,
                    )
                    .order_by("-effective_from")
                    .values_list("reference_rate", flat=True)
                    .first()
                )
                previous_rate = prev_row

            defaults = {
                "meta_base_rate": base.rate,
                "fx_rate": fx_rate,
                "margin_percent": margin,
                "reference_rate": reference_rate,
                "wallet_currency": self.wallet_currency,
                "previous_rate": previous_rate,
                "is_custom": False,
            }

            TenantRateCard.objects.update_or_create(
                tenant=self.tenant,
                destination_country=base.destination_country,
                message_type=base.message_type,
                effective_from=effective_from,
                defaults=defaults,
            )
            count += 1

        return count

    # =========================================================================
    # RECENT CHANGES
    # =========================================================================

    def get_recent_changes(self, effective_from=None):
        """
        Return TenantRateCard rows where rate changed vs the previous period.

        A rate is considered "changed" if:
        - previous_rate is NULL (new country/type combo), or
        - reference_rate ≠ previous_rate
        """
        if effective_from is None:
            today = timezone.now().date()
            effective_from = today.replace(day=1)

        return TenantRateCard.objects.filter(
            tenant=self.tenant,
            effective_from=effective_from,
        ).exclude(
            # Exclude rows where rate is unchanged
            previous_rate=F("reference_rate"),
        )

    # =========================================================================
    # SEND-TIME RATE LOOKUP (used by CreditManager)
    # =========================================================================

    def get_send_time_rate(
        self,
        destination_country: str,
        message_type: str,
    ) -> Optional[Decimal]:
        """
        Get the per-message rate for a specific country + message_type
        at send time, in the tenant's wallet currency.

        This recalculates from current MetaBaseRate + live FX + margin
        (NOT from the pre-computed TenantRateCard, per the ticket spec:
        "Final charge is calculated only at message send time").

        Falls back to:
            1. Current MetaBaseRate × live FX × margin → live-computed rate
            2. TenantRateCard reference_rate (if base rate missing)
            3. None (caller should fall back to flat wa_app price)
        """
        # --- Try live computation ---
        base = MetaBaseRate.objects.filter(
            destination_country=destination_country,
            message_type=message_type,
            is_current=True,
        ).first()

        if base:
            fx_rate = self._get_fx_rate()
            margin = self.resolve_margin(destination_country, message_type)
            return self.compute_reference_rate(base.rate, fx_rate, margin)

        # --- Fallback: pre-computed rate card ---
        today = timezone.now().date()
        card = (
            TenantRateCard.objects.filter(
                tenant=self.tenant,
                destination_country=destination_country,
                message_type=message_type,
                effective_from__lte=today,
            )
            .order_by("-effective_from")
            .values_list("reference_rate", flat=True)
            .first()
        )
        return card  # may be None → caller falls back to flat rate

    # =========================================================================
    # BULK GENERATION (class method — for management command)
    # =========================================================================

    @classmethod
    def generate_all_tenant_rate_cards(cls, effective_from=None) -> dict:
        """
        Generate rate cards for ALL tenants that have WA apps.

        Returns:
            dict: {tenant_id: rows_generated, ...}
        """
        from tenants.models import TenantWAApp

        tenant_ids = (
            TenantWAApp.objects.values_list("tenant_id", flat=True).distinct()
        )
        results = {}
        for tid in tenant_ids:
            try:
                tenant = Tenant.objects.get(pk=tid)
                svc = cls(tenant)
                count = svc.generate_rate_cards(effective_from=effective_from)
                results[tid] = count
            except Tenant.DoesNotExist:
                continue
        return results
