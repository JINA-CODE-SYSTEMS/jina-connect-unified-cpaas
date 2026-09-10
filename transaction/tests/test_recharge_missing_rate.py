"""
A recharge that cannot be applied must fail atomically (#227).

Run with: python manage.py test transaction.tests.test_recharge_missing_rate

The existing conversion tests seed exchange rates. These deliberately do not,
which is the state production was actually in: `OPEN_EXCHANGE_RATES_APP_ID`
unset and `manage.py update_rates` never run, so the Rate table is empty.

Combined with `Transaction.amount` defaulting to INR while wallets default to
USD, the default path needed a conversion that could not happen. The old
behaviour raised `MissingRate` out of a `post_save` receiver: inside an atomic
block the recharge rolled back, but in autocommit the transaction row was
committed and the balance simply never moved — a payment recorded with no
credit, and nothing to show it had failed.
"""

from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from djmoney.contrib.exchange.models import Rate
from djmoney.money import Money

from abstract.exceptions import MissingExchangeRate
from abstract.models import TransactionTypeChoices
from tenants.models import Tenant
from transaction.models import TenantTransaction


class RechargeWithoutRatesTestCase(TestCase):
    """No rates loaded — the production state on 2026-09-10."""

    def setUp(self):
        # Deleting the rows is not enough. djmoney caches resolved rates in
        # Django's cache (djmoney/contrib/exchange/models.py:49), and that
        # cache outlives a test's transaction rollback — so rates seeded by
        # test_recharge_currency_conversion leak into this class and
        # convert_money would quietly succeed. Clear both.
        Rate.objects.all().delete()
        cache.clear()
        self.tenant = Tenant.objects.create(name="No Rates Co")
        Tenant.objects.filter(pk=self.tenant.pk).update(balance=Money(Decimal("100.00"), "USD"))
        self.tenant.refresh_from_db()

    def test_mismatched_currency_raises_a_domain_error(self):
        """Not MissingRate escaping a signal — a named error the caller can act on."""
        with self.assertRaises(MissingExchangeRate):
            TenantTransaction.objects.create(
                tenant=self.tenant,
                amount=Money(Decimal("500.00"), "INR"),
                transaction_type=TransactionTypeChoices.SUCCESS_RECHARGE,
            )

    def test_the_failed_recharge_leaves_no_transaction_row(self):
        """The whole point: no payment recorded without a matching credit."""
        with self.assertRaises(MissingExchangeRate):
            TenantTransaction.objects.create(
                tenant=self.tenant,
                amount=Money(Decimal("500.00"), "INR"),
                transaction_type=TransactionTypeChoices.SUCCESS_RECHARGE,
            )

        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 0)

    def test_the_balance_is_untouched_after_a_failure(self):
        with self.assertRaises(MissingExchangeRate):
            TenantTransaction.objects.create(
                tenant=self.tenant,
                amount=Money(Decimal("500.00"), "INR"),
                transaction_type=TransactionTypeChoices.SUCCESS_RECHARGE,
            )

        self.assertEqual(Tenant.objects.get(pk=self.tenant.pk).balance, Money(Decimal("100.00"), "USD"))

    def test_the_error_says_how_to_fix_it(self):
        with self.assertRaises(MissingExchangeRate) as ctx:
            TenantTransaction.objects.create(
                tenant=self.tenant,
                amount=Money(Decimal("500.00"), "INR"),
                transaction_type=TransactionTypeChoices.SUCCESS_RECHARGE,
            )

        message = str(ctx.exception)
        self.assertIn("INR", message)
        self.assertIn("USD", message)
        self.assertIn("update_rates", message)

    def test_matching_currency_needs_no_rate_and_still_works(self):
        """The path that should be normal once platform currency is applied (#228)."""
        TenantTransaction.objects.create(
            tenant=self.tenant,
            amount=Money(Decimal("25.00"), "USD"),
            transaction_type=TransactionTypeChoices.SUCCESS_RECHARGE,
        )

        self.assertEqual(Tenant.objects.get(pk=self.tenant.pk).balance, Money(Decimal("125.00"), "USD"))
        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 1)

    def test_a_non_recharge_transaction_is_unaffected(self):
        """CONSUMPTION rows do not touch the balance, so no conversion is needed."""
        TenantTransaction.objects.create(
            tenant=self.tenant,
            amount=Money(Decimal("5.00"), "INR"),
            transaction_type=TransactionTypeChoices.CONSUMPTION,
        )

        self.assertEqual(Tenant.objects.get(pk=self.tenant.pk).balance, Money(Decimal("100.00"), "USD"))
        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 1)
