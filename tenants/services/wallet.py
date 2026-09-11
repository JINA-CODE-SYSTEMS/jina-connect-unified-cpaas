"""Operator-applied wallet movements for offline settlement (#233).

Some deployments invoice their customers and settle offline — bank transfer,
EFT, cheque — rather than taking card payments through the platform. Once an
invoice is paid, an operator credits the customer's wallet here.

Two things shape this module.

**The balance change and the record commit together, explicitly.** Gateway
recharges move the balance from a ``post_save`` receiver, which is how a
payment came to be recordable without a matching credit (#227). Nothing here
relies on a signal: the caller can see the whole operation at the call site.

**A credit has to be defensible months later.** Every movement carries a
reference to the real-world payment, a note, and the operator who applied it.
A reference is unique per tenant, so resubmitting a paid invoice cannot
double-credit an account.
"""

from decimal import Decimal

from django.db import IntegrityError
from django.db import transaction as db_transaction
from djmoney.money import Money

from abstract.exceptions import WalletCreditError
from abstract.models import TransactionTypeChoices
from tenants.models import Tenant
from transaction.models import TenantTransaction


class DuplicateReference(WalletCreditError):
    """This reference has already been applied to this tenant."""


class CurrencyMismatch(WalletCreditError):
    """The amount is not in the wallet's currency.

    Deliberately not converted. There are no exchange rates loaded (#227),
    and an operator crediting against an invoice knows what currency it was
    paid in — guessing on their behalf is how money goes missing.
    """


def _apply(tenant, amount, reference, actor, note, *, credit):
    if amount.amount <= Decimal("0"):
        raise WalletCreditError("Amount must be positive. Use a debit to reverse a credit.")
    if not reference or not reference.strip():
        raise WalletCreditError("A reference is required — the invoice or bank reference this settles.")
    if actor is None:
        raise WalletCreditError("An operator must be recorded against a manual wallet movement.")

    reference = reference.strip()

    with db_transaction.atomic():
        # Lock the row: two operators applying different invoices at the same
        # moment would otherwise both read the same starting balance and the
        # second write would discard the first.
        locked = Tenant.objects.select_for_update().get(pk=tenant.pk)

        if str(amount.currency) != str(locked.balance.currency):
            raise CurrencyMismatch(
                f"Wallet is in {locked.balance.currency}; the amount is in {amount.currency}. "
                f"Record the movement in the wallet's currency."
            )

        try:
            txn = TenantTransaction.objects.create(
                tenant=locked,
                amount=amount,
                transaction_type=(
                    TransactionTypeChoices.MANUAL_CREDIT if credit else TransactionTypeChoices.MANUAL_DEBIT
                ),
                reference=reference,
                note=note or "",
                performed_by=actor,
            )
        except IntegrityError as exc:
            raise DuplicateReference(
                f"Reference {reference!r} has already been applied to {locked.name}. Nothing was changed."
            ) from exc

        locked.balance = locked.balance + amount if credit else locked.balance - amount
        locked.save(update_fields=["balance", "balance_currency"])

    tenant.refresh_from_db()
    return txn


def credit_tenant_wallet(tenant: Tenant, amount: Money, reference: str, actor, note: str = "") -> TenantTransaction:
    """Credit *tenant* against a settled invoice. Returns the transaction row."""
    return _apply(tenant, amount, reference, actor, note, credit=True)


def debit_tenant_wallet(tenant: Tenant, amount: Money, reference: str, actor, note: str = "") -> TenantTransaction:
    """Reverse or correct a credit.

    A mistaken credit is corrected with an offsetting debit rather than by
    deleting the row, so the ledger stays append-only and the correction is
    as visible as the mistake.
    """
    return _apply(tenant, amount, reference, actor, note, credit=False)
