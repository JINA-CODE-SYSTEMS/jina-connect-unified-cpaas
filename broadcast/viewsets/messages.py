from django.db.models import Count
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from broadcast.models import BroadcastMessage, MessageStatusChoices
from broadcast.serializers import BroadcastMessageSerializer


class BroadcastMessageViewSet(BaseTenantModelViewSet):
    required_permissions = {
        "list": "broadcast.view",
        "retrieve": "broadcast.view",
        "message_stats": "broadcast.view",
        "default": "broadcast.view",
    }
    """
    ViewSet for managing Broadcast Messages.
    """
    queryset = BroadcastMessage.objects.all()
    serializer_class = BroadcastMessageSerializer
    http_method_names = ["get"]
    filterset_fields = {
        "status": ["exact", "in"],
        "broadcast": ["exact", "in"],
        "broadcast__status": ["exact", "in"],
        "broadcast__scheduled_time": ["exact", "lt", "gt", "lte", "gte"],
        "broadcast__platform": ["exact", "in"],
        "broadcast__tenant": ["exact", "in"],
        "contact": ["exact", "in"],
        "contact__phone": ["exact", "icontains"],
        "contact__first_name": ["exact", "icontains"],
        "contact__last_name": ["exact", "icontains"],
        "retry_count": ["exact", "lt", "gt", "lte", "gte"],
        "created_at": ["exact", "lt", "gt", "lte", "gte"],
        "updated_at": ["exact", "lt", "gt", "lte", "gte"],
    }

    @action(detail=False, methods=["get"], url_path="stats")
    def message_stats(self, request):
        """
        Get total message count and breakdown by status

        Query Parameters:
        - broadcast__scheduled_time__gte: Filter messages from broadcasts scheduled on or after this datetime
        - broadcast__scheduled_time__lte: Filter messages from broadcasts scheduled on or before this datetime
        - Other filters from filterset_fields are also supported
        """
        queryset = self.filter_queryset(self.get_queryset())

        # Get total count
        total_messages = queryset.count()

        # Get count by status
        status_breakdown = queryset.values("status").annotate(count=Count("id")).order_by("status")

        # Create a dict with all statuses (including zeros)
        status_counts = {
            MessageStatusChoices.PENDING: 0,
            MessageStatusChoices.QUEUED: 0,
            MessageStatusChoices.SENDING: 0,
            MessageStatusChoices.SENT: 0,
            MessageStatusChoices.DELIVERED: 0,
            MessageStatusChoices.READ: 0,
            MessageStatusChoices.FAILED: 0,
            MessageStatusChoices.BLOCKED: 0,
        }

        # Update with actual counts
        for item in status_breakdown:
            status_counts[item["status"]] = item["count"]

        # Calculate success count (sent + delivered + read)
        success_count = (
            status_counts[MessageStatusChoices.SENT]
            + status_counts[MessageStatusChoices.DELIVERED]
            + status_counts[MessageStatusChoices.READ]
        )

        # Calculate sent count (all messages that were attempted to send)
        # This includes: sent + delivered + read + failed + blocked
        sent_count = (
            status_counts[MessageStatusChoices.SENT]
            + status_counts[MessageStatusChoices.DELIVERED]
            + status_counts[MessageStatusChoices.READ]
            + status_counts[MessageStatusChoices.FAILED]
            + status_counts[MessageStatusChoices.BLOCKED]
        )

        # Calculate failed count (failed + blocked)
        failed_count = status_counts[MessageStatusChoices.FAILED] + status_counts[MessageStatusChoices.BLOCKED]

        # Calculate pending count (pending + queued)
        pending_count = status_counts[MessageStatusChoices.PENDING] + status_counts[MessageStatusChoices.QUEUED]

        return Response(
            {
                "total_messages": total_messages,
                "success_count": success_count,
                # UI-friendly counts
                "ui_status_breakdown": {
                    "pending": pending_count,  # PENDING + QUEUED
                    "sending": status_counts[MessageStatusChoices.SENDING],
                    "sent": sent_count,  # SENT + DELIVERED + READ + FAILED + BLOCKED
                    "failed": failed_count,  # FAILED + BLOCKED
                },
                # Detailed status breakdown
                "status_breakdown": {
                    "pending": status_counts[MessageStatusChoices.PENDING],
                    "queued": status_counts[MessageStatusChoices.QUEUED],
                    "sending": status_counts[MessageStatusChoices.SENDING],
                    "sent": status_counts[MessageStatusChoices.SENT],
                    "delivered": status_counts[MessageStatusChoices.DELIVERED],
                    "read": status_counts[MessageStatusChoices.READ],
                    "failed": status_counts[MessageStatusChoices.FAILED],
                    "blocked": status_counts[MessageStatusChoices.BLOCKED],
                },
            },
            status=200,
        )
