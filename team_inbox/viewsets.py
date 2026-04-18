"""
ViewSets for team inbox REST API endpoints
"""

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from team_inbox.models import Messages
from team_inbox.serializers import MessageCreateSerializer, MessageListSerializer, MessagesSerializer
from tenants.permission_classes import TenantRolePermission


class MessagesViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing team inbox messages
    Provides REST API endpoints for CRUD operations
    """

    queryset = (
        Messages.objects.all()
        .select_related("read_by", "outgoing_message")
        .prefetch_related("telegram_outbound", "sms_outbound_messages", "rcs_outbound_messages")
    )
    serializer_class = MessagesSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    required_permissions = {
        "list": "inbox.view",
        "retrieve": "inbox.view",
        "create": "inbox.reply",
        "partial_update": "inbox.reply",
        "recent": "inbox.view",
        "platforms": "inbox.view",
        "conversation": "inbox.view",
        "mark_as_read": "inbox.view",
        "mark_multiple_as_read": "inbox.view",
        "bulk_delete": "inbox.resolve",
        "default": "inbox.view",
    }

    # Filtering options
    filterset_fields = ["platform", "direction", "author", "timestamp"]
    ordering_fields = ["timestamp", "created_at"]
    ordering = ["-timestamp"]  # Default ordering by newest first
    search_fields = ["content__body__text", "=message_id__numbering"]

    # ── Role-scoped queryset ──────────────────────────────────────────

    def get_role_scoped_queryset(self, queryset, user, tenant_user):
        """
        Agents see only messages whose parent contact is assigned to them.
        """
        return queryset.filter(contact__assigned_to_user=user)

    def get_serializer_class(self):
        """
        Return appropriate serializer based on action
        """
        if self.action == "create":
            return MessageCreateSerializer
        elif self.action == "list":
            return MessageListSerializer
        return MessagesSerializer

    def create(self, request, *args, **kwargs):
        """
        Create a new message
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Generate unique message ID if not provided
        if "message_id" not in serializer.validated_data:
            import uuid

            serializer.validated_data["message_id"] = str(uuid.uuid4())

        self.perform_create(serializer)

        # Return full message data
        message = serializer.instance
        response_serializer = MessagesSerializer(message)

        headers = self.get_success_headers(response_serializer.data)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=["get"])
    def recent(self, request):
        """
        Get recent messages with pagination
        """
        limit = int(request.query_params.get("limit", 50))
        offset = int(request.query_params.get("offset", 0))

        queryset = self.get_queryset().order_by("-timestamp")[offset : offset + limit]
        serializer = MessageListSerializer(queryset, many=True)

        return Response(
            {
                "results": serializer.data,
                "count": self.get_queryset().count(),
                "limit": limit,
                "offset": offset,
                "has_next": (offset + limit) < self.get_queryset().count(),
            }
        )

    @action(detail=False, methods=["get"])
    def platforms(self, request):
        """
        Get message statistics by platform
        """
        from django.db.models import Count

        stats = self.get_queryset().values("platform").annotate(count=Count("id")).order_by("-count")

        return Response(stats)

    @action(detail=False, methods=["get"])
    def conversation(self, request):
        """
        Get conversation thread for a specific contact/platform
        """
        platform = request.query_params.get("platform")
        contact_id = request.query_params.get("contact_id")

        if not platform:
            return Response({"error": "Platform parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        queryset = self.get_queryset().filter(platform=platform)

        if contact_id:
            queryset = queryset.filter(contact_id=contact_id)

        queryset = queryset.order_by("timestamp")
        serializer = MessagesSerializer(queryset, many=True)

        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def mark_as_read(self, request, pk=None):
        """
        Mark a single incoming message as read.
        Only INCOMING messages that are not already read will be updated.
        """
        from django.utils import timezone

        message = self.get_object()

        if message.direction != "INCOMING":
            return Response(
                {"status": "Only incoming messages can be marked as read"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if message.is_read:
            return Response(
                {
                    "status": "Message already read",
                    "id": message.id,
                    "read_at": message.read_at,
                }
            )

        message.is_read = True
        message.read_at = timezone.now()
        message.read_by = request.user
        message.save(update_fields=["is_read", "read_at", "read_by"])

        return Response(
            {
                "status": "Message marked as read",
                "id": message.id,
                "read_at": message.read_at,
            }
        )

    @action(detail=False, methods=["post"])
    def mark_multiple_as_read(self, request):
        """
        Mark multiple incoming messages as read in bulk.
        Accepts either a list of PKs or a contact_id to mark all
        unread incoming messages for that contact.
        """
        from django.utils import timezone

        message_ids = request.data.get("message_ids", [])
        contact_id = request.data.get("contact_id")

        if not message_ids and not contact_id:
            return Response(
                {"error": "message_ids or contact_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = self.get_queryset().filter(direction="INCOMING", is_read=False)

        if contact_id and not message_ids:
            qs = qs.filter(contact_id=contact_id)
        else:
            qs = qs.filter(id__in=message_ids)

        marked_ids = list(qs.values_list("id", flat=True))

        if marked_ids:
            qs.filter(id__in=marked_ids).update(
                is_read=True,
                read_at=timezone.now(),
                read_by=request.user,
            )

        return Response(
            {
                "status": f"{len(marked_ids)} messages marked as read",
                "marked_ids": marked_ids,
            }
        )

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """
        Delete multiple messages
        """
        message_ids = request.data.get("message_ids", [])

        if not message_ids:
            return Response({"error": "message_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

        deleted_count, _ = self.get_queryset().filter(message_id__in=message_ids).delete()

        return Response({"status": f"{deleted_count} messages deleted", "deleted_count": deleted_count})


class TeamInboxStatsViewSet(viewsets.GenericViewSet):
    """
    ViewSet for team inbox statistics and analytics
    """

    permission_classes = [IsAuthenticated, TenantRolePermission]
    swagger_schema = None  # exclude from Swagger (mock endpoints, no serializers)
    required_permissions = {
        "dashboard": "analytics.view",
        "overview": "analytics.view",
        "platform_activity": "analytics.view",
        "default": "analytics.view",
    }

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        """
        Get inbox dashboard statistics with date range filtering (#483).

        Query Parameters:
            start_date (str): Start date in YYYY-MM-DD format (required)
            end_date (str): End date in YYYY-MM-DD format (required)

        Returns real counts from TenantContact + Event models.
        """
        from datetime import datetime, timedelta

        from django.db.models import Avg, Exists, F, OuterRef

        from contacts.models import AssigneeTypeChoices, TicketStatusChoices
        from team_inbox.models import Event, EventTypeChoices, Messages

        # ── Parse & validate date params ──────────────────────────────
        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")

        if not start_date_str or not end_date_str:
            return Response(
                {"error": "Both start_date and end_date parameters are required (format: YYYY-MM-DD)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"error": "Invalid date format. Use YYYY-MM-DD"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if start_date > end_date:
            return Response(
                {"error": "start_date must be before or equal to end_date"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Tenant scoping ────────────────────────────────────────────
        tenant = request.user.tenant
        if not tenant:
            return Response(
                {"error": "Could not determine tenant"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Helper: compute stats for a date range ────────────────────
        def _stats_for_range(t_start, t_end):
            # "Active" = contacts that had at least one message in the period.
            has_msg_in_period = Exists(
                Messages.objects.filter(
                    contact=OuterRef("pk"),
                    timestamp__date__gte=t_start,
                    timestamp__date__lte=t_end,
                )
            )
            active_qs = tenant.contacts.filter(has_msg_in_period)

            total = active_qs.count()
            open_count = active_qs.filter(status=TicketStatusChoices.OPEN).count()

            # "Closed" = distinct contacts with a TICKET_CLOSED event in the period.
            closed_count = (
                Event.objects.filter(
                    tenant=tenant,
                    event_type=EventTypeChoices.TICKET_CLOSED,
                    timestamp__date__gte=t_start,
                    timestamp__date__lte=t_end,
                )
                .values("contact_id")
                .distinct()
                .count()
            )

            # "Pending" = open + unassigned (awaiting agent).
            pending_count = active_qs.filter(
                status=TicketStatusChoices.OPEN,
                assigned_to_type=AssigneeTypeChoices.UNASSIGNED,
            ).count()

            # "Active" = open + assigned (being handled by an agent).
            active_count = open_count - pending_count

            return total, open_count, closed_count, pending_count, active_count

        # ── Current period stats ──────────────────────────────────────
        total, open_count, closed_count, pending_count, active_count = _stats_for_range(start_date, end_date)

        # ── Avg resolution time (minutes) ─────────────────────────────
        avg_resolution = (
            Event.objects.filter(
                tenant=tenant,
                event_type=EventTypeChoices.TICKET_CLOSED,
                timestamp__date__gte=start_date,
                timestamp__date__lte=end_date,
            )
            .annotate(resolution=F("timestamp") - F("contact__created_at"))
            .aggregate(avg=Avg("resolution"))
        )["avg"]

        avg_resolution_minutes = None
        if avg_resolution is not None:
            avg_resolution_minutes = round(avg_resolution.total_seconds() / 60, 1)

        # ── Comparison vs previous equivalent period ──────────────────
        period_days = (end_date - start_date).days + 1
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        prev_total, prev_open, prev_closed, _, _ = _stats_for_range(prev_start, prev_end)

        def _pct_change(current, previous):
            if previous == 0:
                return None
            return round(((current - previous) / previous) * 100, 1)

        comparison = {
            "total_change_percent": _pct_change(total, prev_total),
            "open_change_percent": _pct_change(open_count, prev_open),
            "closed_change_percent": _pct_change(closed_count, prev_closed),
        }

        return Response(
            {
                "total_chats": total,
                "open_chats": open_count,
                "closed_chats": closed_count,
                "pending_chats": pending_count,
                "active_chats": active_count,
                "avg_resolution_time_minutes": avg_resolution_minutes,
                "date_range": {
                    "start_date": start_date_str,
                    "end_date": end_date_str,
                },
                "comparison": comparison,
            }
        )

    @action(detail=False, methods=["get"])
    def overview(self, request):
        """
        Get overall team inbox statistics
        """
        from datetime import timedelta

        from django.db.models import Count
        from django.utils import timezone

        tenant_id = getattr(request, "tenant_id", None)
        if not tenant_id:
            return Response({"error": "Tenant context required"}, status=status.HTTP_400_BAD_REQUEST)

        queryset = Messages.objects.filter(tenant_id=tenant_id)

        # Overall stats
        total_messages = queryset.count()

        # Today's stats
        today = timezone.now().date()
        today_messages = queryset.filter(timestamp__date=today).count()

        # Platform breakdown
        platform_stats = queryset.values("platform").annotate(count=Count("id")).order_by("-count")

        # Direction breakdown
        direction_stats = queryset.values("direction").annotate(count=Count("id"))

        # Recent activity (last 7 days)
        week_ago = timezone.now() - timedelta(days=7)
        recent_activity = []

        for i in range(7):
            day = week_ago + timedelta(days=i)
            day_count = queryset.filter(timestamp__date=day.date()).count()
            recent_activity.append({"date": day.strftime("%Y-%m-%d"), "count": day_count})

        return Response(
            {
                "total_messages": total_messages,
                "today_messages": today_messages,
                "platform_stats": platform_stats,
                "direction_stats": direction_stats,
                "recent_activity": recent_activity,
            }
        )

    @action(detail=False, methods=["get"])
    def platform_activity(self, request):
        """
        Get detailed activity by platform
        """
        platform = request.query_params.get("platform")
        days = int(request.query_params.get("days", 30))

        tenant_id = getattr(request, "tenant_id", None)
        if not tenant_id:
            return Response({"error": "Tenant context required"}, status=status.HTTP_400_BAD_REQUEST)

        queryset = Messages.objects.filter(tenant_id=tenant_id)

        if platform:
            queryset = queryset.filter(platform=platform)

        # Activity over time
        from datetime import timedelta

        from django.utils import timezone

        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)

        activity_data = []

        for i in range(days):
            day = start_date + timedelta(days=i)
            day_count = queryset.filter(timestamp__date=day.date()).count()

            activity_data.append({"date": day.strftime("%Y-%m-%d"), "count": day_count})

        return Response({"platform": platform or "all", "days": days, "activity": activity_data})
