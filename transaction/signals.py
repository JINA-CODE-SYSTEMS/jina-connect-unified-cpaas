import logging

from abstract.models import TransactionTypeChoices
from django.db.models.signals import post_save
from django.dispatch import receiver
from razorpay.models import RazorPayOrder, RazorPayStatusChoices
from transaction.models import TenantTransaction

logger = logging.getLogger(__name__)



@receiver(post_save, sender=RazorPayOrder)
def handle_razorpay_order_changes(sender, instance, created, **kwargs):
    """
    Handle RazorPay order creation and updates to create/update corresponding transactions
    """
    if created:
        # CREATION LOGIC - Create initial transaction entry
        logger.info(f"New RazorPay order created: {instance.order_id} for tenant {instance.tenant.name}")
        
        # Create a transaction entry for the new order
        try:
            transaction = TenantTransaction.objects.create(
                tenant=instance.tenant,
                amount=instance.amount,
                transaction_type=TransactionTypeChoices.PENDING_RECHARGE,
                transaction_id=instance.order_id,
                razor_pay_order=instance,
                created_by=instance.created_by,
                updated_by=instance.updated_by
            )
            
            logger.info(f"Created transaction {transaction.system_transaction_id} for RazorPay order {instance.order_id}")
            
        except Exception as e:
            logger.error(f"Failed to create transaction for RazorPay order {instance.order_id}: {str(e)}")
    
    else:
        # UPDATE LOGIC - Handle status changes
        logger.info(f"RazorPay order updated: {instance.order_id} - Status: {instance.status}")
        
        try:
            # Get the existing transaction
            existing_transaction = TenantTransaction.objects.get(
                razor_pay_order=instance
            )
            
            if existing_transaction:
                # Update transaction based on RazorPay order status
                update_transaction_based_on_razorpay_status(existing_transaction, instance)
            else:
                # If no transaction exists, create one (fallback)
                logger.warning(f"No existing transaction found for RazorPay order {instance.order_id}, creating new one")
                
                transaction = TenantTransaction.objects.create(
                    tenant=instance.tenant,
                    amount=instance.amount,
                    transaction_type=TransactionTypeChoices.PENDING_RECHARGE,
                    transaction_id=instance.order_id,
                    razor_pay_order=instance,
                    created_by=instance.created_by,
                    updated_by=instance.updated_by
                )
                
                update_transaction_based_on_razorpay_status(transaction, instance)
                
        except Exception as e:
            logger.error(f"Failed to update transaction for RazorPay order {instance.order_id}: {str(e)}")


def update_transaction_based_on_razorpay_status(transaction, razorpay_order):
    """
    Update transaction properties based on RazorPay order status
    """
    old_status = transaction.transaction_type
    new_status = razorpay_order.status
    
    if old_status in [TransactionTypeChoices.SUCCESS_RECHARGE]:
        logger.info(f"Transaction {transaction.system_transaction_id} already marked as successful recharge; no update needed")
        return
    


    
    # Update transaction based on RazorPay status
    if new_status == RazorPayStatusChoices.SUCCESS:
        # Order is successful - this is a confirmed recharge
        transaction.transaction_type = TransactionTypeChoices.SUCCESS_RECHARGE
        logger.info(f"Transaction {transaction.system_transaction_id} marked as successful recharge")
        
        
        
    elif new_status == RazorPayStatusChoices.FAILED:
        # Order failed - mark transaction as inactive but keep for audit
        transaction.transaction_type = TransactionTypeChoices.FAILED_RECHARGE
        
        logger.info(f"Transaction {transaction.system_transaction_id} marked as failed")
        
    elif new_status == RazorPayStatusChoices.PENDING:
        # Order is still pending - keep transaction active but don't add to balance yet
        transaction.transaction_type = TransactionTypeChoices.PENDING_RECHARGE
        
        logger.info(f"Transaction {transaction.system_transaction_id} is pending")
    
    # Update the transaction
    transaction.updated_by = razorpay_order.updated_by
    transaction.save(update_fields=['transaction_type', 'is_active', 'updated_by', 'updated_at'])





# Custom exception for transaction signal errors
class TransactionSignalError(Exception):
    """Custom exception for transaction signal processing errors"""
    pass