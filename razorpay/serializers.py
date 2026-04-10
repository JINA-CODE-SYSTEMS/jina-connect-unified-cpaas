from abstract.serializers import BaseSerializer
from django.conf import settings
from djmoney.contrib.django_rest_framework import MoneyField
from djmoney.money import Money
from razorpay.models import (RazorPayOrder, RazorPayStatusChoices,
                             RazorPayWebhook)
from razorpay.utility.razor_pay_order import create_razorpay_order
from rest_framework import serializers


class RazorPayOrderSerializer(BaseSerializer):
    amount = MoneyField(max_digits=14, decimal_places=2, default_currency='INR')

    class Meta:
        model = RazorPayOrder
        fields = "__all__"
        read_only_fields = ("order_id",)

    def create(self, validated_data):
        amount = validated_data["amount"]
        currency = validated_data.get("amount_currency", "INR")  # fallback if not provided

        # Ensure amount is a Money object
        if not isinstance(amount, Money):
            money_obj = Money(amount, currency)
        else:
            money_obj = amount

        # Extract numeric + currency
        numeric_amount = money_obj.amount  # Decimal
        currency_code = money_obj.currency.code

        # Create Razorpay order
        if settings.RAZORPAY_KEY_SECRET.strip() != "":
            razorpay_response = create_razorpay_order(numeric_amount, currency_code)
        else:
            # Mock response for DEBUG mode
            razorpay_response = {
                "id": "order_mocked12345",
                "amount": int(numeric_amount * 100),
                "currency": currency_code,
                "status": "created",
            }    


        # Save to DB
        validated_data["order_id"] = razorpay_response["id"]
        validated_data["amount"] = money_obj
        validated_data["razor_pay_response"] = razorpay_response

        return super().create(validated_data)


class PaymentVerificationSerializer(serializers.Serializer):
    """
    Serializer for verifying Razorpay payment from frontend.
    After successful checkout, frontend sends these values for verification.
    """
    razorpay_order_id = serializers.CharField(
        help_text="Razorpay order ID (e.g., order_xxxxx)"
    )
    razorpay_payment_id = serializers.CharField(
        help_text="Razorpay payment ID (e.g., pay_xxxxx)"
    )
    razorpay_signature = serializers.CharField(
        help_text="Razorpay signature for verification"
    )


class PaymentStatusSerializer(serializers.ModelSerializer):
    """Serializer for returning payment status."""
    amount = MoneyField(max_digits=14, decimal_places=2, default_currency='INR')
    
    class Meta:
        model = RazorPayOrder
        fields = ['id', 'order_id', 'amount', 'status', 'created_at', 'updated_at']
        read_only_fields = fields


class RazorPayWebhookSerializer(serializers.ModelSerializer):
    class Meta:
        model = RazorPayWebhook
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        webhook = super().create(validated_data)
        self._update_order_status(webhook)
        return webhook

    def _update_order_status(self, webhook: RazorPayWebhook):
        """Update order status based on webhook event."""
        event = webhook.response.get("event")

        if event == "payment.captured":
            webhook.order.status = RazorPayStatusChoices.SUCCESS
        elif event == "payment.failed":
            webhook.order.status = RazorPayStatusChoices.FAILED
        else:
            return

        webhook.order.save(update_fields=["status"])
