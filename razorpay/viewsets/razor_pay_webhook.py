import base64
import hashlib
import hmac
import json

from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from razorpay.models import RazorPayOrder
from razorpay.serializers import RazorPayWebhookSerializer
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response


class RazorpayWebhookViewSet(viewsets.ViewSet):
    """Handles Razorpay webhooks."""

    authentication_classes = []
    permission_classes = []

    @swagger_auto_schema(
        method="post",
        request_body=RazorPayWebhookSerializer,
        responses={200: RazorPayWebhookSerializer},
    )
    @action(detail=False, methods=["post"], url_path="webhook")
    def razorpay_webhook(self, request):
        body = request.body.decode("utf-8")

        signature = request.headers.get("X-Razorpay-Signature", "")
        if not self._verify_signature(body, signature):
            print("Signature mismatch: ", signature)
            return Response({"error": "Invalid signature"}, status=400)
        try:
            payload = json.loads(body)
            order_id = (
                payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
                .get("order_id")
            )
        except json.JSONDecodeError:
            return Response({"error": "Invalid JSON payload"}, status=200)

        if not order_id:
            return Response({"status": "ignored", "event": payload.get("event")})

        try:
            order = RazorPayOrder.objects.get(order_id=order_id)
        except RazorPayOrder.DoesNotExist:
            return Response(
                {"error": f"No RazorPay object found for order_id {order_id}"},
                status=404,
            )

        serializer = RazorPayWebhookSerializer(
            data={"order": order.id, "response": payload}
        )
        serializer.is_valid(raise_exception=True)
        webhook = serializer.save()

        return Response(serializer.data)

    @staticmethod
    def _verify_signature(body: str, received_sig: str) -> bool:
        """Verify Razorpay webhook signature (hex HMAC SHA256)."""
        expected_sig = hmac.new(
            settings.RAZORPAY_WEBHOOK_SECRET.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(received_sig, expected_sig)
