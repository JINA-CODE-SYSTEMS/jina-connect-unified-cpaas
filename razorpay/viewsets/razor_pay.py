import hashlib
import hmac

from django.conf import settings
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from abstract.viewsets.base import BaseModelViewSet
from razorpay.models import RazorPayOrder, RazorPayStatusChoices
from razorpay.serializers import PaymentStatusSerializer, PaymentVerificationSerializer, RazorPayOrderSerializer
from tenants.permission_classes import TenantRolePermission


class RazorPayViewSet(BaseModelViewSet):
    queryset = RazorPayOrder.objects.all()
    serializer_class = RazorPayOrderSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]
    required_permissions = {
        "list": "billing.view",
        "retrieve": "billing.view",
        "create": "billing.manage",
        "verify_payment": "billing.manage",
        "payment_status": "billing.view",
        "default": "billing.view",
    }

    def get_queryset(self):
        """#255: Scope to the requesting user's tenant to prevent cross-tenant data leakage."""
        user = self.request.user
        if user.is_superuser:
            return self.queryset.all()
        return self.queryset.filter(tenant__tenant_users__user=user)

    @swagger_auto_schema(
        method="post",
        request_body=PaymentVerificationSerializer,
        responses={
            200: openapi.Response(
                description="Payment verified successfully",
                examples={
                    "application/json": {
                        "status": "success",
                        "message": "Payment verified successfully",
                        "order_id": "order_xxxxx",
                        "payment_id": "pay_xxxxx",
                    }
                },
            ),
            400: openapi.Response(
                description="Invalid signature or missing parameters",
                examples={
                    "application/json": {
                        "status": "failed",
                        "message": "Payment verification failed. Invalid signature.",
                    }
                },
            ),
            404: openapi.Response(description="Order not found"),
        },
    )
    @action(detail=False, methods=["post"], url_path="verify-payment")
    def verify_payment(self, request):
        """
        Verify Razorpay payment after successful checkout.

        After the user completes payment on Razorpay checkout, the frontend
        receives razorpay_order_id, razorpay_payment_id, and razorpay_signature.
        This endpoint verifies the signature to confirm the payment is authentic.

        Flow:
        1. Frontend creates order via POST /api/razorpay/razor-pay/
        2. Frontend opens Razorpay checkout with order_id
        3. On success, Razorpay returns payment_id + signature
        4. Frontend calls this endpoint to verify
        5. If verified, order status is updated to SUCCESS
        """
        serializer = PaymentVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        razorpay_order_id = serializer.validated_data["razorpay_order_id"]
        razorpay_payment_id = serializer.validated_data["razorpay_payment_id"]
        razorpay_signature = serializer.validated_data["razorpay_signature"]

        # Find the order
        try:
            order = RazorPayOrder.objects.get(
                order_id=razorpay_order_id,
                tenant__tenant_users__user=request.user,
            )
        except RazorPayOrder.DoesNotExist:
            return Response(
                {"status": "failed", "message": "Order not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Verify signature
        # Signature = HMAC-SHA256(order_id + "|" + payment_id, secret)
        message = f"{razorpay_order_id}|{razorpay_payment_id}"
        expected_signature = hmac.new(
            settings.RAZORPAY_KEY_SECRET.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(razorpay_signature, expected_signature):
            # Mark as failed
            order.status = RazorPayStatusChoices.FAILED
            order.save(update_fields=["status"])
            return Response(
                {
                    "status": "failed",
                    "message": "Payment verification failed. Invalid signature.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Signature valid - update order status
        order.status = RazorPayStatusChoices.SUCCESS
        order.razor_pay_response = {
            **(order.razor_pay_response or {}),
            "payment_id": razorpay_payment_id,
            "verified_at": str(request._request.META.get("REQUEST_TIME", "")),
        }
        order.save(update_fields=["status", "razor_pay_response"])

        return Response(
            {
                "status": "success",
                "message": "Payment verified successfully",
                "order_id": razorpay_order_id,
                "payment_id": razorpay_payment_id,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        method="get",
        manual_parameters=[
            openapi.Parameter(
                "order_id",
                openapi.IN_PATH,
                description="Razorpay order ID (e.g., order_xxxxx)",
                type=openapi.TYPE_STRING,
                required=True,
            ),
        ],
        responses={
            200: PaymentStatusSerializer,
            404: openapi.Response(description="Order not found"),
        },
    )
    @action(detail=False, methods=["get"], url_path="payment-status/(?P<order_id>[^/.]+)")
    def payment_status(self, request, order_id=None):
        """
        Get payment status for a Razorpay order.

        Use this endpoint to check if a payment has been completed,
        is still pending, or has failed.

        Status values:
        - PENDING: Payment not yet completed
        - SUCCESS: Payment verified and successful
        - FAILED: Payment failed or verification failed
        """
        try:
            order = RazorPayOrder.objects.get(
                order_id=order_id,
                tenant__tenant_users__user=request.user,
            )
        except RazorPayOrder.DoesNotExist:
            return Response(
                {"error": "Order not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PaymentStatusSerializer(order)
        return Response(serializer.data, status=status.HTTP_200_OK)
