from django.conf import settings
from django.db import models

from abstract.models import BaseTransaction, TransactionTypeChoices
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

    # ── Offline settlement (#233) ────────────────────────────────────────
    # Set only on MANUAL CREDIT / MANUAL DEBIT rows, where an operator moved
    # a balance against a real-world payment. A credit like that has to be
    # defensible months later, so who did it and what it was for are part of
    # the record rather than something to reconstruct from an audit log.
    reference = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Invoice number or bank reference the credit was applied against.",
    )
    note = models.TextField(
        blank=True,
        default="",
        help_text="What happened, in the operator's words.",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="applied_transactions",
        help_text="The operator who applied this. Null for machine-generated rows.",
    )

    name = None

    class Meta:
        constraints = [
            # One credit per invoice. Re-submitting the same reference is the
            # obvious way to double-credit an account by accident, and it is
            # far easier to prevent than to unpick afterwards. Scoped to the
            # manual types so gateway rows, which have no reference, are
            # unaffected.
            models.UniqueConstraint(
                fields=["tenant", "reference"],
                condition=models.Q(
                    transaction_type__in=[
                        TransactionTypeChoices.MANUAL_CREDIT,
                        TransactionTypeChoices.MANUAL_DEBIT,
                    ]
                ),
                name="unique_manual_reference_per_tenant",
            ),
        ]

    def __str__(self):
        return f"{self.tenant.name} - {self.amount} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"
