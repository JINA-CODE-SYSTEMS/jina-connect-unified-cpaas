"""
Platform currency is per-deployment and applies to new wallets (#228).

Run with: python manage.py test tenants.tests.test_platform_currency

A MoneyField's default_currency is fixed at class-definition time and frozen
into the migration, so it cannot follow a setting. New wallets are stamped at
creation instead. The rules that matter:

  * all three wallet fields move together, or none do
  * a currency the caller chose explicitly is never relabelled
"""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from djmoney.money import Money

from abstract.models import TransactionTypeChoices
from tenants.models import Tenant
from transaction.models import TenantTransaction


class NewWalletStampingTestCase(TestCase):
    def test_default_deployment_is_unchanged(self):
        """PLATFORM_DEFAULT_CURRENCY defaults to USD, so nothing moves."""
        tenant = Tenant.objects.create(name="Default Co")

        self.assertEqual(str(tenant.balance.currency), "USD")
        self.assertEqual(str(tenant.threshold_alert.currency), "USD")

    @override_settings(PLATFORM_DEFAULT_CURRENCY="ZAR")
    def test_a_new_wallet_takes_the_platform_currency(self):
        tenant = Tenant.objects.create(name="ZAR Co")

        self.assertEqual(str(tenant.balance.currency), "ZAR")
        self.assertEqual(str(tenant.credit_line.currency), "ZAR")
        self.assertEqual(str(tenant.threshold_alert.currency), "ZAR")

    @override_settings(PLATFORM_DEFAULT_CURRENCY="ZAR")
    def test_all_three_fields_move_together(self):
        """A mixed-currency wallet would raise the moment it was checked."""
        tenant = Tenant.objects.create(name="Mixed Co")

        # is_below_threshold compares total_balance against threshold_alert;
        # django-money raises on arithmetic across currencies.
        self.assertIsInstance(tenant.is_below_threshold, bool)
        self.assertIsInstance(tenant.is_overdrawn, bool)

    @override_settings(PLATFORM_DEFAULT_CURRENCY="ZAR")
    def test_an_explicit_currency_wins_over_the_platform_default(self):
        """The caller's choice is honoured, and the amount is never converted."""
        tenant = Tenant.objects.create(name="Explicit Co", balance=Money(Decimal("250.00"), "INR"))

        self.assertEqual(str(tenant.balance.currency), "INR")
        self.assertEqual(tenant.balance.amount, Decimal("250.00"))

    @override_settings(PLATFORM_DEFAULT_CURRENCY="ZAR")
    def test_an_explicit_currency_pulls_the_other_fields_with_it(self):
        """Previously this combination produced a mixed wallet that threw on save."""
        tenant = Tenant.objects.create(name="Pull Co", balance=Money(Decimal("250.00"), "INR"))

        self.assertEqual(str(tenant.credit_line.currency), "INR")
        self.assertEqual(str(tenant.threshold_alert.currency), "INR")
        self.assertEqual(tenant.threshold_alert.amount, Decimal("10"))

    @override_settings(PLATFORM_DEFAULT_CURRENCY="ZAR")
    def test_stamping_only_happens_on_creation(self):
        """An existing wallet is never re-stamped by a later save."""
        tenant = Tenant.objects.create(name="Once Co")
        Tenant.objects.filter(pk=tenant.pk).update(
            balance=Decimal("40.00"),
            balance_currency="INR",
            credit_line_currency="INR",
            threshold_alert_currency="INR",
        )
        tenant.refresh_from_db()

        tenant.save()

        self.assertEqual(str(Tenant.objects.get(pk=tenant.pk).balance.currency), "INR")


@override_settings(PLATFORM_DEFAULT_CURRENCY="ZAR")
class SetPlatformCurrencyCommandTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Legacy Co")
        Tenant.objects.filter(pk=self.tenant.pk).update(
            balance=Decimal("0"),
            balance_currency="USD",
            credit_line=Decimal("0"),
            credit_line_currency="USD",
            threshold_alert=Decimal("10"),
            threshold_alert_currency="USD",
        )
        self.tenant.refresh_from_db()

    def _run(self, *args):
        out = StringIO()
        call_command("set_platform_currency", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_a_zero_balance_wallet_is_relabelled(self):
        self._run()

        self.assertEqual(str(Tenant.objects.get(pk=self.tenant.pk).balance.currency), "ZAR")

    def test_dry_run_changes_nothing(self):
        output = self._run("--dry-run")

        self.assertIn("dry-run", output)
        self.assertEqual(str(Tenant.objects.get(pk=self.tenant.pk).balance.currency), "USD")

    def test_a_non_zero_wallet_is_skipped(self):
        Tenant.objects.filter(pk=self.tenant.pk).update(balance=Decimal("75.00"), balance_currency="USD")

        output = self._run()

        self.assertIn("non-zero", output)
        self.assertEqual(str(Tenant.objects.get(pk=self.tenant.pk).balance.currency), "USD")

    def test_it_refuses_once_transactions_exist(self):
        TenantTransaction.objects.create(
            tenant=self.tenant,
            amount=Money(Decimal("5.00"), "USD"),
            transaction_type=TransactionTypeChoices.CONSUMPTION,
        )

        with self.assertRaises(CommandError) as ctx:
            self._run()

        self.assertIn("transaction", str(ctx.exception).lower())

    def test_an_unsupported_currency_is_rejected(self):
        with self.assertRaises(CommandError):
            self._run("--to", "XYZ")


@override_settings(PLATFORM_DEFAULT_CURRENCY="ZAR")
class EditingAWalletKeepsItsCurrencyTestCase(TestCase):
    """Editing an amount must not relabel the money (#228).

    The host dashboard PATCHes balance, credit line and threshold as bare
    numbers. A MoneyField's default_currency is USD for every wallet, so
    djmoney resolves a bare number to USD — which on a non-USD deployment
    turns R1,000 into $1,000: same digits, different money, no conversion.

    Nothing downstream catches it. Stamping runs only at creation, and the
    mixed-currency guard cannot fire because all three fields are sent
    together, so they land in USD consistently and the arithmetic stays valid.
    """

    def setUp(self):
        from tenants.serializers import TenantSerializer

        self.serializer_class = TenantSerializer
        self.tenant = Tenant.objects.create(name="ZA Co")
        self.assertEqual(str(self.tenant.balance.currency), "ZAR")

    def _patch(self, payload):
        serializer = self.serializer_class(self.tenant, data=payload, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.tenant.refresh_from_db()

    def test_a_bare_amount_does_not_restamp_the_wallet(self):
        self._patch({"balance": 1000.0, "credit_line": 500.0, "threshold_alert": 10.0})

        self.assertEqual(self.tenant.balance, Money(Decimal("1000"), "ZAR"))
        self.assertEqual(self.tenant.credit_line, Money(Decimal("500"), "ZAR"))
        self.assertEqual(self.tenant.threshold_alert, Money(Decimal("10"), "ZAR"))

    def test_editing_one_field_leaves_the_others_alone(self):
        self._patch({"balance": 250.0})

        self.assertEqual(str(self.tenant.balance.currency), "ZAR")
        self.assertEqual(self.tenant.balance.amount, Decimal("250"))
        self.assertEqual(str(self.tenant.credit_line.currency), "ZAR")

    def test_an_explicit_currency_still_wins(self):
        """Relabelling on purpose must stay possible — that is what the
        set-platform-currency command does.

        All three fields have to move together: a wallet holding a mix is
        unusable, and BaseWallet.save rejects it.
        """
        self._patch(
            {
                "balance": 1000.0,
                "balance_currency": "USD",
                "credit_line": 500.0,
                "credit_line_currency": "USD",
                "threshold_alert": 10.0,
                "threshold_alert_currency": "USD",
            }
        )

        self.assertEqual(str(self.tenant.balance.currency), "USD")
        self.assertEqual(str(self.tenant.credit_line.currency), "USD")

    def test_relabelling_a_single_field_is_still_refused(self):
        """The mixed-currency guard must keep working.

        It is precisely because the dashboard sends all three fields together
        that this guard never fired on the bug above — they landed in USD
        consistently, so the arithmetic stayed valid and nothing complained.
        """
        serializer = self.serializer_class(
            self.tenant, data={"balance": 1000.0, "balance_currency": "USD"}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        with self.assertRaises(TypeError):
            serializer.save()

    def test_the_amount_is_never_converted(self):
        """Preserving the currency must not quietly apply an exchange rate."""
        self._patch({"balance": 1234.56})

        self.assertEqual(self.tenant.balance, Money(Decimal("1234.56"), "ZAR"))


@override_settings(PLATFORM_DEFAULT_CURRENCY="USD")
class EditingAUsdWalletIsUnaffectedTestCase(TestCase):
    def test_a_usd_deployment_behaves_exactly_as_before(self):
        from tenants.serializers import TenantSerializer

        tenant = Tenant.objects.create(name="US Co")
        serializer = TenantSerializer(tenant, data={"balance": 42.0}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        tenant.refresh_from_db()

        self.assertEqual(tenant.balance, Money(Decimal("42"), "USD"))
