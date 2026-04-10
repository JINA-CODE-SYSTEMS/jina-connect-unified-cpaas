class BaseTemplateMessagesViewSetMixin:
    
    
    
    filterset_fields = {
        "is_active": ["exact"],
        "tag": ["exact", "in", "isnull"],
        "tag__id": ["exact", "in"],
        "tag__name": ["exact", "icontains"],        
    }