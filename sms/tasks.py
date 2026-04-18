"""SMS Celery tasks for inbound and DLR processing."""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from contacts.models import AssigneeTypeChoices, ContactSource, TenantContact
from sms.models import SMSOutboundMessage, SMSWebhookEvent
from sms.providers import get_sms_provider
from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices
from team_inbox.utils.inbox_message_factory import create_inbox_message

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_sms_event_task(self, event_id: str):
    try:
        event = SMSWebhookEvent.objects.select_related("sms_app", "tenant").get(pk=event_id)
    except SMSWebhookEvent.DoesNotExist:
        logger.error("SMSWebhookEvent %s not found", event_id)
        return

    if event.is_processed:
        return

    try:
        if event.event_type == "INBOUND":
            _handle_inbound_sms(event)
        elif event.event_type == "DLR":
            _handle_dlr(event)

        event.is_processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["is_processed", "processed_at"])
    except Exception as exc:
        event.retry_count += 1
        event.error_message = str(exc)[:2000]
        event.save(update_fields=["retry_count", "error_message"])
        # Exponential backoff: 60s → 120s → 240s, capped at 300s (#106)
        countdown = min(60 * (2**self.request.retries), 300)
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except self.MaxRetriesExceededError:
            event.error_message = f"FAILED after {self.max_retries} retries: {exc!s}"[:2000]
            event.save(update_fields=["error_message"])
            logger.error("SMSWebhookEvent %s FAILED after max retries", event_id)


def _handle_inbound_sms(event: SMSWebhookEvent):
    provider = get_sms_provider(event.sms_app)
    inbound = provider.parse_inbound_webhook(event.payload)

    from contacts.services import resolve_or_create_contact

    contact = resolve_or_create_contact(
        tenant=event.tenant,
        source=ContactSource.SMS,
        phone=inbound.from_number,
    )

    create_inbox_message(
        tenant=event.tenant,
        contact=contact,
        platform=MessagePlatformChoices.SMS,
        direction=MessageDirectionChoices.INCOMING,
        author=AuthorChoices.CONTACT,
        content={"type": "text", "body": {"text": inbound.body}},
        external_message_id=inbound.provider_message_id,
    )

    _route_to_chatflow(contact, inbound.body)


def _handle_dlr(event: SMSWebhookEvent):
    from broadcast.models import MessageStatusChoices

    provider = get_sms_provider(event.sms_app)
    dlr = provider.parse_dlr_webhook(event.payload)

    message = SMSOutboundMessage.objects.filter(
        sms_app=event.sms_app,
        provider_message_id=dlr.provider_message_id,
    ).first()
    if not message:
        logger.warning("DLR received for unknown SMS message: %s", dlr.provider_message_id)
        return

    message.status = dlr.status
    update_fields = ["status"]

    if dlr.status == "DELIVERED":
        message.delivered_at = timezone.now()
        update_fields.append("delivered_at")
    elif dlr.status in ("FAILED", "UNDELIVERED"):
        message.failed_at = timezone.now()
        message.error_code = dlr.error_code or ""
        message.error_message = dlr.error_message or ""
        update_fields.extend(["failed_at", "error_code", "error_message"])

    message.save(update_fields=update_fields)

    # Keep broadcast delivery status in sync when this SMS originated from a broadcast.
    if message.broadcast_message:
        bm = message.broadcast_message
        status_map = {
            "PENDING": MessageStatusChoices.PENDING,
            "QUEUED": MessageStatusChoices.QUEUED,
            "SENT": MessageStatusChoices.SENT,
            "DELIVERED": MessageStatusChoices.DELIVERED,
            "FAILED": MessageStatusChoices.FAILED,
            "UNDELIVERED": MessageStatusChoices.FAILED,
        }
        mapped_status = status_map.get(dlr.status)
        if mapped_status:
            bm.status = mapped_status
            if dlr.status in ("FAILED", "UNDELIVERED"):
                bm.response = dlr.error_message or dlr.error_code or bm.response
            bm.save(update_fields=["status", "response"] if dlr.status in ("FAILED", "UNDELIVERED") else ["status"])


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def process_sms_dlr_batch(self, event_ids: list):
    """Process a batch of SMS DLR webhook events efficiently (#105).

    Instead of one DB write per DLR, loads all messages in one query,
    applies status updates in memory, then bulk_updates.
    """
    from broadcast.models import MessageStatusChoices as BMStatus

    now = timezone.now()
    events = SMSWebhookEvent.objects.filter(
        pk__in=event_ids,
        event_type="DLR",
        is_processed=False,
    ).select_related("sms_app")

    if not events:
        return {"processed": 0}

    # Parse all DLRs
    dlr_map = {}  # provider_message_id -> (dlr, sms_app)
    for event in events:
        provider = get_sms_provider(event.sms_app)
        dlr = provider.parse_dlr_webhook(event.payload)
        if dlr.provider_message_id:
            dlr_map[dlr.provider_message_id] = (dlr, event.sms_app, event)

    if not dlr_map:
        SMSWebhookEvent.objects.filter(pk__in=event_ids).update(is_processed=True, processed_at=now)
        return {"processed": 0}

    # Single query to load all matching outbound messages
    messages = SMSOutboundMessage.objects.filter(
        provider_message_id__in=list(dlr_map.keys()),
    ).select_related("broadcast_message")

    msg_lookup = {m.provider_message_id: m for m in messages}

    to_update = []
    bm_to_update = []
    processed_events = []

    for pid, (dlr, sms_app, event) in dlr_map.items():
        message = msg_lookup.get(pid)
        if not message:
            logger.warning("Batch DLR: unknown SMS message %s", pid)
            processed_events.append(event.pk)
            continue

        message.status = dlr.status
        if dlr.status == "DELIVERED":
            message.delivered_at = now
        elif dlr.status in ("FAILED", "UNDELIVERED"):
            message.failed_at = now
            message.error_code = dlr.error_code or ""
            message.error_message = dlr.error_message or ""
        to_update.append(message)

        # Sync broadcast message status
        if message.broadcast_message:
            bm = message.broadcast_message
            status_map = {
                "SENT": BMStatus.SENT,
                "DELIVERED": BMStatus.DELIVERED,
                "FAILED": BMStatus.FAILED,
                "UNDELIVERED": BMStatus.FAILED,
            }
            mapped = status_map.get(dlr.status)
            if mapped:
                bm.status = mapped
                if dlr.status in ("FAILED", "UNDELIVERED"):
                    bm.response = dlr.error_message or dlr.error_code or bm.response
                bm_to_update.append(bm)

        processed_events.append(event.pk)

    # Bulk update outbound messages
    if to_update:
        SMSOutboundMessage.objects.bulk_update(
            to_update,
            fields=["status", "delivered_at", "failed_at", "error_code", "error_message"],
            batch_size=500,
        )

    # Bulk update broadcast messages
    if bm_to_update:
        from broadcast.models import BroadcastMessage

        BroadcastMessage.objects.bulk_update(bm_to_update, fields=["status", "response"], batch_size=500)

    # Mark events processed
    SMSWebhookEvent.objects.filter(pk__in=processed_events).update(is_processed=True, processed_at=now)

    logger.info(
        "Batch DLR processed: %d messages updated, %d broadcast statuses synced", len(to_update), len(bm_to_update)
    )
    return {"processed": len(to_update), "broadcast_synced": len(bm_to_update)}


@shared_task
def reconcile_stale_sms_dlrs():
    """Celery beat task: find SMS messages stuck in SENT for >1 hour and re-query provider (#107).

    For providers that support status polling (currently Twilio), queries the
    current status and updates accordingly.
    """
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(hours=1)
    stale = SMSOutboundMessage.objects.filter(
        status="SENT",
        sent_at__lte=cutoff,
        sent_at__gte=cutoff - timedelta(hours=24),  # only look back 24h
    ).select_related("sms_app")[:200]

    updated = 0
    for msg in stale:
        try:
            provider = get_sms_provider(msg.sms_app)
            if not hasattr(provider, "get_message_status"):
                continue
            status = provider.get_message_status(msg.provider_message_id)
            if status and status != msg.status:
                msg.status = status
                update_fields = ["status"]
                if status == "DELIVERED":
                    msg.delivered_at = timezone.now()
                    update_fields.append("delivered_at")
                elif status in ("FAILED", "UNDELIVERED"):
                    msg.failed_at = timezone.now()
                    update_fields.append("failed_at")
                msg.save(update_fields=update_fields)
                updated += 1
        except Exception:
            logger.warning("[reconcile_stale_sms_dlrs] Failed to poll status for %s", msg.pk, exc_info=True)

    if updated:
        logger.info("[reconcile_stale_sms_dlrs] Reconciled %d stale SMS DLRs", updated)
    return {"reconciled": updated}


def _route_to_chatflow(contact: TenantContact, user_input: str):
    """Route SMS inbound input to active chatflow session when assigned."""
    from chat_flow.models import ChatFlow, UserChatFlowSession
    from chat_flow.services.graph_executor import get_executor

    if contact.assigned_to_type != AssigneeTypeChoices.CHATFLOW:
        session = UserChatFlowSession.objects.filter(contact=contact, is_active=True, is_complete=False).first()
        if not session:
            return
        flow = session.flow
    else:
        flow = ChatFlow.objects.filter(pk=contact.assigned_to_id).first()
        if not flow:
            return

    executor = get_executor(flow)
    result = executor.process_input(
        contact_id=contact.id,
        user_input=user_input,
        additional_context={"platform": "SMS"},
    )

    if result.get("is_complete"):
        contact.assigned_to_type = AssigneeTypeChoices.UNASSIGNED
        contact.assigned_to_id = None
        contact.save(update_fields=["assigned_to_type", "assigned_to_id"])
