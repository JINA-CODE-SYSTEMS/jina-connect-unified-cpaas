from typing import List, Optional

from django.db.models import Sum
from djmoney.money import Money

from abstract.managers import BaseTenantModelForFilterUserManager


class TenantManager(BaseTenantModelForFilterUserManager):
    def aggregate_total_balance(self, tenant_ids: Optional[List[int]] = None) -> Money:
        """
        Calculate the aggregate total balance across multiple tenants.
        Assumes all balances are in the same currency.

        Args:
            tenant_ids: Optional list of tenant IDs to filter by. If None, calculates for all tenants.

        Returns:
            Money: The sum of all tenant total balances (balance + credit_line).
        """
        queryset = self.get_queryset()
        if tenant_ids:
            queryset = queryset.filter(id__in=tenant_ids)

        # Calculate total of (balance + credit_line) for all tenants
        balance_sum = queryset.aggregate(total_balance=Sum("balance"), total_credit=Sum("credit_line"))

        total_balance_amount = balance_sum["total_balance"] or 0
        total_credit_amount = balance_sum["total_credit"] or 0

        # Get currency from the first tenant (assuming all are same currency)
        first_tenant = queryset.first()
        currency = first_tenant.balance.currency if first_tenant else "USD"

        return Money(total_balance_amount + total_credit_amount, currency)

    def get_outstanding_balance_total(self, tenant_ids: Optional[List[int]] = None) -> Money:
        """
        Calculate the total outstanding balance for tenants with negative balances.
        Only considers the actual balance field, not total_balance (balance + credit_line).

        Args:
            tenant_ids: Optional list of tenant IDs to filter by. If None, calculates for all tenants.

        Returns:
            Money: The sum of all negative balances (absolute value of debt owed).
        """
        queryset = self.get_queryset()
        if tenant_ids:
            queryset = queryset.filter(id__in=tenant_ids)

        # Filter tenants with negative balance (those who owe money)
        overdrawn_tenants = queryset.filter(balance__lt=0)

        # Sum the negative balances (will be negative, so we'll make it positive)
        outstanding_sum = overdrawn_tenants.aggregate(total_outstanding=Sum("balance"))["total_outstanding"] or 0

        # Get currency from the first overdrawn tenant
        first_overdrawn = overdrawn_tenants.first()
        currency = first_overdrawn.balance.currency if first_overdrawn else "USD"

        # Return absolute value since outstanding debt should be positive
        return Money(abs(outstanding_sum), currency)
