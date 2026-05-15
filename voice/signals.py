"""Voice signals.

``call_completed`` fires when a ``VoiceCall`` reaches a terminal state.
Receivers in this module:

  * ``write_to_team_inbox`` — creates a ``team_inbox.Messages`` row so the
    call shows up in the unified inbox alongside text-channel messages.
  * ``trigger_provider_cost_billing`` — for adapters that support provider
    cost callbacks, schedules a follow-up task ~30s later to fetch the
    final price and record a ``TenantTransaction``.

Both receivers are kept in the same module so the post-call fan-out
lives in one place. Heavier fan-out (recording download, transcription,
SMS fallback) hooks into the same signal from later PRs.
"""

from __future__ import annotations

import logging

import django.dispatch
from django.dispatch import receiver

logger = logging.getLogger(__name__)


# ``call_completed`` is sent from ``voice.tasks.process_call_status`` once
# a call transitions to COMPLETED / FAILED / CANCELED.
call_completed = django.dispatch.Signal()


@receiver(call_completed)
def write_to_team_inbox(sender, call, **kwargs) -> None:
    """Create a ``team_inbox.Messages`` row for the completed call.

    ``Messages.platform`` already has a ``VOICE`` choice
    (see ``team_inbox/models.py``). The row carries a short text
    summary; full call detail lives on the linked ``VoiceCall``.
    """
    # Lazy import — avoids module-import-time circular with team_inbox.
    from team_inbox.models import (
        AuthorChoices,
        MessageDirectionChoices,
        MessagePlatformChoices,
        Messages,
    )

    if call.team_inbox_message_id:
        # Already mirrored (e.g. signal fired twice from idempotent webhooks).
        return

    summary = _summarise_call(call)
    direction = MessageDirectionChoices.INCOMING if call.direction == "inbound" else MessageDirectionChoices.OUTGOING
    author = AuthorChoices.CONTACT if call.direction == "inbound" else AuthorChoices.BOT

    try:
        msg = Messages.objects.create(
            tenant=call.tenant,
            platform=MessagePlatformChoices.VOICE,
            direction=direction,
            author=author,
            contact=call.contact,
            content={"type": "text", "body": {"text": summary}},
        )
    except Exception:  # noqa: BLE001 — inbox failure must not block billing
        logger.exception("[voice.signals.write_to_team_inbox] failed for call %s", call.id)
        return

    # Link both directions for easy lookup.
    call.team_inbox_message_id = msg.id
    call.save(update_fields=["team_inbox_message", "updated_at"])


def _summarise_call(call) -> str:
    direction = "Inbound" if call.direction == "inbound" else "Outbound"
    duration = f"{call.duration_seconds}s" if call.duration_seconds else "no answer"
    cause = f" — {call.hangup_cause}" if call.hangup_cause else ""
    return f"{direction} call {call.from_number} → {call.to_number} ({call.status}, {duration}){cause}"


@receiver(call_completed)
def trigger_provider_cost_billing(sender, call, **kwargs) -> None:
    """For adapters with ``supports_provider_cost``, schedule a delayed
    follow-up to fetch the provider's billed cost.

    Twilio publishes the call price ~30s after completion via a
    separate API endpoint. We defer the billing write until then so we
    record the authoritative number, not a local estimate.
    """
    from voice.adapters.registry import get_voice_adapter_cls

    try:
        adapter_cls = get_voice_adapter_cls(call.provider_config.provider)
    except NotImplementedError:
        return

    if not adapter_cls.capabilities.supports_provider_cost:
        return

    # Lazy import — billing module ships in #170 will extend this. For
    # #160 we have a thin provider-cost implementation in voice.billing.
    from voice.billing.tasks import fetch_provider_cost

    fetch_provider_cost.apply_async(args=[str(call.id)], countdown=30)
