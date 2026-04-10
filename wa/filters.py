"""
Custom filters for WhatsApp models.

Provides advanced filtering capabilities including JSON field filters
for buttons and other complex data types.
"""

import django_filters
from django.db.models import Q

from .models import WATemplate


class WATemplateFilter(django_filters.FilterSet):
    """
    Custom filterset for WATemplate with button-type filters.
    
    Adds the following boolean filters:
    - has_buttons: Templates that have any buttons
    - has_quick_reply_buttons: Templates with QUICK_REPLY buttons
    - has_call_button: Templates with PHONE_NUMBER buttons
    - has_url_button: Templates with URL buttons
    """
    
    # Boolean filters for button types
    has_buttons = django_filters.BooleanFilter(
        method='filter_has_buttons',
        label='Has any buttons'
    )
    has_quick_reply_buttons = django_filters.BooleanFilter(
        method='filter_has_quick_reply_buttons',
        label='Has QUICK_REPLY buttons'
    )
    has_call_button = django_filters.BooleanFilter(
        method='filter_has_call_button',
        label='Has PHONE_NUMBER (call) buttons'
    )
    has_url_button = django_filters.BooleanFilter(
        method='filter_has_url_button',
        label='Has URL buttons'
    )
    has_otp_button = django_filters.BooleanFilter(
        method='filter_has_otp_button',
        label='Has OTP buttons'
    )
    
    # Standard field filters
    status = django_filters.CharFilter(lookup_expr='exact')
    status__in = django_filters.BaseInFilter(field_name='status')
    template_type = django_filters.CharFilter(lookup_expr='exact')
    template_type__in = django_filters.BaseInFilter(field_name='template_type')
    category = django_filters.CharFilter(lookup_expr='exact')
    category__in = django_filters.BaseInFilter(field_name='category')
    template_id = django_filters.CharFilter(lookup_expr='exact')
    template_id__icontains = django_filters.CharFilter(field_name='template_id', lookup_expr='icontains')
    content__icontains = django_filters.CharFilter(field_name='content', lookup_expr='icontains')
    number = django_filters.NumberFilter(lookup_expr='exact')
    wa_app__id = django_filters.NumberFilter(field_name='wa_app__id', lookup_expr='exact')
    wa_app__app_name = django_filters.CharFilter(field_name='wa_app__app_name', lookup_expr='exact')
    wa_app__app_name__icontains = django_filters.CharFilter(field_name='wa_app__app_name', lookup_expr='icontains')
    
    class Meta:
        model = WATemplate
        fields = []  # We define all filters explicitly above
    
    def filter_has_buttons(self, queryset, name, value):
        """
        Filter templates that have any buttons.
        
        Args:
            value: True = has buttons, False = no buttons
        """
        if value is None:
            return queryset
        
        if value:
            # Has buttons: buttons is not null and not empty
            return queryset.exclude(
                Q(buttons__isnull=True) | Q(buttons=[])
            )
        else:
            # No buttons: buttons is null or empty
            return queryset.filter(
                Q(buttons__isnull=True) | Q(buttons=[])
            )
    
    def filter_has_quick_reply_buttons(self, queryset, name, value):
        """
        Filter templates that have QUICK_REPLY buttons.
        
        Uses JSON contains lookup to check button types.
        """
        if value is None:
            return queryset
        
        # PostgreSQL JSON containment: buttons contains an object with type=QUICK_REPLY
        has_quick_reply = queryset.filter(
            buttons__contains=[{'type': 'QUICK_REPLY'}]
        )
        
        if value:
            return has_quick_reply
        else:
            return queryset.exclude(pk__in=has_quick_reply.values('pk'))
    
    def filter_has_call_button(self, queryset, name, value):
        """
        Filter templates that have PHONE_NUMBER (call) buttons.
        """
        if value is None:
            return queryset
        
        has_call = queryset.filter(
            buttons__contains=[{'type': 'PHONE_NUMBER'}]
        )
        
        if value:
            return has_call
        else:
            return queryset.exclude(pk__in=has_call.values('pk'))
    
    def filter_has_url_button(self, queryset, name, value):
        """
        Filter templates that have URL buttons.
        """
        if value is None:
            return queryset
        
        has_url = queryset.filter(
            buttons__contains=[{'type': 'URL'}]
        )
        
        if value:
            return has_url
        else:
            return queryset.exclude(pk__in=has_url.values('pk'))
    
    def filter_has_otp_button(self, queryset, name, value):
        """
        Filter templates that have OTP buttons.
        """
        if value is None:
            return queryset
        
        has_otp = queryset.filter(
            buttons__contains=[{'type': 'OTP'}]
        )
        
        if value:
            return has_otp
        else:
            return queryset.exclude(pk__in=has_otp.values('pk'))
