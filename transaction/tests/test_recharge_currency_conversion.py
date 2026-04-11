from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from djmoney.contrib.exchange.models import convert_money
from djmoney.money import Money

from abstract.models import TransactionTypeChoices
from razorpay.models import RazorPayOrder, RazorPayStatusChoices
from tenants.models import Tenant
from transaction.models import TenantTransaction

User = get_user_model()


class TenantRechargeWithCurrencyConversionTestCase(TestCase):
    """
    Test cases for tenant recharge scenarios with currency conversion.

    Covers scenarios where:
    - Tenant balance is in USD but recharge is in INR
    - Tenant balance is in INR but recharge is in USD
    - Both currencies are the same
    - Multiple recharges with different currencies
    """

    @classmethod
    def setUpTestData(cls):
        call_command("update_rates")  # populates test DB with live or backend rates

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", mobile="+919876543210", password="testpass123"
        )

        # Create tenant with USD balance
        self.tenant_usd = Tenant.objects.create(
            name="USD Tenant",
            balance=Money(100, "USD"),  # $100 initial balance
            credit_line=Money(0, "USD"),
            created_by=self.user,
            updated_by=self.user,
        )

        # Create tenant with INR balance
        self.tenant_inr = Tenant.objects.create(
            name="INR Tenant",
            balance=Money(5000, "INR"),  # ₹5000 initial balance
            credit_line=Money(0, "INR"),
            created_by=self.user,
            updated_by=self.user,
        )

    def test_usd_tenant_recharge_with_inr_amount(self):
        """
        Test: Tenant has USD balance, recharge is done in INR
        Expected: INR amount should be converted to USD and added to balance
        """
        # Update exchange rates for accurate testing
        call_command("update_rates")

        # Use real currency conversion: 830 INR to USD
        inr_amount = Money(830, "INR")
        expected_usd_equivalent = convert_money(inr_amount, "USD")

        initial_balance = self.tenant_usd.balance
        print(f"🔍 DEBUG: Initial balance: {initial_balance}")

        # Create a recharge order in INR
        order = RazorPayOrder.objects.create(
            tenant=self.tenant_usd,
            amount=inr_amount,
            order_id="order_inr_to_usd_123",
            status=RazorPayStatusChoices.PENDING,
            created_by=self.user,
            updated_by=self.user,
        )
        print(f"🔍 DEBUG: Created order with amount: {inr_amount}")

        # Verify transaction was created as PENDING initially
        transaction = TenantTransaction.objects.get(razor_pay_order=order)
        self.assertEqual(transaction.transaction_type, TransactionTypeChoices.PENDING_RECHARGE)
        print(f"🔍 DEBUG: Transaction created as: {transaction.transaction_type}")

        # Check balance after creating order (should be unchanged)
        self.tenant_usd.refresh_from_db()
        print(f"🔍 DEBUG: Balance after order creation: {self.tenant_usd.balance}")
        self.assertEqual(self.tenant_usd.balance, initial_balance)

        # Update order to SUCCESS
        print("🔍 DEBUG: Updating order status to SUCCESS...")
        order.status = RazorPayStatusChoices.SUCCESS
        order.save()
        print(f"🔍 DEBUG: Order status updated to: {order.status}")

        # Refresh tenant and check balance
        self.tenant_usd.refresh_from_db()
        print(f"🔍 DEBUG: Balance after SUCCESS: {self.tenant_usd.balance}")
        print(f"🔍 DEBUG: Expected conversion: {expected_usd_equivalent}")
        print(
            f"🔍 DEBUG: Expected final balance: {Money((initial_balance.amount + expected_usd_equivalent.amount).quantize(Decimal('0.01')), 'USD')}"
        )

        # Check transaction
        transaction.refresh_from_db()
        self.assertEqual(transaction.transaction_type, TransactionTypeChoices.SUCCESS_RECHARGE)

        # Check balance update
        expected_balance = Money(
            (initial_balance.amount + expected_usd_equivalent.amount).quantize(Decimal("0.01")), "USD"
        )
        self.assertEqual(self.tenant_usd.balance, expected_balance)

    def test_inr_tenant_recharge_with_usd_amount(self):
        """
        Test: Tenant has INR balance, recharge is done in USD
        Expected: USD amount should be converted to INR and added to balance
        """
        # Use real currency conversion: 50 USD to INR
        usd_amount = Money(50, "USD")
        expected_inr_equivalent = convert_money(usd_amount, "INR")

        initial_balance = self.tenant_inr.balance

        # Create a recharge order in USD
        order = RazorPayOrder.objects.create(
            tenant=self.tenant_inr,
            amount=usd_amount,
            order_id="order_usd_to_inr_456",
            status=RazorPayStatusChoices.PENDING,
            created_by=self.user,
            updated_by=self.user,
        )

        transaction = TenantTransaction.objects.get(razor_pay_order=order)
        self.assertEqual(transaction.transaction_type, TransactionTypeChoices.PENDING_RECHARGE)

        # Update order to SUCCESS
        order.status = RazorPayStatusChoices.SUCCESS
        order.save()

        # Refresh tenant
        self.tenant_inr.refresh_from_db()

        # Check transaction
        transaction = TenantTransaction.objects.get(razor_pay_order=order)
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.amount, usd_amount)  # Original USD amount
        self.assertEqual(transaction.transaction_type, TransactionTypeChoices.SUCCESS_RECHARGE)
        self.assertTrue(transaction.is_active)

        # Check balance update
        expected_balance = initial_balance + expected_inr_equivalent
        self.assertEqual(self.tenant_inr.balance, Money((expected_balance.amount).quantize(Decimal("0.01")), "INR"))

        print("✅ Test 2 Passed:")
        print(f"   Initial Balance: {initial_balance}")
        print(f"   Recharge Amount: {usd_amount}")
        print(f"   Converted Amount: {expected_inr_equivalent}")
        print(f"   Final Balance: {self.tenant_inr.balance}")

    def test_same_currency_recharge_no_conversion(self):
        """
        Test: Tenant has USD balance, recharge is done in USD
        Expected: No conversion needed, direct addition
        """
        initial_balance = self.tenant_usd.balance
        usd_recharge_amount = Money(25, "USD")

        # Create a recharge order in same currency
        order = RazorPayOrder.objects.create(
            tenant=self.tenant_usd,
            amount=usd_recharge_amount,
            order_id="order_same_currency_789",
            status=RazorPayStatusChoices.PENDING,
            created_by=self.user,
            updated_by=self.user,
        )

        # Verify transaction was created as PENDING initially
        transaction = TenantTransaction.objects.filter(razor_pay_order=order).first()
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.transaction_type, TransactionTypeChoices.PENDING_RECHARGE)

        # Verify balance hasn't changed yet (pending transaction)
        self.tenant_usd.refresh_from_db()
        self.assertEqual(self.tenant_usd.balance, initial_balance)

        # Update to SUCCESS to trigger balance update
        order.status = RazorPayStatusChoices.SUCCESS
        order.save()

        # Refresh tenant and transaction
        self.tenant_usd.refresh_from_db()
        transaction.refresh_from_db()

        # Verify transaction is now SUCCESS_RECHARGE
        self.assertEqual(transaction.transaction_type, TransactionTypeChoices.SUCCESS_RECHARGE)

        # Check direct addition (no conversion)
        expected_balance = initial_balance + usd_recharge_amount
        self.assertEqual(self.tenant_usd.balance, expected_balance)

        print("✅ Test 3 Passed:")
        print(f"   Initial Balance: {initial_balance}")
        print(f"   Recharge Amount: {usd_recharge_amount}")
        print(f"   Final Balance: {self.tenant_usd.balance} (no conversion needed)")

    def test_failed_recharge_no_balance_update(self):
        """
        Test: Failed recharge should not update balance regardless of currency
        Expected: Balance remains unchanged, transaction marked inactive
        """
        initial_balance = self.tenant_usd.balance

        # Create a recharge that will fail
        order = RazorPayOrder.objects.create(
            tenant=self.tenant_usd,
            amount=Money(1000, "INR"),
            order_id="order_failed_test",
            status=RazorPayStatusChoices.PENDING,
            created_by=self.user,
            updated_by=self.user,
        )

        # Verify transaction was created as PENDING initially
        transaction = TenantTransaction.objects.get(razor_pay_order=order)
        self.assertEqual(transaction.transaction_type, TransactionTypeChoices.PENDING_RECHARGE)

        # Update to FAILED
        order.status = RazorPayStatusChoices.FAILED
        order.save()

        # Refresh tenant
        self.tenant_usd.refresh_from_db()

        # Check that balance remained unchanged
        self.assertEqual(self.tenant_usd.balance, initial_balance)

        # Check that transaction exists but is marked as failed
        transaction = TenantTransaction.objects.filter(razor_pay_order=order).first()
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.transaction_type, TransactionTypeChoices.FAILED_RECHARGE)

        print("✅ Test 4 Passed - Failed Recharge:")
        print(f"   Balance unchanged: {self.tenant_usd.balance}")
        print(f"   Transaction failed: {transaction.transaction_type}")

    def test_zero_balance_tenant_recharge(self):
        """
        Test: Tenant with zero balance gets recharged
        Expected: Balance should be updated to recharge amount (converted if needed)
        """
        # Create tenant with zero balance
        zero_balance_tenant = Tenant.objects.create(
            name="Zero Balance Tenant",
            balance=Money(0, "USD"),
            credit_line=Money(0, "USD"),
            created_by=self.user,
            updated_by=self.user,
        )

        # Use real currency conversion: 4150 INR to USD
        inr_amount = Money(4150, "INR")
        usd_equivalent = convert_money(inr_amount, "USD")

        # Create recharge order
        order = RazorPayOrder.objects.create(
            tenant=zero_balance_tenant,
            amount=inr_amount,
            order_id="order_zero_balance",
            status=RazorPayStatusChoices.PENDING,
            created_by=self.user,
            updated_by=self.user,
        )

        # Update to SUCCESS
        order.status = RazorPayStatusChoices.SUCCESS
        order.save()

        # Refresh tenant
        zero_balance_tenant.refresh_from_db()

        # Check that balance is now the converted amount
        expected_balance = Money(usd_equivalent.amount.quantize(Decimal("0.01")), "USD")
        self.assertEqual(zero_balance_tenant.balance, expected_balance)

        print("✅ Test 5 Passed - Zero Balance Recharge:")
        print("   Initial Balance: $0")
        print(f"   Recharge: {inr_amount}")
        print(f"   Converted Amount: {usd_equivalent}")
        print(f"   Final Balance: {zero_balance_tenant.balance}")

    def test_negative_balance_tenant_recharge(self):
        """
        Test: Tenant with negative balance gets recharged
        Expected: Recharge should increase balance (handle debt scenarios)
        """
        # Create tenant with negative balance (overdrawn)
        negative_balance_tenant = Tenant.objects.create(
            name="Overdrawn Tenant",
            balance=Money(-25, "USD"),  # $25 in debt
            credit_line=Money(100, "USD"),  # Has credit line
            created_by=self.user,
            updated_by=self.user,
        )

        # Use real currency conversion: 2075 INR to USD
        inr_amount = Money(2075, "INR")
        usd_equivalent = convert_money(inr_amount, "USD")

        initial_balance = negative_balance_tenant.balance

        # Create recharge order
        order = RazorPayOrder.objects.create(
            tenant=negative_balance_tenant,
            amount=inr_amount,
            order_id="order_negative_balance",
            status=RazorPayStatusChoices.PENDING,
            created_by=self.user,
            updated_by=self.user,
        )

        # Update to SUCCESS
        order.status = RazorPayStatusChoices.SUCCESS
        order.save()

        # Refresh tenant
        negative_balance_tenant.refresh_from_db()

        # Check that debt was reduced/cleared
        expected_balance = Money((initial_balance.amount + usd_equivalent.amount).quantize(Decimal("0.01")), "USD")
        self.assertEqual(negative_balance_tenant.balance, expected_balance)

        print("✅ Test 6 Passed - Negative Balance Recharge:")
        print(f"   Initial Balance: {initial_balance}")
        print(f"   Recharge: {inr_amount}")
        print(f"   Converted Amount: {usd_equivalent}")
        print(f"   Final Balance: {negative_balance_tenant.balance}")


def run_all_tests():
    """Helper function to run all tests with output"""
    import unittest

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test cases
    suite.addTests(loader.loadTestsFromTestCase(TenantRechargeWithCurrencyConversionTestCase))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print(f"\n{'=' * 60}")
    print("TEST SUMMARY")
    print(f"{'=' * 60}")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(
        f"Success rate: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%"
    )

    return result


if __name__ == "__main__":
    run_all_tests()
