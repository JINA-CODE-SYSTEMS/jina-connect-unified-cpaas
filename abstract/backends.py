from django_filters.rest_framework import DjangoFilterBackend
from django.http import QueryDict


class DateTimeAwareFilterBackend(DjangoFilterBackend):
    """
    Custom filter backend that properly handles date-only inputs for DateTimeField filtering.
    
    When a date-only value (e.g., 2026-01-05) is passed to a __lte filter,
    it adjusts the value to end of day (23:59:59.999999) so that filtering
    for a single day returns all records from that entire day.
    
    Usage:
        1. Add this as filter_backends in your viewset
        2. Define `datetime_filter_fields` in your viewset with the list of datetime fields
        
    Example:
        class MyViewSet(ModelViewSet):
            filter_backends = [DateTimeAwareFilterBackend]
            datetime_filter_fields = ['created_at', 'scheduled_time', 'updated_at']
            filterset_fields = {
                'created_at': ['exact', 'lte', 'gte'],
                ...
            }
    """
    
    def get_filterset_kwargs(self, request, queryset, view):
        kwargs = super().get_filterset_kwargs(request, queryset, view)
        
        # Get datetime fields from the view, default to common fields if not specified
        datetime_fields = getattr(view, 'datetime_filter_fields', [])
        
        if not datetime_fields:
            return kwargs
        
        # Get the original query params
        data = kwargs.get('data')
        if data:
            # Create a mutable copy
            mutable_data = data.copy()
            
            for field in datetime_fields:
                lte_key = f'{field}__lte'
                if lte_key in mutable_data:
                    value = mutable_data[lte_key]
                    # Check if it's a date-only value (no time component)
                    if value and 'T' not in value and ' ' not in value and len(value) == 10:
                        # Append end of day time
                        mutable_data[lte_key] = f'{value}T23:59:59.999999'
            
            kwargs['data'] = mutable_data
        
        return kwargs
