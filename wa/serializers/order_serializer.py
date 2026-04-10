"""
Order REST Serializers — BE-15

Serializers for WAOrder list/detail/action endpoints.
"""
from rest_framework import serializers

from wa.models import OrderStatus, PaymentStatus, WAOrder, WAPaymentEvent


class WAPaymentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = WAPaymentEvent
        fields = [
            "id", "status", "transaction_id", "pg_transaction_id",
            "transaction_status", "amount_value", "currency", "created_at",
        ]


class WAOrderListSerializer(serializers.ModelSerializer):
    contact_name = serializers.CharField(
        source="contact.name", read_only=True, default="",
    )
    contact_phone = serializers.CharField(
        source="contact.wa_number", read_only=True, default="",
    )

    class Meta:
        model = WAOrder
        fields = [
            "id", "reference_id", "order_type", "currency", "total_amount",
            "order_status", "payment_status", "payment_gateway",
            "contact_name", "contact_phone",
            "created_at", "payment_captured_at",
        ]


class WAOrderDetailSerializer(WAOrderListSerializer):
    payment_events = WAPaymentEventSerializer(
        many=True, read_only=True,
    )

    class Meta(WAOrderListSerializer.Meta):
        fields = WAOrderListSerializer.Meta.fields + [
            "items", "subtotal", "tax", "shipping", "discount",
            "configuration_name", "transaction_id", "pg_transaction_id",
            "payment_method", "order_details_payload", "payment_events",
        ]


class OrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    body_text = serializers.CharField(max_length=1024)
    description = serializers.CharField(
        max_length=120, required=False, default="",
    )


class RefundSerializer(serializers.Serializer):
    speed = serializers.ChoiceField(
        choices=["instant", "normal"], default="normal",
    )
