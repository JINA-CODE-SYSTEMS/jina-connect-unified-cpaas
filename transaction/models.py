from django.db import models

from abstract.models import BaseTransaction
from broadcast.models import Broadcast
from razorpay.models import RazorPayOrder
from tenants.models import Tenant


class TenantTransaction(BaseTransaction):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="tenant_transactions",
    )
    razor_pay_order = models.ForeignKey(
        RazorPayOrder,
        on_delete=models.CASCADE,
        related_name="tenant_transactions",
        null=True,
        blank=True,
    )
    broadcast = models.ForeignKey(
        Broadcast,
        on_delete=models.CASCADE,
        related_name="tenant_transactions",
        null=True,
        blank=True,
    )
    broadcast_history_id = models.IntegerField(
        null=True, blank=True, help_text="Reference to HistoricalBroadcast record ID at transaction time"
    )

    name = None

    def __str__(self):
        return f"{self.tenant.name} - {self.amount} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"
