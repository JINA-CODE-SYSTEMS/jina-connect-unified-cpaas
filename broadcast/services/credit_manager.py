"""
Credit management service for broadcasts.
Handles deduction and refund of credits for broadcast messages.
"""

import logging
from decimal import Decimal

from django.db import transaction as db_transaction
from djmoney.money import Money

from abstract.models import TransactionTypeChoices
from broadcast.models import Broadcast, BroadcastStatusChoices
from transaction.models import TenantTransaction

logger = logging.getLogger(__name__)


class InsufficientBalanceError(Exception):
    """Raised when tenant doesn't have enough balance for broadcast"""

    def __init__(self, message, required=None, available=None):
        super().__init__(message)
        self.required = required
        self.available = available


class BroadcastCreditManager:
    """Manages credit deduction and refunds for broadcasts"""

    @staticmethod
    def deduct_credits_for_broadcast(broadcast: Broadcast):
        """
        Deduct credits when broadcast is queued/scheduled.

        Args:
            broadcast: Broadcast instance

        Raises:
            InsufficientBalanceError: If tenant doesn't have enough balance

        Returns:
            TenantTransaction: The debit transaction created
        """
        # Check if deduction should be applied
        if not broadcast.should_apply_credit_deduction():
            logger.debug("[CREDIT] Broadcast %s doesn't require credit deduction", broadcast.id)
            return None

        # Check if already deducted
        if broadcast.credit_deducted:
            logger.debug("[CREDIT] Credits already deducted for broadcast %s", broadcast.id)
            return None

        # Calculate cost
        initial_cost = broadcast.calculate_initial_cost()

        if initial_cost <= 0.0:
            logger.debug("[CREDIT] No cost calculated for broadcast %s", broadcast.id)
            return None

        tenant = broadcast.tenant

        # Check balance using total_balance property (includes credit_limit)
        available_balance = tenant.total_balance
        required_amount = Money(initial_cost, tenant.balance.currency)

        if available_balance < required_amount:
            error_msg = (
                f"Insufficient balance. Required: {required_amount}, "
                f"Available: {available_balance} (Balance: {tenant.balance}, "
                f"Credit Limit: {tenant.credit_line})"
            )
            raise InsufficientBalanceError(error_msg, required=initial_cost, available=available_balance.amount)

        # Use atomic transaction to ensure consistency
        with db_transaction.atomic():
            # Deduct from tenant balance
            tenant.balance -= Money(initial_cost, tenant.balance.currency)
            tenant.save(update_fields=["balance"])

            # Get the latest historical record ID
            history_id = broadcast.history.latest().history_id if broadcast.history.exists() else None

            # Create debit transaction with historical reference
            transaction = TenantTransaction.objects.create(
                tenant=tenant,
                broadcast=broadcast,
                broadcast_history_id=history_id,
                transaction_type=TransactionTypeChoices.CONSUMPTION,
                amount=Money(initial_cost, tenant.balance.currency),
                description=f"Broadcast credit deduction for '{broadcast.name}' ({broadcast.recipients.count()} recipients)",
            )

            # Mark broadcast as credit deducted
            Broadcast.objects.filter(pk=broadcast.pk).update(
                credit_deducted=True, refund_processed=False, initial_cost=Decimal(str(initial_cost))
            )

            logger.info("[CREDIT] Deducted %s credits for broadcast %s", initial_cost, broadcast.id)
            logger.info("[CREDIT] Transaction ID: %s", transaction.id)
            logger.info(f"[CREDIT] New balance: {tenant.balance}, Total available: {tenant.total_balance}")

            return transaction

    @staticmethod
    def process_refund_for_broadcast(broadcast: Broadcast):
        """
        Process refund for failed messages after broadcast completion.

        Args:
            broadcast: Broadcast instance

        Returns:
            TenantTransaction: The refund transaction created, or None if no refund needed
        """
        # Check if refund should be processed
        if not broadcast.should_apply_credit_deduction():
            logger.debug("[REFUND] Broadcast %s doesn't require refund processing", broadcast.id)
            return None

        # Check if credits were deducted
        if not broadcast.credit_deducted:
            logger.debug("[REFUND] No credits were deducted for broadcast %s", broadcast.id)
            return None

        # Check if already processed
        if broadcast.refund_processed:
            logger.debug("[REFUND] Refund already processed for broadcast %s", broadcast.id)
            return None

        # Check if broadcast is complete or cancelled
        if broadcast.status == BroadcastStatusChoices.CANCELLED:
            # Full refund for cancelled broadcasts (no messages were sent)
            refund_amount = broadcast.initial_cost.amount
            refund_reason = "broadcast cancelled before sending"
            logger.info("[REFUND] Broadcast %s was cancelled. Processing full refund.", broadcast.id)
        elif broadcast.status in [
            BroadcastStatusChoices.SENT,
            BroadcastStatusChoices.PARTIALLY_SENT,
            BroadcastStatusChoices.FAILED,
        ]:
            # Partial refund based on failed messages
            refund_amount = broadcast.calculate_refund_amount()
            failed_count = broadcast.get_failed_message_count()
            refund_reason = f"{failed_count} message(s) failed to deliver"
            logger.info(
                "[REFUND] Broadcast %s completed. Processing refund for %s failed messages.", broadcast.id, failed_count
            )
        else:
            logger.debug(f"[REFUND] Broadcast {broadcast.id} is not complete yet (status: {broadcast.status})")
            return None

        # Validate refund doesn't exceed initial cost
        if refund_amount > broadcast.initial_cost.amount:
            logger.warning(
                f"[REFUND WARNING] Calculated refund ({refund_amount}) > initial cost ({broadcast.initial_cost})"
            )
            refund_amount = broadcast.initial_cost.amount

        if refund_amount <= 0.0:
            logger.debug("[REFUND] No refund needed for broadcast %s (no failed messages)", broadcast.id)
            # Mark as processed even if no refund
            Broadcast.objects.filter(pk=broadcast.pk).update(
                refund_processed=True, refund_amount=Money(0, broadcast.tenant.balance.currency)
            )
            return None

        tenant = broadcast.tenant

        # Use atomic transaction to ensure consistency
        with db_transaction.atomic():
            # Add refund to tenant balance
            tenant.balance += Money(refund_amount, tenant.balance.currency)
            tenant.save(update_fields=["balance"])

            # Get the latest historical record ID
            history_id = broadcast.history.latest().history_id if broadcast.history.exists() else None

            # Create refund transaction with historical reference
            transaction = TenantTransaction.objects.create(
                tenant=tenant,
                broadcast=broadcast,
                broadcast_history_id=history_id,
                transaction_type=TransactionTypeChoices.REFUND,
                amount=Money(refund_amount, tenant.balance.currency),
                description=f"Refund for {refund_reason} in broadcast '{broadcast.name}'",
            )

            # Mark broadcast as refund processed
            Broadcast.objects.filter(pk=broadcast.pk).update(
                refund_processed=True, refund_amount=Money(refund_amount, broadcast.tenant.balance.currency)
            )

            logger.info("[REFUND] Processed refund of %s credits for broadcast %s", refund_amount, broadcast.id)
            logger.info("[REFUND] Transaction ID: %s", transaction.id)
            logger.info(f"[REFUND] New balance: {tenant.balance}, Total available: {tenant.total_balance}")

            return transaction
