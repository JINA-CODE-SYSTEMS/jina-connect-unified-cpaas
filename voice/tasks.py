"""Voice Celery tasks.

Two tasks for #160:

  * ``initiate_call`` — place an outbound call via the adapter, populate
    ``provider_call_id`` on the ``VoiceCall`` row.
  * ``process_call_status`` — applied from the call-status webhook handler;
    updates ``VoiceCall.status`` and appends a ``VoiceCallEvent``. Fires
    the ``call.completed`` signal on terminal status so billing /
    team_inbox can react.

Heavier tasks (download_recording, transcribe_recording, billing
follow-ups) land in their respective PRs.
"""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from voice.adapters.base import CallInstructions
from voice.constants import TERMINAL_STATUSES, CallEventType, CallStatus
from voice.signals import call_completed

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def initiate_call(self, call_id: str) -> None:
    """Place an outbound call.

    Looks up the ``VoiceCall``, resolves its adapter, calls
    ``adapter.initiate_call``, and stashes the provider's CallSid on the
    row.
    """
    from voice.models import VoiceCall

    try:
        call = VoiceCall.objects.select_related("provider_config", "tenant").get(pk=UUID(call_id))
    except VoiceCall.DoesNotExist:
        logger.error("[voice.tasks.initiate_call] VoiceCall %s not found", call_id)
        return

    # Lazy import — avoid circular at module import time.
    from voice.adapters.registry import get_voice_adapter_cls

    adapter_cls = get_voice_adapter_cls(call.provider_config.provider)
    adapter = adapter_cls(call.provider_config)

    instructions = CallInstructions(
        flow_id=getattr(call.flow_session, "flow_id", None),
    )

    # Stamp the consent decision on the call so adapters that emit
    # recording verbs can check ``call.metadata["recording_allowed"]``
    # before attaching them (#171). The compliance helper returns True
    # for tenants that haven't opted into the consent gate, so this is
    # a no-op for the default-permissive case.
    from voice.compliance.consent import recording_allowed

    consent_ok = recording_allowed(call)
    call.metadata = {**call.metadata, "recording_allowed": consent_ok}

    callback_url = call.metadata.get("answer_callback_url", "")
    try:
        handle = adapter.initiate_call(
            from_number=call.from_number,
            to_number=call.to_number,
            callback_url=callback_url,
            instructions=instructions,
        )
    except Exception as exc:  # noqa: BLE001 — broad: retry any provider error
        logger.warning("[voice.tasks.initiate_call] adapter failed for %s: %s", call_id, exc)
        raise self.retry(exc=exc)

    call.provider_call_id = handle.provider_call_id
    call.status = CallStatus.INITIATING
    call.metadata = {**call.metadata, "initiate_response": handle.raw}
    call.save(update_fields=["provider_call_id", "status", "metadata", "updated_at"])
    logger.info(
        "[voice.tasks.initiate_call] %s placed as provider_call_id=%s",
        call_id,
        handle.provider_call_id,
    )


@shared_task
def process_call_status(payload: dict) -> None:
    """Apply a webhook event to a ``VoiceCall`` row.

    ``payload`` is the normalised dict produced by the webhook handler
    (after signature + idempotency pass). Must be JSON-serialisable so
    Celery can ship it.
    """
    from voice.models import VoiceCall, VoiceCallEvent

    provider_call_id = payload.get("provider_call_id")
    if not provider_call_id:
        logger.warning("[voice.tasks.process_call_status] missing provider_call_id in payload")
        return

    try:
        call = VoiceCall.objects.get(provider_call_id=provider_call_id)
    except VoiceCall.DoesNotExist:
        logger.warning(
            "[voice.tasks.process_call_status] unknown provider_call_id=%s",
            provider_call_id,
        )
        return

    new_status = payload.get("status")
    event_type = payload.get("event_type") or CallEventType.INITIATED
    hangup_cause = payload.get("hangup_cause") or ""
    raw = payload.get("raw") or {}
    now = timezone.now()

    with transaction.atomic():
        update_fields = ["updated_at"]
        if new_status and call.status not in TERMINAL_STATUSES:
            call.status = new_status
            update_fields.append("status")
        if new_status == CallStatus.IN_PROGRESS and call.started_at is None:
            call.started_at = now
            update_fields.append("started_at")
        if new_status in TERMINAL_STATUSES and call.ended_at is None:
            call.ended_at = now
            update_fields.append("ended_at")
            if call.started_at:
                call.duration_seconds = int((now - call.started_at).total_seconds())
                update_fields.append("duration_seconds")
            if hangup_cause:
                call.hangup_cause = hangup_cause
                update_fields.append("hangup_cause")
        call.metadata = {**call.metadata, "last_webhook": raw}
        update_fields.append("metadata")
        call.save(update_fields=update_fields)

        next_seq = (
            VoiceCallEvent.objects.filter(call=call).count() + 1
        )  # adequate for #160; #168 hardens with a max+1 query
        VoiceCallEvent.objects.create(
            call=call,
            name=event_type,  # required CharField from BaseModel
            event_type=event_type,
            payload=raw,
            occurred_at=now,
            sequence=next_seq,
        )

    if new_status in TERMINAL_STATUSES:
        call_completed.send(sender=VoiceCall, call=call)
