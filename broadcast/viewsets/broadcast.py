from django.conf import settings
from django.db.models import Count, Q
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from broadcast.models import UI_STATUS_TO_BROADCAST_STATUSES, Broadcast, MessageStatusChoices
from broadcast.serializers import BroadcastLimitedSerializer, BroadcastSerializer


class BroadcastViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing Broadcasts.

    Supports filtering by:
    - status: Internal status (DRAFT, SCHEDULED, QUEUED, SENDING, SENT, PARTIALLY_SENT, FAILED, CANCELLED)
    - ui_status: UI-friendly status (DRAFT, SCHEDULED, ONGOING, COMPLETED, FAILED, CANCELLED)

    Example: ?ui_status=COMPLETED will return broadcasts with status SENT or PARTIALLY_SENT
    """

    queryset = Broadcast.objects.all()
    serializer_class = BroadcastSerializer
    http_method_names = ["get"]
    required_permissions = {
        "list": "broadcast.view",
        "retrieve": "broadcast.view",
        "reserve_keyword_list": "broadcast.view",
        "min_scheduled_time": "broadcast.view",
        "default": "broadcast.view",
    }

    def get_serializer_class(self):
        """
        #251: MANAGER+ (priority >= 60) see cost fields.
        AGENT/VIEWER get BroadcastLimitedSerializer.
        """
        tu = self._get_tenant_user()
        if tu and tu.role and tu.role.priority >= 60:
            return BroadcastSerializer
        return BroadcastLimitedSerializer

    datetime_filter_fields = ["created_at", "scheduled_time"]
    filterset_fields = {
        "status": ["exact", "in"],
        "template_number__number": ["exact", "in"],
        "created_by": ["exact", "in"],
        "updated_by": ["exact", "in"],
        "platform": ["exact", "in"],
        "is_active": ["exact"],
        "scheduled_time": ["exact", "lt", "gt", "lte", "gte"],
        "tenant": ["exact", "in"],
        "created_at": ["exact", "lte", "gte"],
    }

    def get_queryset(self):
        """
        Annotate queryset with message status counts for efficient querying.
        Also handles ui_status filtering by mapping to internal statuses.
        """
        queryset = super().get_queryset()

        # Eager-load relations accessed in BroadcastSerializer.to_representation
        queryset = queryset.select_related(
            "template_number__gupshup_template__tenant_media",
        ).prefetch_related(
            "template_number__gupshup_template__card_media",
        )

        # Handle ui_status filter
        ui_status = self.request.query_params.get("ui_status")
        ui_status_in = self.request.query_params.get("ui_status__in")

        if ui_status:
            # Single ui_status filter
            internal_statuses = UI_STATUS_TO_BROADCAST_STATUSES.get(ui_status, [])
            if internal_statuses:
                queryset = queryset.filter(status__in=internal_statuses)
        elif ui_status_in:
            # Multiple ui_status filter (comma-separated)
            ui_statuses = [s.strip() for s in ui_status_in.split(",")]
            internal_statuses = []
            for ui_stat in ui_statuses:
                internal_statuses.extend(UI_STATUS_TO_BROADCAST_STATUSES.get(ui_stat, []))
            if internal_statuses:
                queryset = queryset.filter(status__in=internal_statuses)

        # Annotate with message status counts using conditional aggregation
        # Each annotation is EXCLUSIVE — a message is counted in exactly one bucket
        queryset = queryset.annotate(
            pending_count=Count("broadcasts", filter=Q(broadcasts__status=MessageStatusChoices.PENDING)),
            queued_count=Count("broadcasts", filter=Q(broadcasts__status=MessageStatusChoices.QUEUED)),
            sending_count=Count("broadcasts", filter=Q(broadcasts__status=MessageStatusChoices.SENDING)),
            sent_count=Count("broadcasts", filter=Q(broadcasts__status=MessageStatusChoices.SENT)),
            delivered_count=Count("broadcasts", filter=Q(broadcasts__status=MessageStatusChoices.DELIVERED)),
            read_count=Count("broadcasts", filter=Q(broadcasts__status=MessageStatusChoices.READ)),
            failed_count=Count("broadcasts", filter=Q(broadcasts__status=MessageStatusChoices.FAILED)),
            blocked_count=Count("broadcasts", filter=Q(broadcasts__status=MessageStatusChoices.BLOCKED)),
            success_count=Count(
                "broadcasts",
                filter=Q(
                    broadcasts__status__in=[
                        MessageStatusChoices.SENT,
                        MessageStatusChoices.DELIVERED,
                        MessageStatusChoices.READ,
                    ]
                ),
            ),
            total_messages=Count("broadcasts"),
        )

        return queryset

    @action(detail=False, methods=["get"], url_path="reserve-keyword")
    def reserve_keyword_list(self, request):
        """
        Returns list of reserved keywords that can be used in broadcast messages
        along with their descriptions.
        """
        data = Broadcast.RESERVED_VARS
        return Response(data, status=200)

    @action(detail=False, methods=["get"], url_path="min-schedule-time")
    def min_scheduled_time(self, request):
        min_time = settings.BROADCAST_CANCELLATION_TIME_LIMIT_IN_MINUTES
        return Response({"min_scheduled_time_in_minutes": min_time}, status=200)
