"""
Custom filters for transaction viewsets to support historical broadcast filtering
"""

import django_filters
from django.db.models import Exists, OuterRef, Q

from transaction.models import TenantTransaction


class TenantTransactionFilter(django_filters.FilterSet):
    """
    Custom filterset that filters transactions based on historical broadcast state
    at the time of transaction, not current broadcast state.

    Uses database-level filtering to maintain pagination performance.

    Usage examples:
    - /transactions/?broadcast__status=SCHEDULED  (shows transactions when broadcast was SCHEDULED)
    - /transactions/?broadcast__scheduled_time__gte=2025-11-20  (historical scheduled_time)
    - /transactions/?broadcast__platform=WHATSAPP  (platform at transaction time)
    """

    # Standard transaction filters
    tenant_id = django_filters.NumberFilter(field_name="tenant_id")
    transaction_type = django_filters.CharFilter(field_name="transaction_type", lookup_expr="exact")
    transaction_type__in = django_filters.CharFilter(field_name="transaction_type", lookup_expr="in")
    created_at__gte = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_at__lte = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")
    amount__gte = django_filters.NumberFilter(field_name="amount", lookup_expr="gte")
    amount__lte = django_filters.NumberFilter(field_name="amount", lookup_expr="lte")
    tenant__name = django_filters.CharFilter(field_name="tenant__name", lookup_expr="exact")
    tenant__name__icontains = django_filters.CharFilter(field_name="tenant__name", lookup_expr="icontains")
    transaction_id = django_filters.CharFilter(field_name="transaction_id", lookup_expr="exact")
    transaction_id__icontains = django_filters.CharFilter(field_name="transaction_id", lookup_expr="icontains")

    # Basic broadcast filters
    broadcast = django_filters.NumberFilter(field_name="broadcast", lookup_expr="exact")
    broadcast__isnull = django_filters.BooleanFilter(field_name="broadcast", lookup_expr="isnull")

    # Historical broadcast filters (use state at transaction time)
    broadcast__status = django_filters.CharFilter(method="filter_by_historical_status")
    broadcast__status__in = django_filters.CharFilter(method="filter_by_historical_status_in")
    broadcast__name = django_filters.CharFilter(method="filter_by_historical_name")
    broadcast__name__icontains = django_filters.CharFilter(method="filter_by_historical_name_icontains")
    broadcast__scheduled_time__gte = django_filters.DateTimeFilter(method="filter_by_historical_scheduled_time_gte")
    broadcast__scheduled_time__lte = django_filters.DateTimeFilter(method="filter_by_historical_scheduled_time_lte")
    broadcast__platform = django_filters.CharFilter(method="filter_by_historical_platform")
    broadcast__platform__in = django_filters.CharFilter(method="filter_by_historical_platform_in")
    broadcast__created_at__gte = django_filters.DateTimeFilter(method="filter_by_historical_created_at_gte")
    broadcast__created_at__lte = django_filters.DateTimeFilter(method="filter_by_historical_created_at_lte")

    class Meta:
        model = TenantTransaction
        fields = []

    def _build_historical_filter(self, queryset, **historical_filters):
        """
        Build a database-level filter using historical records.
        Returns queryset filtered by historical state OR fallback to current state.

        Args:
            queryset: Base queryset
            **historical_filters: Field lookups for HistoricalBroadcast (e.g., status='SCHEDULED')
        """
        from broadcast.models import Broadcast

        # Get the HistoricalBroadcast model
        HistoricalBroadcast = Broadcast.history.model

        # Build subquery to check if historical record matches
        historical_subquery = HistoricalBroadcast.objects.filter(
            id=OuterRef("broadcast_id"), history_id=OuterRef("broadcast_history_id"), **historical_filters
        )

        # Filter: (has matching history) OR (no history but current broadcast matches)
        # Convert historical filters to current broadcast filters
        current_filters = {f"broadcast__{k}": v for k, v in historical_filters.items()}

        return queryset.filter(
            Q(broadcast_history_id__isnull=False, broadcast__isnull=False) & Q(Exists(historical_subquery))
            | Q(broadcast_history_id__isnull=True) & Q(**current_filters)
        )

    def filter_by_historical_status(self, queryset, name, value):
        """Filter by broadcast status at transaction time"""
        return self._build_historical_filter(queryset, status=value)

    def filter_by_historical_status_in(self, queryset, name, value):
        """Filter by broadcast status (multiple) at transaction time"""
        status_list = [s.strip() for s in value.split(",")]
        return self._build_historical_filter(queryset, status__in=status_list)

    def filter_by_historical_name(self, queryset, name, value):
        """Filter by broadcast name at transaction time"""
        return self._build_historical_filter(queryset, name=value)

    def filter_by_historical_name_icontains(self, queryset, name, value):
        """Filter by broadcast name (case-insensitive) at transaction time"""
        return self._build_historical_filter(queryset, name__icontains=value)

    def filter_by_historical_scheduled_time_gte(self, queryset, name, value):
        """Filter by broadcast scheduled_time >= value at transaction time"""
        return self._build_historical_filter(queryset, scheduled_time__gte=value)

    def filter_by_historical_scheduled_time_lte(self, queryset, name, value):
        """Filter by broadcast scheduled_time <= value at transaction time"""
        return self._build_historical_filter(queryset, scheduled_time__lte=value)

    def filter_by_historical_platform(self, queryset, name, value):
        """Filter by broadcast platform at transaction time"""
        return self._build_historical_filter(queryset, platform=value)

    def filter_by_historical_platform_in(self, queryset, name, value):
        """Filter by broadcast platform (multiple) at transaction time"""
        platform_list = [p.strip() for p in value.split(",")]
        return self._build_historical_filter(queryset, platform__in=platform_list)

    def filter_by_historical_created_at_gte(self, queryset, name, value):
        """Filter by broadcast created_at >= value at transaction time"""
        return self._build_historical_filter(queryset, created_at__gte=value)

    def filter_by_historical_created_at_lte(self, queryset, name, value):
        """Filter by broadcast created_at <= value at transaction time"""
        return self._build_historical_filter(queryset, created_at__lte=value)
