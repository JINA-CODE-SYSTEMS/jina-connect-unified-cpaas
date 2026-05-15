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
def sync_broadcast_message_status(sender, call, **kwargs) -> None:
    """Mirror terminal ``VoiceCall.status`` onto its ``BroadcastMessage``.

    Maps:
      * COMPLETED + duration > 0 → DELIVERED
      * COMPLETED + duration == 0 → FAILED   (no-answer / never answered)
      * FAILED / CANCELED        → FAILED

    Only fires for calls that originated from a broadcast (the
    ``broadcast`` FK is set + ``metadata["broadcast_message_id"]`` was
    stamped by the dispatcher).
    """
    if call.broadcast_id is None:
        return

    bm_id = (call.metadata or {}).get("broadcast_message_id")
    if bm_id is None:
        return

    from broadcast.models import BroadcastMessage, MessageStatusChoices

    if call.status == "COMPLETED" and (call.duration_seconds or 0) > 0:
        target = MessageStatusChoices.DELIVERED
    else:
        # COMPLETED with 0s, FAILED, CANCELED all map to FAILED on the
        # broadcast-message side.
        target = MessageStatusChoices.FAILED

    BroadcastMessage.objects.filter(pk=bm_id).update(status=target)


@receiver(call_completed)
def release_concurrency_semaphore(sender, call, **kwargs) -> None:
    """Free the per-config concurrency slot once a call terminates.

    Acquired by ``broadcast.tasks.handle_voice_message`` when the call
    was dispatched. Calls outside a broadcast still consume / release
    a slot if the dispatcher used the semaphore — the operation is
    idempotent so a release with no prior acquire is harmless.
    """
    from voice.concurrency import release

    try:
        release(call.tenant_id, call.provider_config_id)
    except Exception:  # noqa: BLE001 — Redis hiccups must not block billing
        logger.exception(
            "[voice.signals.release_concurrency_semaphore] failed for call %s",
            call.id,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SIP provisioning — render PJSIP config when a SIP VoiceProviderConfig
# is saved / deleted so Asterisk has the trunk loaded before the first
# call lands.
# ─────────────────────────────────────────────────────────────────────────────


def _is_sip(provider_config) -> bool:
    return getattr(provider_config, "provider", "") == "sip"


def _on_provider_config_saved(sender, instance, **kwargs) -> None:
    if not _is_sip(instance):
        return
    try:
        from voice.sip_config.pjsip_writer import ensure_endpoint

        ensure_endpoint(instance)
    except Exception:  # noqa: BLE001
        logger.exception(
            "[voice.signals] failed to provision PJSIP for config %s",
            instance.pk,
        )


def _on_provider_config_deleted(sender, instance, **kwargs) -> None:
    if not _is_sip(instance):
        return
    try:
        from voice.sip_config.pjsip_writer import remove_endpoint

        remove_endpoint(instance)
    except Exception:  # noqa: BLE001
        logger.exception(
            "[voice.signals] failed to remove PJSIP config for %s",
            instance.pk,
        )


def _connect_provisioning_signals() -> None:
    """Wire ``post_save`` / ``post_delete`` on VoiceProviderConfig.

    Called lazily from the module body so the import doesn't fail when
    Django apps aren't ready yet.
    """
    from django.db.models.signals import post_delete, post_save

    from voice.models import VoiceProviderConfig

    post_save.connect(
        _on_provider_config_saved,
        sender=VoiceProviderConfig,
        dispatch_uid="voice.sip.ensure_endpoint",
    )
    post_delete.connect(
        _on_provider_config_deleted,
        sender=VoiceProviderConfig,
        dispatch_uid="voice.sip.remove_endpoint",
    )


_connect_provisioning_signals()


@receiver(call_completed)
def trigger_sms_fallback(sender, call, **kwargs) -> None:
    """Dispatch the SMS fallback when the call ended without engagement.

    All decisions / errors stay inside ``maybe_send_sms_fallback`` — it
    is fully fault-tolerant so a downed SMS provider doesn't crash the
    voice signal chain.
    """
    try:
        from voice.fallback import maybe_send_sms_fallback

        maybe_send_sms_fallback(call)
    except Exception:  # noqa: BLE001 — defence in depth around the swallow
        logger.exception("[voice.signals.trigger_sms_fallback] failed for call %s", call.id)


@receiver(call_completed)
def trigger_provider_cost_billing(sender, call, **kwargs) -> None:
    """Route the completed call into the right billing pipeline.

    Adapters with ``Capabilities.supports_provider_cost = True``
    (Twilio, Plivo, Vonage, Telnyx, Exotel) get the delayed-fetch path
    — Twilio publishes price ~30s after completion via a separate API
    endpoint, so we defer the billing write until then to record the
    authoritative number rather than a local estimate.

    Everything else (SIP, plus future providers without a cost
    callback) gets the local rate-card path which prices the call from
    ``VoiceRateCard`` rows on the provider config (#170).
    """
    from voice.adapters.registry import get_voice_adapter_cls

    try:
        adapter_cls = get_voice_adapter_cls(call.provider_config.provider)
    except NotImplementedError:
        return

    if adapter_cls.capabilities.supports_provider_cost:
        from voice.billing.tasks import fetch_provider_cost

        fetch_provider_cost.apply_async(args=[str(call.id)], countdown=30)
        return

    # Local rate-card path — SIP and any HTTP provider that doesn't
    # publish per-call cost.
    from voice.billing.tasks import rate_call_locally

    rate_call_locally.delay(str(call.id))
