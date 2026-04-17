"""RCS Celery tasks for inbound and event processing."""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from contacts.models import AssigneeTypeChoices, ContactSource, TenantContact
from rcs.models import RCSOutboundMessage, RCSWebhookEvent
from rcs.providers import get_rcs_provider
from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices
from team_inbox.utils.inbox_message_factory import create_inbox_message

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_rcs_event_task(self, event_id: str):
    try:
        event = RCSWebhookEvent.objects.select_related("rcs_app", "tenant").get(pk=event_id)
    except RCSWebhookEvent.DoesNotExist:
        logger.error("RCSWebhookEvent %s not found", event_id)
        return

    if event.is_processed:
        return

    try:
        if event.event_type in ("MESSAGE", "SUGGESTION_RESPONSE", "LOCATION", "FILE"):
            _handle_inbound_message(event)
        elif event.event_type in ("DELIVERED", "READ"):
            _handle_delivery_event(event)
        # IS_TYPING — no action needed

        event.is_processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["is_processed", "processed_at"])
    except Exception as exc:
        event.retry_count += 1
        event.error_message = str(exc)[:2000]
        event.save(update_fields=["retry_count", "error_message"])
        # Exponential backoff: 60s → 120s → 240s, capped at 300s (#106)
        countdown = min(60 * (2 ** self.request.retries), 300)
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except self.MaxRetriesExceededError:
            event.error_message = f"FAILED after {self.max_retries} retries: {exc!s}"[:2000]
            event.save(update_fields=["error_message"])
            logger.error("RCSWebhookEvent %s FAILED after max retries", event_id)


def _handle_inbound_message(event: RCSWebhookEvent):
    """Process inbound RCS message: upsert contact → inbox → chatflow."""
    provider = get_rcs_provider(event.rcs_app)
    inbound = provider.parse_inbound_webhook(event.payload)

    # 1. Upsert contact by phone with fallback (#108)
    from contacts.services import resolve_or_create_contact

    contact = resolve_or_create_contact(
        tenant=event.tenant,
        source=ContactSource.RCS,
        phone=inbound.sender_phone,
    )

    # 2. Build inbox content
    if inbound.message_type == "text":
        content = {"type": "text", "body": {"text": inbound.text or ""}}
    elif inbound.message_type == "suggestion_response":
        content = {"type": "text", "body": {"text": inbound.suggestion_text or inbound.postback_data or ""}}
    elif inbound.message_type == "location":
        content = {"type": "location", "body": inbound.location or {}}
    elif inbound.message_type == "file":
        content = {"type": "file", "body": inbound.file_info or {}}
    else:
        content = {"type": "text", "body": {"text": str(inbound.raw_payload)}}

    # 3. Create team inbox message
    create_inbox_message(
        tenant=event.tenant,
        contact=contact,
        platform=MessagePlatformChoices.RCS,
        direction=MessageDirectionChoices.INCOMING,
        author=AuthorChoices.CONTACT,
        content=content,
        external_message_id=inbound.message_id,
    )

    # 4. Route to chat flow if assigned
    if inbound.message_type in ("text", "suggestion_response"):
        user_input = inbound.text or inbound.postback_data or inbound.suggestion_text or ""
        if user_input:
            _route_to_chatflow(contact, user_input)


def _handle_delivery_event(event: RCSWebhookEvent):
    """Update outbound message status from DELIVERED/READ event."""
    provider = get_rcs_provider(event.rcs_app)
    report = provider.parse_event_webhook(event.payload)

    message = RCSOutboundMessage.objects.filter(
        rcs_app=event.rcs_app,
        provider_message_id=report.message_id,
    ).first()
    if not message:
        logger.warning("RCS event for unknown message: %s", report.message_id)
        return

    update_fields = ["status"]
    if report.event_type == "DELIVERED":
        message.status = "DELIVERED"
        message.delivered_at = timezone.now()
        update_fields.append("delivered_at")
    elif report.event_type == "READ":
        message.status = "READ"
        message.read_at = timezone.now()
        update_fields.append("read_at")
        if not message.delivered_at:
            message.delivered_at = timezone.now()
            update_fields.append("delivered_at")

    message.save(update_fields=update_fields)

    # Keep broadcast delivery status in sync
    if message.broadcast_message:
        bm = message.broadcast_message
        status_map = {"DELIVERED": "DELIVERED", "READ": "DELIVERED"}
        bm.status = status_map.get(report.event_type, bm.status)
        bm.save(update_fields=["status"])


def _route_to_chatflow(contact: TenantContact, user_input: str):
    """Route RCS inbound input to active chatflow session (mirrors SMS pattern)."""
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
        additional_context={"platform": "RCS"},
    )

    if result.get("is_complete"):
        contact.assigned_to_type = AssigneeTypeChoices.UNASSIGNED
        contact.assigned_to_id = None
        contact.save(update_fields=["assigned_to_type", "assigned_to_id"])
