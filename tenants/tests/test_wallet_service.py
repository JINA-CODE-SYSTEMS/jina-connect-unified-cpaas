"""
Operator-applied wallet movements for offline settlement (#233).

Run with: python manage.py test tenants.tests.test_wallet_service

The partner invoices its customers and settles offline, so an operator moves
the balance by hand once an invoice is paid. That makes these the only writes
to a wallet a human performs directly, and the guard rails matter more than
the happy path: every failure below must leave both the ledger and the
balance exactly as they were.
"""

from decimal import Decimal

from django.test import TestCase
from djmoney.money import Money

from abstract.exceptions import WalletCreditError
from abstract.models import TransactionTypeChoices
from tenants.models import Tenant
from tenants.services.wallet import (
    CurrencyMismatch,
    DuplicateReference,
    credit_tenant_wallet,
    debit_tenant_wallet,
)
from transaction.models import TenantTransaction
from users.models import User


def _balance(tenant):
    return Tenant.objects.get(pk=tenant.pk).balance


class OfflineSettlementTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(
            username="ops", email="ops@test.invalid", password="x", mobile="+919000020001"
        )
        cls.other_actor = User.objects.create_user(
            username="ops2", email="ops2@test.invalid", password="x", mobile="+919000020002"
        )

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Settled Co")
        Tenant.objects.filter(pk=self.tenant.pk).update(balance=Decimal("100.00"), balance_currency="USD")
        self.tenant.refresh_from_db()

    # ── happy path ────────────────────────────────────────────────────────
    def test_a_credit_moves_the_balance_and_records_why(self):
        txn = credit_tenant_wallet(
            self.tenant, Money(Decimal("250.00"), "USD"), "INV-2026-0041", self.actor, note="EFT received"
        )

        self.assertEqual(_balance(self.tenant), Money(Decimal("350.00"), "USD"))
        self.assertEqual(txn.transaction_type, TransactionTypeChoices.MANUAL_CREDIT)
        self.assertEqual(txn.reference, "INV-2026-0041")
        self.assertEqual(txn.performed_by, self.actor)
        self.assertEqual(txn.note, "EFT received")

    def test_a_debit_reverses_one(self):
        credit_tenant_wallet(self.tenant, Money(Decimal("250.00"), "USD"), "INV-1", self.actor)

        debit_tenant_wallet(self.tenant, Money(Decimal("250.00"), "USD"), "INV-1-REVERSAL", self.actor)

        self.assertEqual(_balance(self.tenant), Money(Decimal("100.00"), "USD"))

    def test_a_reversal_leaves_both_rows_visible(self):
        """Corrections are append-only — the mistake stays as visible as the fix."""
        credit_tenant_wallet(self.tenant, Money(Decimal("50.00"), "USD"), "INV-2", self.actor)
        debit_tenant_wallet(self.tenant, Money(Decimal("50.00"), "USD"), "INV-2-REVERSAL", self.actor)

        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 2)

    def test_the_reference_is_trimmed(self):
        txn = credit_tenant_wallet(self.tenant, Money(Decimal("10.00"), "USD"), "  INV-3  ", self.actor)

        self.assertEqual(txn.reference, "INV-3")

    # ── idempotency ───────────────────────────────────────────────────────
    def test_the_same_reference_cannot_be_applied_twice(self):
        credit_tenant_wallet(self.tenant, Money(Decimal("250.00"), "USD"), "INV-DUP", self.actor)

        with self.assertRaises(DuplicateReference):
            credit_tenant_wallet(self.tenant, Money(Decimal("250.00"), "USD"), "INV-DUP", self.other_actor)

        self.assertEqual(_balance(self.tenant), Money(Decimal("350.00"), "USD"))
        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 1)

    def test_the_same_reference_on_a_different_tenant_is_fine(self):
        """Invoice numbering is per-customer; uniqueness must not be global."""
        other = Tenant.objects.create(name="Other Co")
        Tenant.objects.filter(pk=other.pk).update(balance=Decimal("0.00"), balance_currency="USD")

        credit_tenant_wallet(self.tenant, Money(Decimal("10.00"), "USD"), "INV-SHARED", self.actor)
        credit_tenant_wallet(other, Money(Decimal("20.00"), "USD"), "INV-SHARED", self.actor)

        self.assertEqual(_balance(other), Money(Decimal("20.00"), "USD"))

    def test_gateway_rows_without_a_reference_are_unconstrained(self):
        """The unique rule is scoped to manual types; machine rows have no reference."""
        for _ in range(3):
            TenantTransaction.objects.create(
                tenant=self.tenant,
                amount=Money(Decimal("1.00"), "USD"),
                transaction_type=TransactionTypeChoices.CONSUMPTION,
            )

        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 3)

    # ── rejections, each leaving nothing behind ───────────────────────────
    def _assert_nothing_happened(self):
        self.assertEqual(_balance(self.tenant), Money(Decimal("100.00"), "USD"))
        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 0)

    def test_a_currency_mismatch_is_refused_not_converted(self):
        with self.assertRaises(CurrencyMismatch):
            credit_tenant_wallet(self.tenant, Money(Decimal("250.00"), "INR"), "INV-FX", self.actor)

        self._assert_nothing_happened()

    def test_a_zero_amount_is_refused(self):
        with self.assertRaises(WalletCreditError):
            credit_tenant_wallet(self.tenant, Money(Decimal("0.00"), "USD"), "INV-ZERO", self.actor)

        self._assert_nothing_happened()

    def test_a_negative_amount_is_refused(self):
        """Negatives would turn a credit into a debit without saying so."""
        with self.assertRaises(WalletCreditError):
            credit_tenant_wallet(self.tenant, Money(Decimal("-50.00"), "USD"), "INV-NEG", self.actor)

        self._assert_nothing_happened()

    def test_a_blank_reference_is_refused(self):
        with self.assertRaises(WalletCreditError):
            credit_tenant_wallet(self.tenant, Money(Decimal("10.00"), "USD"), "   ", self.actor)

        self._assert_nothing_happened()

    def test_an_unattributed_movement_is_refused(self):
        with self.assertRaises(WalletCreditError):
            credit_tenant_wallet(self.tenant, Money(Decimal("10.00"), "USD"), "INV-NOACTOR", None)

        self._assert_nothing_happened()

    def test_the_mismatch_error_names_both_currencies(self):
        with self.assertRaises(CurrencyMismatch) as ctx:
            credit_tenant_wallet(self.tenant, Money(Decimal("10.00"), "INR"), "INV-MSG", self.actor)

        self.assertIn("USD", str(ctx.exception))
        self.assertIn("INR", str(ctx.exception))
