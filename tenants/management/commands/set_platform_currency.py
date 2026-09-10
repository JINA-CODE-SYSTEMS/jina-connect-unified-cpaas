"""Move existing wallets onto the platform currency (#228).

Changing ``PLATFORM_DEFAULT_CURRENCY`` only affects wallets created after the
change. Existing rows keep whatever currency they were written with, and
nothing reconciles them — django-money will then refuse to compare a balance
against a threshold in a different currency.

This command does that reconciliation, and is deliberately reluctant:

* it **relabels**, it does not convert. There are no exchange rates loaded
  (see #227), and converting balances is a financial operation that needs
  rates, an audit trail and reconciliation — not a management command.
* so it only touches wallets whose balance and credit line are **zero**,
  where relabelling cannot change what anyone is owed.
* it refuses to run at all once transactions exist, unless forced, because
  past transactions are denominated in the old currency and relabelling the
  wallet silently detaches the balance from its own history.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction
from djmoney.money import Money

from tenants.models import Tenant


class Command(BaseCommand):
    help = "Relabel zero-balance tenant wallets onto the platform currency."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            dest="target",
            default=None,
            help="Currency to move to. Defaults to settings.PLATFORM_DEFAULT_CURRENCY.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report what would change and exit.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Proceed even when transactions exist or balances are non-zero. Read the warnings first.",
        )

    def handle(self, *args, **options):
        target = options["target"] or getattr(settings, "PLATFORM_DEFAULT_CURRENCY", "USD")
        allowed = getattr(settings, "CURRENCIES", None)
        if allowed and target not in allowed:
            raise CommandError(f"{target} is not in settings.CURRENCIES {tuple(allowed)}.")

        dry_run, force = options["dry_run"], options["force"]

        from transaction.models import TenantTransaction

        txn_count = TenantTransaction.objects.count()
        if txn_count and not force:
            raise CommandError(
                f"{txn_count} transaction(s) exist. Those are denominated in the current "
                f"currency, and relabelling wallets would detach a balance from its own "
                f"history. Re-run with --force only if you understand that."
            )

        mismatched = [t for t in Tenant.objects.all() if str(t.balance.currency) != target]
        if not mismatched:
            self.stdout.write(self.style.SUCCESS(f"All tenant wallets are already in {target}. Nothing to do."))
            return

        safe, unsafe = [], []
        for tenant in mismatched:
            if tenant.balance.amount == 0 and tenant.credit_line.amount == 0:
                safe.append(tenant)
            else:
                unsafe.append(tenant)

        self.stdout.write(f"Target currency: {target}")
        self.stdout.write(f"Wallets not in {target}: {len(mismatched)}  (safe: {len(safe)}, non-zero: {len(unsafe)})")

        for tenant in unsafe:
            self.stdout.write(
                self.style.WARNING(
                    f"  non-zero  {tenant.name}: balance={tenant.balance}, credit_line={tenant.credit_line}"
                )
            )
        if unsafe and not force:
            self.stdout.write(
                self.style.WARNING(
                    "  ^ skipped. Relabelling these would change what they are worth. "
                    "Settle or convert them deliberately, then re-run."
                )
            )

        to_change = safe + (unsafe if force else [])
        if dry_run:
            self.stdout.write(
                self.style.NOTICE(f"--dry-run: would update {len(to_change)} wallet(s). No changes made.")
            )
            return

        with db_transaction.atomic():
            for tenant in to_change:
                tenant.balance = Money(tenant.balance.amount, target)
                tenant.credit_line = Money(tenant.credit_line.amount, target)
                tenant.threshold_alert = Money(tenant.threshold_alert.amount, target)
                # django-money keeps the currency in a separate column, and
                # update_fields does not infer it — omitting these writes the
                # amount back while leaving the old currency in place.
                tenant.save(
                    update_fields=[
                        "balance",
                        "balance_currency",
                        "credit_line",
                        "credit_line_currency",
                        "threshold_alert",
                        "threshold_alert_currency",
                    ]
                )

        self.stdout.write(self.style.SUCCESS(f"Updated {len(to_change)} wallet(s) to {target}."))
        if unsafe and not force:
            self.stdout.write(self.style.WARNING(f"{len(unsafe)} non-zero wallet(s) left untouched."))
