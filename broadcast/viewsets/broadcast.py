from django.conf import settings
from django.db.models import Count, Q
from rest_framework import status as drf_status
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
    http_method_names = ["get", "post"]
    required_permissions = {
        "list": "broadcast.view",
        "retrieve": "broadcast.view",
        "reserve_keyword_list": "broadcast.view",
        "min_scheduled_time": "broadcast.view",
        "cancel": "broadcast.cancel",
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

    def create(self, request, *args, **kwargs):
        """Block create on the generic base viewsets; delegate for channel subclasses.

        ``BroadcastViewSet`` and ``MobileBroadcastViewSet`` are channel-agnostic
        and must not create broadcasts directly — callers must use a
        channel-specific endpoint (WA, SMS, RCS, Telegram, ...).

        Channel subclasses are expected to either:
          * override ``create()`` and call ``super().create(...)`` (e.g.
            ``WABroadcastViewSet`` does this to attach low-balance warnings), or
          * delegate directly to ``CreateModelMixin.create`` themselves.

        The ``super().create(...)`` call below is the path taken by subclasses
        in the first category and is therefore not dead code.
        """
        if type(self).__name__ in {"BroadcastViewSet", "MobileBroadcastViewSet"}:
            return Response(
                {"detail": "Use a channel-specific broadcast endpoint to create broadcasts."},
                status=drf_status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        return super().create(request, *args, **kwargs)

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

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """Cancel a SCHEDULED or SENDING broadcast (#102).

        - Revokes the Celery task if one is tracked.
        - Transitions status → CANCELLED.
        - Marks all PENDING/QUEUED BroadcastMessages as FAILED.
        """
        from django.db import transaction

        from broadcast.models import BroadcastStatusChoices, MessageStatusChoices
        from broadcast.tasks import cancel_broadcast_task

        with transaction.atomic():
            broadcast = Broadcast.objects.select_for_update().get(pk=pk)

            cancellable = {
                BroadcastStatusChoices.SCHEDULED,
                BroadcastStatusChoices.QUEUED,
                BroadcastStatusChoices.SENDING,
            }
            if broadcast.status not in cancellable:
                return Response(
                    {"detail": f"Cannot cancel broadcast in status {broadcast.status}."},
                    status=400,
                )

            # Revoke Celery task
            if broadcast.task_id:
                cancel_broadcast_task.delay(broadcast.task_id)

            reason = request.data.get("reason", "Cancelled by user")
            broadcast.status = BroadcastStatusChoices.CANCELLED
            broadcast.reason_for_cancellation = reason
            broadcast.save(update_fields=["status", "reason_for_cancellation"])

        # Mark unsent messages as FAILED (outside lock)
        updated = broadcast.broadcasts.filter(
            status__in=[MessageStatusChoices.PENDING, MessageStatusChoices.QUEUED],
        ).update(status=MessageStatusChoices.FAILED)

        return Response(
            {"detail": "Broadcast cancelled.", "messages_cancelled": updated},
            status=200,
        )
