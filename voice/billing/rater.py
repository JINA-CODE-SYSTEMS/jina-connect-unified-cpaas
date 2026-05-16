"""Local rate-card billing (#170).

For providers without a cost callback (SIP, and any HTTP provider whose
adapter sets ``Capabilities.supports_provider_cost = False``) we have to
price the call ourselves from a per-config ``VoiceRateCard``.

Lookup rule — longest matching ``destination_prefix`` for the dialled
number within the call's ``valid_from`` / ``valid_to`` window. Once we
have a matching rate, billable duration rounds up to the next
``billing_increment_seconds`` chunk, then we multiply by the per-minute
rate. The result lands on ``VoiceCall.cost_*`` and as a
``TenantTransaction`` debit, so wallet aggregates pick it up.

Wired to ``call_completed`` so this fan-out happens automatically when
a call terminates.
"""

from __future__ import annotations

import logging
import math
from decimal import ROUND_HALF_UP, Decimal

logger = logging.getLogger(__name__)


def compute_local_cost(call) -> tuple[Decimal | None, str, "VoiceRateCard | None"]:  # noqa: F821
    """Look up the matching rate card and compute the cost.

    Returns ``(cost, currency, rate_card)``. When no card matches we
    return ``(None, "", None)`` so callers can log and skip without
    inventing a price.
    """

    if not call.duration_seconds:
        # Calls that never connected (no-answer / failed-pre-connect) get
        # a zero-cost row so audit still shows the attempt, but we don't
        # need a rate card for them.
        currency = call.provider_config.currency or "USD"
        return Decimal("0"), currency, None

    to_number = (call.to_number or "").strip()
    if not to_number:
        return None, "", None

    rate_card = _pick_rate_card(call.provider_config_id, to_number, call.ended_at)
    if rate_card is None:
        return None, "", None

    increment = max(int(rate_card.billing_increment_seconds or 1), 1)
    billable_seconds = math.ceil(call.duration_seconds / increment) * increment
    # rate_per_minute * (billable_seconds / 60), rounded to 6 dp to match
    # the column precision.
    cost = (Decimal(rate_card.rate_per_minute) * Decimal(billable_seconds) / Decimal(60)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )
    return cost, rate_card.currency or "USD", rate_card


def _pick_rate_card(provider_config_id, to_number: str, when):
    """Return the longest-prefix-match rate card, or ``None``.

    The DB query yanks every card valid at ``when`` for the config
    (small set per config by design) then we resolve longest-prefix in
    Python — keeps the query simple and avoids DB-specific ``LIKE``
    shenanigans.
    """
    from django.db.models import Q
    from django.utils import timezone

    from voice.models import VoiceRateCard

    now = when or timezone.now()
    candidates = list(
        VoiceRateCard.objects.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=now),
            provider_config_id=provider_config_id,
            valid_from__lte=now,
        )
    )
    matches = [c for c in candidates if to_number.startswith(c.destination_prefix)]
    if not matches:
        return None
    matches.sort(key=lambda c: len(c.destination_prefix), reverse=True)
    return matches[0]


def rate_call_and_record(call) -> bool:
    """Compute + persist the local cost for ``call``.

    Returns ``True`` if a transaction was written, ``False`` if the
    call was skipped (already billed, no matching rate card, zero
    duration with no card to default the currency, …). Designed to be
    idempotent so a re-fired signal doesn't double-bill.
    """
    from djmoney.money import Money

    from abstract.models import TransactionTypeChoices
    from transaction.models import TenantTransaction
    from voice.constants import CostSource

    if call.cost_amount is not None:
        # Already billed — provider path won or this signal fired twice.
        return False

    cost, currency, _ = compute_local_cost(call)
    if cost is None:
        logger.info(
            "[voice.billing.rater] no matching rate card for call %s (to=%s); skipping local billing",
            call.id,
            call.to_number,
        )
        return False

    call.cost_amount = cost
    call.cost_currency = currency
    call.cost_source = CostSource.LOCAL_RATECARD
    call.save(update_fields=["cost_amount", "cost_currency", "cost_source", "updated_at"])

    transaction_type = (
        TransactionTypeChoices.VOICE_OUTBOUND if call.direction == "outbound" else TransactionTypeChoices.VOICE_INBOUND
    )
    TenantTransaction.objects.create(
        tenant=call.tenant,
        amount=Money(cost, currency),
        transaction_type=transaction_type,
        description=(
            f"Voice {call.direction} call {call.from_number}->{call.to_number} "
            f"({call.provider_call_id}); cost_source={CostSource.LOCAL_RATECARD}"
        ),
    )
    return True
