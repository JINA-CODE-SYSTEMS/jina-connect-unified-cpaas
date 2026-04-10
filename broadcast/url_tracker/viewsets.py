"""
ViewSet for URL tracking analytics (tenant-scoped, authenticated).

Endpoints:
    GET /broadcast/url-tracking/                           → list all tracked URLs for tenant
    GET /broadcast/url-tracking/<id>/                      → detail of a tracked URL
    GET /broadcast/url-tracking/<id>/clicks/               → click events for a tracked URL
    GET /broadcast/url-tracking/broadcast/<broadcast_id>/  → aggregated analytics for a broadcast
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from broadcast.url_tracker.models import TrackedURL, TrackedURLClick
from broadcast.url_tracker.serializers import (
    BroadcastClickAnalyticsSerializer,
    TrackedURLClickSerializer,
    TrackedURLSerializer,
)
from broadcast.url_tracker.service import (
    get_click_analytics_for_broadcast,
    get_click_analytics_for_message,
)


class TrackedURLViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only viewset for tracked URL analytics.
    Tenant-scoped: users can only see tracked URLs belonging to their tenant.
    """

    serializer_class = TrackedURLSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter tracked URLs by the requesting user's tenant."""
        user = self.request.user
        qs = TrackedURL.objects.select_related(
            'contact', 'broadcast', 'broadcast_message'
        ).filter(
            tenant__tenant_users__user=user
        )

        # Optional filters via query params
        broadcast_id = self.request.query_params.get('broadcast_id')
        if broadcast_id:
            qs = qs.filter(broadcast_id=broadcast_id)

        contact_id = self.request.query_params.get('contact_id')
        if contact_id:
            qs = qs.filter(contact_id=contact_id)

        # Only show URLs that have been clicked (optional filter)
        clicked_only = self.request.query_params.get('clicked_only', '').lower()
        if clicked_only in ('true', '1', 'yes'):
            qs = qs.filter(click_count__gt=0)

        return qs.order_by('-created_at')

    @action(detail=True, methods=['get'])
    def clicks(self, request, pk=None):
        """List individual click events for a specific tracked URL."""
        tracked_url = self.get_object()
        clicks = TrackedURLClick.objects.filter(
            tracked_url=tracked_url
        ).order_by('-clicked_at')

        # Paginate
        page = self.paginate_queryset(clicks)
        if page is not None:
            serializer = TrackedURLClickSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = TrackedURLClickSerializer(clicks, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path=r'broadcast/(?P<broadcast_id>\d+)')
    def broadcast_analytics(self, request, broadcast_id=None):
        """
        Get aggregated click analytics for a broadcast.
        
        Response:
            {
                "total_tracked_urls": 150,
                "total_clicks": 42,
                "unique_contacts_clicked": 38,
                "buttons": [
                    {
                        "button_index": 0,
                        "button_text": "Shop Now",
                        "original_url": "https://shop.com/sale",
                        "total_clicks": 42,
                        "unique_clickers": 38,
                        "first_click": "2026-02-25T10:30:00Z",
                        "last_click": "2026-02-25T14:20:00Z"
                    }
                ]
            }
        """
        # Verify the user has access to this broadcast's tenant
        from broadcast.models import Broadcast
        try:
            broadcast = Broadcast.objects.filter(
                tenant__tenant_users__user=request.user,
                id=broadcast_id
            ).first()

            if not broadcast:
                return Response(
                    {'error': 'Broadcast not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        except Exception:
            return Response(
                {'error': 'Broadcast not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        analytics = get_click_analytics_for_broadcast(int(broadcast_id))
        serializer = BroadcastClickAnalyticsSerializer(analytics)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path=r'message/(?P<message_id>\d+)')
    def message_analytics(self, request, message_id=None):
        """
        Get click analytics for a specific broadcast message (single recipient).
        """
        from broadcast.models import BroadcastMessage
        try:
            message = BroadcastMessage.objects.filter(
                broadcast__tenant__tenant_users__user=request.user,
                id=message_id
            ).first()

            if not message:
                return Response(
                    {'error': 'Message not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        except Exception:
            return Response(
                {'error': 'Message not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        analytics = get_click_analytics_for_message(int(message_id))
        return Response(analytics)
