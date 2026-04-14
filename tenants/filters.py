"""
Custom filters for Tenant model.
"""

import django_filters
from django.db.models import Count, Q

from tenants.models import Tenant


class TenantFilter(django_filters.FilterSet):
    """
    Custom FilterSet for Tenant model with advanced filtering options.

    Filters:
    - name: Exact match or case-insensitive contains
    - created_at: Greater than or equal (gte), less than or equal (lte)
    - contacts_count__gte: Filter tenants with contacts count >= specified value
    - contacts_count__lte: Filter tenants with contacts count <= specified value
    - product: Filter by product type (all, wa, sms)
    """

    # Standard filters (same as before)
    name = django_filters.CharFilter(lookup_expr="icontains", field_name="name")
    name__exact = django_filters.CharFilter(field_name="name", lookup_expr="exact")
    created_at__gte = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_at__lte = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")

    # Custom contacts count filters
    contacts_count__gte = django_filters.NumberFilter(method="filter_contacts_count_gte")
    contacts_count__lte = django_filters.NumberFilter(method="filter_contacts_count_lte")

    # balance
    balance__gte = django_filters.NumberFilter(field_name="balance", lookup_expr="gte")
    balance__lte = django_filters.NumberFilter(field_name="balance", lookup_expr="lte")

    # credit line
    credit_line__gte = django_filters.NumberFilter(field_name="credit_line", lookup_expr="gte")
    credit_line__lte = django_filters.NumberFilter(field_name="credit_line", lookup_expr="lte")

    # threshold alert
    threshold_alert__gte = django_filters.NumberFilter(field_name="threshold_alert", lookup_expr="gte")
    threshold_alert__lte = django_filters.NumberFilter(field_name="threshold_alert", lookup_expr="lte")

    # Product filter (wa, sms, all)
    product = django_filters.CharFilter(method="filter_by_product")

    class Meta:
        model = Tenant
        fields = ["name", "created_at", "balance", "credit_line", "threshold_alert"]

    def filter_contacts_count_gte(self, queryset, name, value):
        """
        Filter tenants with contacts count >= specified value.
        """
        return queryset.annotate(contacts_count=Count("contacts")).filter(contacts_count__gte=value)

    def filter_contacts_count_lte(self, queryset, name, value):
        """
        Filter tenants with contacts count <= specified value.
        """
        return queryset.annotate(contacts_count=Count("contacts")).filter(contacts_count__lte=value)

    def filter_by_product(self, queryset, name, value):
        """
        Filter tenants by product type.

        Args:
            value (str): Product type - 'wa' (WhatsApp), 'sms', or 'all'

        Returns:
            Filtered queryset based on product type
        """
        value_lower = value.lower()

        if value_lower == "wa" or value_lower == "whatsapp":
            # Filter tenants that have at least one active Gupshup app (WhatsApp)
            return queryset.filter(wa_apps__is_active=True).distinct()

        elif value_lower == "sms":
            return queryset.filter(sms_apps__is_active=True).distinct()

        elif value_lower == "all":
            # Return tenants that have either WhatsApp or SMS
            return queryset.filter(Q(wa_apps__is_active=True) | Q(sms_apps__is_active=True)).distinct()

        else:
            # Invalid product type, return empty queryset
            return queryset.none()
