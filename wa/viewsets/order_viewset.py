"""
WAOrder ViewSet — BE-16

REST endpoints for order management:
  GET  /api/wa/v2/orders/              → list orders (tenant-scoped)
  GET  /api/wa/v2/orders/{pk}/         → order detail with payment events
  POST /api/wa/v2/orders/{pk}/update-status/  → send order_status message
  POST /api/wa/v2/orders/{pk}/check-payment/  → lookup payment via META API
  POST /api/wa/v2/orders/{pk}/refund/          → initiate refund
"""

import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from wa.models import WAOrder
from wa.serializers.order_serializer import (
    OrderStatusUpdateSerializer,
    RefundSerializer,
    WAOrderDetailSerializer,
    WAOrderListSerializer,
)

logger = logging.getLogger(__name__)


class WAOrderViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing WhatsApp orders.

    Uses BaseTenantModelViewSet for automatic tenant scoping.
    """

    queryset = WAOrder.objects.all()
    serializer_class = WAOrderListSerializer
    http_method_names = ["get", "post"]
    search_fields = ["reference_id", "contact__name", "contact__wa_number"]
    ordering_fields = [
        "created_at", "payment_captured_at", "order_status", "payment_status",
    ]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return WAOrderDetailSerializer
        return WAOrderListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related("contact", "wa_app", "outgoing_message")

    # ── Custom actions ─────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="update-status")
    def update_status(self, request, pk=None):
        """Send an order_status message for this order."""
        from wa.services.order_service import OrderService

        order = self.get_object()
        serializer = OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            wa_message = OrderService.send_order_status(
                wa_order=order,
                new_status=serializer.validated_data["status"],
                body_text=serializer.validated_data["body_text"],
                description=serializer.validated_data.get("description", ""),
            )
            return Response(
                {"message_id": str(wa_message.pk), "status": "sent"},
                status=status.HTTP_200_OK,
            )
        except (ValueError, ValidationError) as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="check-payment")
    def check_payment(self, request, pk=None):
        """Lookup payment status via META Cloud API."""
        from wa.services.order_service import OrderService

        order = self.get_object()
        try:
            result = OrderService.lookup_payment(order)
            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error("check_payment failed for order %s: %s", pk, e)
            return Response(
                {"error": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """Initiate a refund for this order."""
        from wa.services.order_service import OrderService

        order = self.get_object()
        serializer = RefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = OrderService.initiate_refund(
                wa_order=order,
                speed=serializer.validated_data["speed"],
            )
            return Response(result, status=status.HTTP_200_OK)
        except (ValueError, ValidationError) as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error("refund failed for order %s: %s", pk, e)
            return Response(
                {"error": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
