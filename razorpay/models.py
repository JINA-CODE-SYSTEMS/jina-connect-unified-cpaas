from django.db import models
from djmoney.models.fields import MoneyField

from abstract.models import BaseModel, BaseModelWithOwner
from tenants.models import Tenant


class RazorPayStatusChoices(models.TextChoices):
    PENDING = "PENDING", "PENDING"
    SUCCESS = "SUCCESS", "SUCCESS"
    FAILED = "FAILED", "FAILED"


class RazorPayOrder(BaseModelWithOwner):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="razorpay_orders",
    )
    amount = MoneyField(max_digits=14, decimal_places=2, default_currency="INR")
    order_id = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=20, choices=RazorPayStatusChoices.choices, default=RazorPayStatusChoices.PENDING
    )
    razor_pay_response = models.JSONField(null=True, blank=True, editable=False)
    name = None

    def __str__(self):
        return f"{self.order_id} | {self.id}"


class RazorPayWebhook(BaseModel):
    order = models.ForeignKey(RazorPayOrder, on_delete=models.CASCADE, related_name="webhooks")
    response = models.JSONField()
    name = None

    def __str__(self):
        return f"Webhook for {self.order.order_id}"
