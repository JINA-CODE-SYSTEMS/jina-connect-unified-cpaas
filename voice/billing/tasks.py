"""Provider-cost billing tasks (#160).

For adapters that publish call cost via a separate API endpoint
(Twilio, Plivo, …), this fetches the authoritative price and writes
a ``TenantTransaction`` row. Scheduled with a 30s countdown from
``voice.signals.trigger_provider_cost_billing`` because Twilio
populates the price asynchronously after call completion.

Local rate-card billing (for SIP and providers without cost callbacks)
lives in #170 — this module only covers the provider-cost path.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def fetch_provider_cost(self, call_id: str) -> None:
    """Re-fetch the call from the provider, extract the cost, write a
    ``TenantTransaction``.

    For Twilio specifically: the ``Calls/{sid}.json`` endpoint returns
    ``price`` (e.g. ``-0.01400``) and ``price_unit`` (e.g. ``USD``)
    once Twilio has billed the call.
    """
    from voice.models import VoiceCall

    try:
        call = VoiceCall.objects.select_related("provider_config", "tenant").get(pk=UUID(call_id))
    except VoiceCall.DoesNotExist:
        logger.warning("[voice.billing.fetch_provider_cost] VoiceCall %s not found", call_id)
        return

    if call.cost_amount is not None:
        # Already billed — guard against duplicate fan-out.
        return

    from voice.adapters.registry import get_voice_adapter_cls

    adapter_cls = get_voice_adapter_cls(call.provider_config.provider)
    adapter = adapter_cls(call.provider_config)

    try:
        cost_amount, cost_currency = _fetch_twilio_price(adapter, call.provider_call_id)
    except Exception as exc:  # noqa: BLE001 — retry transient provider errors
        logger.info(
            "[voice.billing.fetch_provider_cost] %s not yet billed by provider (%s); retrying",
            call_id,
            exc,
        )
        raise self.retry(exc=exc)

    if cost_amount is None:
        # Provider hasn't published a price yet. Bounce — retries fall off
        # after max_retries.
        raise self.retry()

    _record_transaction(call, cost_amount, cost_currency)


def _fetch_twilio_price(adapter, provider_call_id: str) -> tuple[Decimal | None, str]:
    """Return ``(price, currency)`` from Twilio's Calls resource.

    Twilio reports price as a *negative* decimal string (cost to the
    account); we store it as a positive Decimal in ``cost_amount`` so
    aggregates read intuitively.
    """
    url = adapter._account_url(f"Calls/{provider_call_id}.json")
    resp = adapter._request("GET", url, auth=adapter._auth())
    resp.raise_for_status()
    body = resp.json()
    price_raw = body.get("price")
    price_unit = body.get("price_unit") or "USD"
    if price_raw is None:
        return None, price_unit
    return abs(Decimal(price_raw)), price_unit


def _record_transaction(call, cost_amount: Decimal, cost_currency: str) -> None:
    """Persist a ``TenantTransaction`` debit for this call.

    Uses the ``VOICE_OUTBOUND`` / ``VOICE_INBOUND`` transaction types
    already declared in ``abstract.models.TransactionTypeChoices`` (no
    schema change required).
    """
    from djmoney.money import Money

    from abstract.models import TransactionTypeChoices
    from transaction.models import TenantTransaction
    from voice.constants import CostSource

    transaction_type = (
        TransactionTypeChoices.VOICE_OUTBOUND if call.direction == "outbound" else TransactionTypeChoices.VOICE_INBOUND
    )

    # Update the call row too so admin / billing dashboards see the cost.
    call.cost_amount = cost_amount
    call.cost_currency = cost_currency
    call.cost_source = CostSource.PROVIDER
    call.save(update_fields=["cost_amount", "cost_currency", "cost_source", "updated_at"])

    TenantTransaction.objects.create(
        tenant=call.tenant,
        amount=Money(cost_amount, cost_currency),
        transaction_type=transaction_type,
        description=(
            f"Voice {call.direction} call {call.from_number}->{call.to_number} "
            f"({call.provider_call_id}); cost_source={CostSource.PROVIDER}"
        ),
    )
