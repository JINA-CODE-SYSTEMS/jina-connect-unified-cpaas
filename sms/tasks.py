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
        raise self.retry(exc=exc)


def _handle_inbound_sms(event: SMSWebhookEvent):
    provider = get_sms_provider(event.sms_app)
    inbound = provider.parse_inbound_webhook(event.payload)

    contact, _ = TenantContact.objects.get_or_create(
        tenant=event.tenant,
        phone=inbound.from_number,
        defaults={"source": ContactSource.SMS},
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
