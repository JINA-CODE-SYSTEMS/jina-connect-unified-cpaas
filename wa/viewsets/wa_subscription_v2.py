"""
WASubscription ViewSet - BSP Agnostic Webhook Subscription Management

Provides CRUD operations for WhatsApp Webhook Subscriptions.
Frontend uses this to manage webhook endpoints for receiving events.
"""

from django_filters import rest_framework as filters
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from wa.models import SubscriptionStatus, WASubscription
from wa.serializers import WASubscriptionV2ListSerializer, WASubscriptionV2Serializer


class WASubscriptionV2Filter(filters.FilterSet):
    """Filter for WASubscription listing."""

    wa_app = filters.UUIDFilter(field_name="wa_app__id")
    status = filters.ChoiceFilter(choices=SubscriptionStatus.choices)
    is_active = filters.BooleanFilter()

    class Meta:
        model = WASubscription
        fields = ["wa_app", "status", "is_active"]


class WASubscriptionV2ViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing WhatsApp Webhook Subscriptions (v2).

    Provides endpoints to:
    - List webhook subscriptions
    - Create new webhook subscriptions
    - Update subscription configurations
    - Activate/deactivate subscriptions
    - Test webhook endpoints

    All operations are tenant-scoped through wa_app relationship.
    """

    queryset = WASubscription.objects.select_related("wa_app").all()
    serializer_class = WASubscriptionV2Serializer
    filterset_class = WASubscriptionV2Filter
    search_fields = ["name", "webhook_url"]
    ordering_fields = ["created_at", "updated_at", "name"]
    ordering = ["-created_at"]
    required_permissions = {
        "list": "wa_app.view",
        "retrieve": "wa_app.view",
        "create": "wa_app.manage",
        "partial_update": "wa_app.manage",
        "activate": "wa_app.manage",
        "deactivate": "wa_app.manage",
        "test": "wa_app.manage",
        "event_types": "wa_app.view",
        "refresh": "wa_app.manage",
        "default": "wa_app.view",
    }

    def get_serializer_class(self):
        """Use list serializer for list action."""
        if self.action == "list":
            return WASubscriptionV2ListSerializer
        return WASubscriptionV2Serializer

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
        operation_description="List all webhook subscriptions for the tenant",
        operation_summary="List Subscriptions",
        operation_id="list_wa_subscriptions_v2",
        tags=["WhatsApp Subscriptions (v2)"],
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
                "status",
                openapi.IN_QUERY,
                description="Filter by subscription status",
                type=openapi.TYPE_STRING,
                enum=["ACTIVE", "INACTIVE", "PENDING", "FAILED"],
                required=False,
            ),
            openapi.Parameter(
                "is_active",
                openapi.IN_QUERY,
                description="Filter by active status",
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Search in name, webhook_url",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "ordering",
                openapi.IN_QUERY,
                description="Order results by field",
                type=openapi.TYPE_STRING,
                enum=["created_at", "-created_at", "name", "-name"],
                required=False,
            ),
        ],
        responses={
            200: openapi.Response(
                description="List of subscriptions", schema=WASubscriptionV2ListSerializer(many=True)
            ),
            401: openapi.Response(description="Authentication required"),
        },
    )
    def list(self, request, *args, **kwargs):
        """List all webhook subscriptions for the tenant."""
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Create a new webhook subscription",
        operation_summary="Create Subscription",
        operation_id="create_wa_subscription_v2",
        tags=["WhatsApp Subscriptions (v2)"],
        request_body=WASubscriptionV2Serializer,
        responses={
            201: openapi.Response(description="Subscription created successfully", schema=WASubscriptionV2Serializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a new webhook subscription and register with BSP."""
        from wa.adapters import get_bsp_adapter

        # Step 0 — check that the BSP supports subscriptions.
        wa_app_id = request.data.get("wa_app")
        if wa_app_id:
            from wa.models import WAApp

            try:
                wa_app = WAApp.objects.get(pk=wa_app_id)
                adapter = get_bsp_adapter(wa_app)
                if not adapter.supports("subscriptions"):
                    return Response(
                        {
                            "error": (f"{adapter.PROVIDER_NAME} does not support webhook subscriptions via API."),
                        },
                        status=status.HTTP_501_NOT_IMPLEMENTED,
                    )
            except WAApp.DoesNotExist:
                pass  # let normal validation handle missing wa_app

        # Step 1 — persist the subscription (status=PENDING).
        response = super().create(request, *args, **kwargs)

        if response.status_code != 201:
            return response

        # Step 2 — register on BSP.
        try:
            from wa.models import WASubscription

            subscription = WASubscription.objects.get(pk=response.data["id"])
            adapter = get_bsp_adapter(subscription.wa_app)
            adapter.register_webhook(subscription)
            subscription.refresh_from_db()

            # Re-serialise so the response includes updated status / bsp_subscription_id.
            serializer = self.get_serializer(subscription)
            response.data = serializer.data
        except NotImplementedError:
            # No adapter yet — subscription stays PENDING.
            pass
        except Exception:
            # Non-fatal — subscription is saved, BSP registration can be retried.
            pass

        return response

    @swagger_auto_schema(
        operation_description="Retrieve a specific subscription by ID",
        operation_summary="Get Subscription",
        operation_id="retrieve_wa_subscription_v2",
        tags=["WhatsApp Subscriptions (v2)"],
        responses={
            200: openapi.Response(description="Subscription details", schema=WASubscriptionV2Serializer()),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Subscription not found"),
        },
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific subscription by ID."""
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Partially update a subscription",
        operation_summary="Update Subscription",
        operation_id="partial_update_wa_subscription_v2",
        tags=["WhatsApp Subscriptions (v2)"],
        request_body=WASubscriptionV2Serializer,
        responses={
            200: openapi.Response(description="Subscription updated successfully", schema=WASubscriptionV2Serializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Subscription not found"),
        },
    )
    def partial_update(self, request, *args, **kwargs):
        """Partially update a subscription."""
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Activate a webhook subscription",
        operation_summary="Activate Subscription",
        operation_id="activate_wa_subscription_v2",
        tags=["WhatsApp Subscriptions (v2)"],
        responses={
            200: openapi.Response(
                description="Subscription activated",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "message": openapi.Schema(type=openapi.TYPE_STRING),
                        "status": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            400: openapi.Response(description="Cannot activate subscription"),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Subscription not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        """Activate a webhook subscription via BSP adapter."""
        from wa.adapters import get_bsp_adapter

        subscription = self.get_object()
        adapter = get_bsp_adapter(subscription.wa_app)

        if not adapter.supports("subscriptions"):
            return Response(
                {
                    "error": (f"{adapter.PROVIDER_NAME} does not support webhook subscriptions via API."),
                },
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        if subscription.status == SubscriptionStatus.ACTIVE:
            return Response({"error": "Subscription is already active"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = adapter.register_webhook(subscription)
            subscription.refresh_from_db()
        except NotImplementedError as exc:
            # BSP adapter not yet registered — mark active locally.
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.is_active = True
            subscription.error_message = str(exc)
            subscription.save(update_fields=["status", "is_active", "error_message"])
            return Response(
                {
                    "message": "Subscription activated locally (BSP adapter not available)",
                    "status": subscription.status,
                    "warning": str(exc),
                }
            )
        except Exception as exc:
            subscription.status = SubscriptionStatus.FAILED
            subscription.error_message = f"BSP adapter error: {exc}"
            subscription.save(update_fields=["status", "error_message"])
            return Response(
                {"error": f"Failed to register webhook: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if result.success:
            subscription.is_active = True
            subscription.save(update_fields=["is_active"])
            return Response(
                {
                    "message": "Subscription activated",
                    "status": subscription.status,
                    "bsp_subscription_id": subscription.bsp_subscription_id,
                }
            )

        return Response(
            {
                "error": result.error_message or "BSP rejected the webhook registration",
                "status": subscription.status,
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    @swagger_auto_schema(
        operation_description="Deactivate a webhook subscription",
        operation_summary="Deactivate Subscription",
        operation_id="deactivate_wa_subscription_v2",
        tags=["WhatsApp Subscriptions (v2)"],
        responses={
            200: openapi.Response(
                description="Subscription deactivated",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "message": openapi.Schema(type=openapi.TYPE_STRING),
                        "status": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Subscription not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """Deactivate a webhook subscription via BSP adapter."""
        from wa.adapters import get_bsp_adapter

        subscription = self.get_object()
        adapter = get_bsp_adapter(subscription.wa_app)

        if not adapter.supports("subscriptions"):
            return Response(
                {
                    "error": (f"{adapter.PROVIDER_NAME} does not support webhook subscriptions via API."),
                },
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        try:
            adapter.unregister_webhook(subscription)
            subscription.refresh_from_db()
        except NotImplementedError:
            # No adapter — just deactivate locally.
            pass
        except Exception as exc:
            # Non-fatal — still deactivate locally even if BSP call fails.
            subscription.error_message = f"BSP unregister warning: {exc}"

        subscription.status = SubscriptionStatus.INACTIVE
        subscription.is_active = False
        subscription.save(update_fields=["status", "is_active", "error_message"])

        return Response(
            {
                "message": "Subscription deactivated",
                "status": subscription.status,
            }
        )

    @swagger_auto_schema(
        operation_description="Test a webhook endpoint by sending a test payload",
        operation_summary="Test Webhook",
        operation_id="test_wa_subscription_v2",
        tags=["WhatsApp Subscriptions (v2)"],
        responses={
            200: openapi.Response(
                description="Test result",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "success": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                        "response_code": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "response_time_ms": openapi.Schema(type=openapi.TYPE_NUMBER),
                        "message": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Subscription not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="test")
    def test(self, request, pk=None):
        """Test a webhook endpoint."""
        import time

        import requests

        subscription = self.get_object()

        test_payload = {
            "type": "test",
            "timestamp": int(time.time()),
            "message": "This is a test webhook from Jina Connect",
            "subscription_id": str(subscription.id),
        }

        try:
            start_time = time.time()
            response = requests.post(
                subscription.webhook_url, json=test_payload, timeout=10, headers={"Content-Type": "application/json"}
            )
            response_time = (time.time() - start_time) * 1000

            return Response(
                {
                    "success": response.status_code < 400,
                    "response_code": response.status_code,
                    "response_time_ms": round(response_time, 2),
                    "message": "Webhook test completed successfully"
                    if response.status_code < 400
                    else f"Webhook returned error: {response.status_code}",
                }
            )
        except requests.Timeout:
            return Response(
                {
                    "success": False,
                    "response_code": None,
                    "response_time_ms": 10000,
                    "message": "Webhook request timed out after 10 seconds",
                }
            )
        except requests.RequestException as e:
            return Response(
                {
                    "success": False,
                    "response_code": None,
                    "response_time_ms": None,
                    "message": f"Webhook request failed: {str(e)}",
                }
            )

    @swagger_auto_schema(
        operation_description="Get available event types for subscription",
        operation_summary="Get Event Types",
        operation_id="get_event_types_wa_subscription_v2",
        tags=["WhatsApp Subscriptions (v2)"],
        responses={
            200: openapi.Response(
                description="Available event types",
                schema=openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "value": openapi.Schema(type=openapi.TYPE_STRING),
                            "label": openapi.Schema(type=openapi.TYPE_STRING),
                            "description": openapi.Schema(type=openapi.TYPE_STRING),
                        },
                    ),
                ),
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="event-types")
    def event_types(self, request):
        """Get available event types for subscription."""
        from wa.models import WebhookEventType

        event_types = [
            {"value": choice[0], "label": choice[1], "description": self._get_event_type_description(choice[0])}
            for choice in WebhookEventType.choices
        ]

        return Response(event_types)

    @swagger_auto_schema(
        operation_description=(
            "Refresh subscriptions for a WA App. "
            "Purges ALL existing webhook subscriptions on the BSP side "
            "(Gupshup allows max 5), deletes stale local records, and "
            "re-creates a single subscription covering all event types. "
            "Use this when webhooks stop working or you need a clean slate."
        ),
        operation_summary="Refresh Subscriptions",
        operation_id="refresh_wa_subscriptions_v2",
        tags=["WhatsApp Subscriptions (v2)"],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["wa_app"],
            properties={
                "wa_app": openapi.Schema(
                    type=openapi.TYPE_INTEGER, description="WA App PK to refresh subscriptions for"
                ),
                "webhook_url": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Override webhook URL (optional — defaults to settings.DEFAULT_WEBHOOK_BASE_URL + BSP path)",
                ),
            },
        ),
        responses={
            200: openapi.Response(
                description="Subscriptions refreshed",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "message": openapi.Schema(type=openapi.TYPE_STRING),
                        "purged": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "created": openapi.Schema(type=openapi.TYPE_OBJECT),
                    },
                ),
            ),
            400: openapi.Response(description="Missing wa_app or validation error"),
            404: openapi.Response(description="WA App not found"),
            502: openapi.Response(description="BSP operation failed"),
        },
    )
    @action(detail=False, methods=["post"], url_path="refresh")
    def refresh(self, request):
        """
        Purge all existing webhook subscriptions on the BSP and re-create
        a single clean subscription covering all event types.

        Gupshup enforces a max of 5 subscriptions per app. This action
        calls delete-all on the BSP side first, cleans up local records,
        then creates one new subscription and registers it.
        """
        from django.conf import settings as django_settings

        from wa.adapters import get_bsp_adapter
        from wa.models import SubscriptionStatus, WAApp, WASubscription, WebhookEventType

        wa_app_pk = request.data.get("wa_app")
        if not wa_app_pk:
            return Response(
                {"error": "wa_app is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            wa_app = WAApp.objects.get(pk=wa_app_pk)
        except WAApp.DoesNotExist:
            return Response(
                {"error": f"WAApp with pk={wa_app_pk} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        adapter = get_bsp_adapter(wa_app)

        # ── Step 1: Purge all existing subscriptions on BSP ──────────────
        purge_result = adapter.purge_all_webhooks()
        if not purge_result.success:
            return Response(
                {
                    "error": f"Failed to purge existing subscriptions: {purge_result.error_message}",
                    "provider": purge_result.provider,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        purged_count = purge_result.data.get("deleted_count", 0)

        # ── Step 2: Delete stale local records ───────────────────────────
        WASubscription.objects.filter(wa_app=wa_app).delete()

        # ── Step 3: Determine webhook URL ────────────────────────────────
        webhook_url = request.data.get("webhook_url")
        if not webhook_url:
            bsp_path_map = {
                "GUPSHUP": "/wa/v2/webhooks/gupshup/",
                "META": "/wa/v2/webhooks/meta/",
            }
            base = getattr(django_settings, "DEFAULT_WEBHOOK_BASE_URL", "").rstrip("/")
            path = bsp_path_map.get(wa_app.bsp, "/wa/v2/webhooks/gupshup/")
            webhook_url = f"{base}{path}"

        # ── Step 4: Create a single subscription covering all events ─────
        all_event_types = [et.value for et in WebhookEventType]
        subscription = WASubscription.objects.create(
            wa_app=wa_app,
            webhook_url=webhook_url,
            event_types=all_event_types,
            status=SubscriptionStatus.PENDING,
        )

        # ── Step 5: Register with BSP ────────────────────────────────────
        try:
            adapter.register_webhook(subscription)
            subscription.refresh_from_db()
        except NotImplementedError:
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.save(update_fields=["status"])
        except Exception as exc:
            subscription.error_message = f"BSP registration failed: {exc}"
            subscription.status = SubscriptionStatus.FAILED
            subscription.save(update_fields=["error_message", "status"])
            return Response(
                {
                    "error": f"Purge succeeded but re-registration failed: {exc}",
                    "purged": purged_count,
                    "subscription_id": str(subscription.pk),
                    "status": subscription.status,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        serializer = self.get_serializer(subscription)
        return Response(
            {
                "message": "Subscriptions refreshed successfully",
                "purged": purged_count,
                "created": serializer.data,
            }
        )

    def _get_event_type_description(self, event_type):
        """Get description for event type."""
        descriptions = {
            "MESSAGE": "Inbound message events (text, media, location, etc.)",
            "STATUS": "Message delivery status updates (sent, delivered, read, failed)",
            "TEMPLATE": "Template approval status changes",
            "BILLING": "Billing and pricing events",
            "ACCOUNT": "Account status and policy events",
        }
        return descriptions.get(event_type, "")
