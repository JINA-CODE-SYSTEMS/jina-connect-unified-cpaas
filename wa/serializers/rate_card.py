"""
Serializers for WhatsApp Rate Card API (Issue #188).
"""
from rest_framework import serializers
from wa.models import MetaBaseRate, RateCardMargin, TenantRateCard


class TenantRateCardSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for tenant-facing rate card entries.
    Shown in wallet currency with pricing breakdown.
    """
    rate_changed = serializers.BooleanField(read_only=True)
    rate_change_percent = serializers.DecimalField(
        max_digits=8, decimal_places=2, read_only=True, allow_null=True,
    )
    destination_country_name = serializers.SerializerMethodField()

    def get_destination_country_name(self, obj) -> str:
        """Resolve ISO alpha-2 code to full country name."""
        code = obj.destination_country
        if not code:
            return ""
        try:
            import pycountry
            c = pycountry.countries.get(alpha_2=code)
            return c.name if c else code
        except (ImportError, AttributeError):
            _NAMES = {
                "IN": "India", "US": "United States", "GB": "United Kingdom",
                "AE": "United Arab Emirates", "AU": "Australia",
                "SG": "Singapore", "BR": "Brazil", "DE": "Germany",
                "FR": "France", "CA": "Canada", "ZZ": "Unknown",
            }
            return _NAMES.get(code, code)

    class Meta:
        model = TenantRateCard
        fields = [
            "id",
            "destination_country",
            "destination_country_name",
            "message_type",
            "reference_rate",
            "wallet_currency",
            "effective_from",
            "last_updated_at",
            # Breakdown (useful for transparency)
            "meta_base_rate",
            "fx_rate",
            "margin_percent",
            # Change detection
            "previous_rate",
            "rate_changed",
            "rate_change_percent",
            "is_custom",
        ]
        read_only_fields = fields


class TenantRateCardSummarySerializer(serializers.Serializer):
    """Response serializer for the rate-card summary endpoint."""
    total_countries = serializers.IntegerField()
    total_entries = serializers.IntegerField()
    wallet_currency = serializers.CharField()
    effective_from = serializers.DateField()
    by_message_type = serializers.DictField(
        child=serializers.DictField(),
        help_text="Avg / min / max rate per message type",
    )
    recent_changes_count = serializers.IntegerField()


class MetaBaseRateSerializer(serializers.ModelSerializer):
    """Admin serializer for viewing / importing Meta base rates."""

    class Meta:
        model = MetaBaseRate
        fields = [
            "id",
            "destination_country",
            "message_type",
            "rate",
            "effective_from",
            "effective_to",
            "is_current",
        ]
        read_only_fields = ["id"]


class RateCardMarginSerializer(serializers.ModelSerializer):
    """Admin serializer for margin configuration."""
    specificity = serializers.IntegerField(read_only=True)

    class Meta:
        model = RateCardMargin
        fields = [
            "id",
            "tenant",
            "destination_country",
            "message_type",
            "margin_percent",
            "specificity",
        ]
        read_only_fields = ["id", "specificity"]
