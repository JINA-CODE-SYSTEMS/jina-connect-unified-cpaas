"""
WAWebhookEvent ViewSet - BSP Agnostic Webhook Event Management

Provides read operations for WhatsApp Webhook Events.
Frontend uses this to view and debug webhook events.
"""

from django_filters import rest_framework as filters
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from wa.models import BSPChoices, WAWebhookEvent, WebhookEventType
from wa.serializers import WAWebhookEventListSerializer, WAWebhookEventSerializer


class WAWebhookEventFilter(filters.FilterSet):
    """Filter for WAWebhookEvent listing."""

    wa_app = filters.NumberFilter(field_name="wa_app__id")
    event_type = filters.ChoiceFilter(choices=WebhookEventType.choices)
    bsp = filters.ChoiceFilter(choices=BSPChoices.choices)
    is_processed = filters.BooleanFilter()

    # Date range filters
    created_after = filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")

    # Error filter
    has_error = filters.BooleanFilter(method="filter_has_error")

    def filter_has_error(self, queryset, name, value):
        if value:
            return queryset.exclude(error_message__isnull=True).exclude(error_message="")
        return queryset.filter(error_message__isnull=True) | queryset.filter(error_message="")

    class Meta:
        model = WAWebhookEvent
        fields = ["wa_app", "event_type", "bsp", "is_processed"]


class WAWebhookEventViewSet(BaseTenantModelViewSet):
    """
    ViewSet for viewing WhatsApp Webhook Events.

    Provides read-only endpoints to:
    - List webhook events with filtering
    - View event details and payload
    - Retry failed event processing

    These are primarily for debugging and monitoring purposes.
    """

    queryset = WAWebhookEvent.objects.select_related("wa_app", "message").all()
    serializer_class = WAWebhookEventSerializer
    filterset_class = WAWebhookEventFilter
    ordering_fields = ["created_at", "processed_at"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post"]  # POST only for retry action
    required_permissions = {
        "list": "webhook.view",
        "retrieve": "webhook.view",
        "retry": "webhook.manage",
        "statistics": "webhook.view",
        "default": "webhook.view",
    }

    def get_serializer_class(self):
        """Use list serializer for list action."""
        if self.action == "list":
            return WAWebhookEventListSerializer
        return WAWebhookEventSerializer

    def get_queryset(self):
        """
        Custom queryset to filter by tenant through wa_app relationship.
        """
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return queryset

        # Filter through wa_app -> tenant -> tenant_users -> user
        return queryset.filter(wa_app__tenant__tenant_users__user=user)

    @swagger_auto_schema(
        operation_description="List WhatsApp webhook events with filtering support",
        operation_summary="List Webhook Events",
        operation_id="list_wa_webhook_events",
        tags=["WhatsApp Webhook Events (v2)"],
        manual_parameters=[
            openapi.Parameter(
                "wa_app",
                openapi.IN_QUERY,
                description="Filter by WA App ID",
                type=openapi.TYPE_STRING,
                format="uuid",
                required=False,
            ),
            openapi.Parameter(
                "event_type",
                openapi.IN_QUERY,
                description="Filter by event type",
                type=openapi.TYPE_STRING,
                enum=["MESSAGE", "STATUS", "TEMPLATE", "BILLING", "ACCOUNT"],
                required=False,
            ),
            openapi.Parameter(
                "bsp",
                openapi.IN_QUERY,
                description="Filter by BSP",
                type=openapi.TYPE_STRING,
                enum=["META", "GUPSHUP", "TWILIO", "MESSAGEBIRD"],
                required=False,
            ),
            openapi.Parameter(
                "is_processed",
                openapi.IN_QUERY,
                description="Filter by processing status",
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                "has_error",
                openapi.IN_QUERY,
                description="Filter events with errors",
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                "created_after",
                openapi.IN_QUERY,
                description="Filter events after this datetime",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATETIME,
                required=False,
            ),
            openapi.Parameter(
                "created_before",
                openapi.IN_QUERY,
                description="Filter events before this datetime",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATETIME,
                required=False,
            ),
            openapi.Parameter(
                "ordering",
                openapi.IN_QUERY,
                description="Order results by field",
                type=openapi.TYPE_STRING,
                enum=["created_at", "-created_at", "processed_at", "-processed_at"],
                required=False,
            ),
        ],
        responses={
            200: openapi.Response(description="List of webhook events", schema=WAWebhookEventListSerializer(many=True)),
            401: openapi.Response(description="Authentication required"),
        },
    )
    def list(self, request, *args, **kwargs):
        """List WhatsApp webhook events with filtering support."""
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Retrieve a specific webhook event by ID",
        operation_summary="Get Webhook Event",
        operation_id="retrieve_wa_webhook_event",
        tags=["WhatsApp Webhook Events (v2)"],
        responses={
            200: openapi.Response(
                description="Webhook event details with full payload", schema=WAWebhookEventSerializer()
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Event not found"),
        },
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific webhook event by ID."""
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Retry processing a failed webhook event",
        operation_summary="Retry Event Processing",
        operation_id="retry_wa_webhook_event",
        tags=["WhatsApp Webhook Events (v2)"],
        responses={
            200: openapi.Response(
                description="Event queued for retry",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "message": openapi.Schema(type=openapi.TYPE_STRING),
                        "retry_count": openapi.Schema(type=openapi.TYPE_INTEGER),
                    },
                ),
            ),
            400: openapi.Response(description="Event already processed or max retries reached"),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Event not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        """Retry processing a failed webhook event."""
        event = self.get_object()

        if event.is_processed:
            return Response({"error": "Event already processed successfully"}, status=status.HTTP_400_BAD_REQUEST)

        max_retries = 5
        if event.retry_count >= max_retries:
            return Response({"error": f"Maximum retries ({max_retries}) reached"}, status=status.HTTP_400_BAD_REQUEST)

        # Increment retry count
        event.retry_count += 1
        event.error_message = None
        event.save(update_fields=["retry_count", "error_message"])

        # TODO: Trigger async task to reprocess webhook
        # process_webhook_event.delay(str(event.id))

        return Response({"message": "Event queued for retry", "retry_count": event.retry_count})

    @swagger_auto_schema(
        operation_description="Get webhook event processing statistics",
        operation_summary="Webhook Statistics",
        operation_id="wa_webhook_statistics",
        tags=["WhatsApp Webhook Events (v2)"],
        manual_parameters=[
            openapi.Parameter(
                "wa_app",
                openapi.IN_QUERY,
                description="Filter by WA App ID",
                type=openapi.TYPE_STRING,
                format="uuid",
                required=False,
            ),
        ],
        responses={
            200: openapi.Response(
                description="Webhook statistics",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "total": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "processed": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "pending": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "failed": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "by_event_type": openapi.Schema(type=openapi.TYPE_OBJECT),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
        },
    )
    @action(detail=False, methods=["get"], url_path="statistics")
    def statistics(self, request):
        """Get webhook event processing statistics."""
        from django.db.models import Count, Q

        queryset = self.filter_queryset(self.get_queryset())

        # Calculate statistics
        stats = queryset.aggregate(
            total=Count("id"),
            processed=Count("id", filter=Q(is_processed=True)),
            pending=Count("id", filter=Q(is_processed=False, error_message__isnull=True)),
            failed=Count("id", filter=Q(is_processed=False, error_message__isnull=False)),
        )

        # Count by event type
        by_type = queryset.values("event_type").annotate(count=Count("id"))
        by_event_type = {item["event_type"]: item["count"] for item in by_type}

        return Response(
            {
                "total": stats["total"],
                "processed": stats["processed"],
                "pending": stats["pending"],
                "failed": stats["failed"],
                "by_event_type": by_event_type,
            }
        )
