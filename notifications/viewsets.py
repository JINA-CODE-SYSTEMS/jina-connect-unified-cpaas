from abstract.viewsets.base import BaseTenantModelViewSet
from notifications.models import Notification
from notifications.serializers import NotificationSerializer
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response


class NotificationViewSet(BaseTenantModelViewSet):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    http_method_names = ['get', 'post', 'patch']
    required_permissions = {
        "list": "analytics.view",
        "retrieve": "analytics.view",
        "read": "analytics.view",
        "mark_all_read": "analytics.view",
        "unread_count": "analytics.view",
        "default": "analytics.view",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            qs = qs.filter(is_read=is_read.lower() == 'true')
        notif_type = self.request.query_params.get('type')
        if notif_type:
            qs = qs.filter(notification_type=notif_type)
        return qs

    @action(detail=True, methods=['post'], url_path='read')
    def read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=['is_read'])
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=['patch'], url_path='mark-all-read')
    def mark_all_read(self, request):
        qs = super().get_queryset().filter(is_read=False)
        updated = qs.update(is_read=True)
        return Response({'updated': updated})

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        count = super().get_queryset().filter(is_read=False).count()
        return Response({'unread_count': count})
