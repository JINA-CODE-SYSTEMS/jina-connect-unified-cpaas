"""
Custom filters for TenantContact model.
"""
import django_filters
from django.db.models import Q
from contacts.models import TenantContact


class TenantContactFilter(django_filters.FilterSet):
    """
    Custom FilterSet for TenantContact model with keyword search.
    
    Filters:
    - search: Keyword search across first_name, last_name, and phone
    - All standard filterset_fields are preserved
    """
    
    search = django_filters.CharFilter(method='filter_search')
    
    class Meta:
        model = TenantContact
        fields = {
            "tenant": ["exact", "in"],
            "created_by": ["exact", "in"],
            "updated_by": ["exact", "in"],
            "is_active": ["exact"],
            "phone": ["exact", "in", "icontains", "istartswith"],
            "first_name": ["exact", "in", "icontains", "istartswith"],
            "last_name": ["exact", "in", "icontains", "istartswith"],
            "tag": ["exact", "in", "icontains", "istartswith"],
            "source": ["exact", "in"],
        }
    
    def filter_search(self, queryset, name, value):
        """
        Filter contacts by keyword search across multiple fields.
        Searches in: first_name, last_name, phone (case-insensitive contains)
        """
        if not value:
            return queryset
        
        return queryset.filter(
            Q(first_name__icontains=value) |
            Q(last_name__icontains=value) |
            Q(phone__icontains=value)
        )
