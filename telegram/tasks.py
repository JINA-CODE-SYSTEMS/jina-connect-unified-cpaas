"""
Telegram Celery tasks — webhook event processing.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_tg_event_task(self, event_id: str):
    """
    Process a single TelegramWebhookEvent.

    Dispatches to the appropriate handler based on event_type:
    - MESSAGE → upsert contact, create inbox message, route to chatflow
    - CALLBACK_QUERY → ack query, parse callback_data, route to chatflow
    - EDITED_MESSAGE → log for audit
    - UNKNOWN → mark processed
    """
    from django.utils import timezone

    from telegram.models import TelegramWebhookEvent

    try:
        event = TelegramWebhookEvent.objects.select_related("bot_app", "bot_app__tenant").get(pk=event_id)
    except TelegramWebhookEvent.DoesNotExist:
        logger.error("[process_tg_event_task] Event %s not found", event_id)
        return

    if event.is_processed:
        logger.info("[process_tg_event_task] Event %s already processed, skipping", event_id)
        return

    try:
        if event.event_type == "MESSAGE":
            _handle_message(event)
        elif event.event_type == "CALLBACK_QUERY":
            _handle_callback_query(event)
        elif event.event_type == "EDITED_MESSAGE":
            logger.info("[process_tg_event_task] Edited message event %s — logged for audit", event_id)
        else:
            logger.info("[process_tg_event_task] Unknown event type %s for %s", event.event_type, event_id)

        event.is_processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["is_processed", "processed_at"])

    except Exception as exc:
        event.retry_count += 1
        event.error_message = str(exc)[:2000]
        event.save(update_fields=["retry_count", "error_message"])
        logger.exception("[process_tg_event_task] Error processing event %s", event_id)
        raise self.retry(exc=exc)


def _handle_message(event):
    """Process an incoming Telegram message — upsert contact, write inbox."""
    from contacts.models import ContactSource, TenantContact
    from team_inbox.models import AuthorChoices, MessageDirectionChoices, MessagePlatformChoices
    from team_inbox.utils.inbox_message_factory import create_inbox_message

    payload = event.payload
    message = payload.get("message", {})
    from_user = message.get("from", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not chat_id:
        logger.warning("[_handle_message] No chat_id in event %s", event.pk)
        return

    tenant = event.bot_app.tenant

    # Upsert contact — only set names on creation, never overwrite existing data
    contact, created = TenantContact.objects.get_or_create(
        tenant=tenant,
        telegram_chat_id=chat_id,
        defaults={
            "first_name": (from_user.get("first_name") or "")[:150],
            "last_name": (from_user.get("last_name") or "")[:150],
            "telegram_username": (from_user.get("username") or "")[:150],
            "source": ContactSource.TELEGRAM,
        },
    )
    if created:
        logger.info("[_handle_message] Created new contact %s for chat_id %s", contact.pk, chat_id)

    # Determine message content type
    content = _extract_message_content(message)

    # Create inbox message
    create_inbox_message(
        tenant=tenant,
        contact=contact,
        platform=MessagePlatformChoices.TELEGRAM,
        direction=MessageDirectionChoices.INCOMING,
        author=AuthorChoices.CONTACT,
        content=content,
        external_message_id=str(message.get("message_id", "")),
    )

    # Route to ChatFlow if contact is assigned to one
    _handle_chatflow_routing_telegram(contact, content)


def _handle_callback_query(event):
    """Process an incoming Telegram callback query."""
    from contacts.models import TenantContact
    from telegram.services.bot_client import TelegramBotClient

    payload = event.payload
    callback = payload.get("callback_query", {})
    callback_query_id = callback.get("id")
    callback_data = callback.get("data", "")
    chat = callback.get("message", {}).get("chat", {})
    chat_id = chat.get("id")

    if not chat_id:
        logger.warning("[_handle_callback_query] No chat_id in event %s", event.pk)
        return

    # Acknowledge the callback query immediately
    try:
        client = TelegramBotClient(token=event.bot_app.bot_token)
        client.answer_callback_query(callback_query_id)
    except Exception:
        logger.exception("[_handle_callback_query] Failed to ack callback query %s", callback_query_id)

    # Resolve contact
    tenant = event.bot_app.tenant
    contact = TenantContact.objects.filter(
        tenant=tenant,
        telegram_chat_id=chat_id,
    ).first()

    if not contact:
        logger.warning("[_handle_callback_query] No contact for chat_id %s", chat_id)
        return

    # Route callback_data to chatflow engine
    _handle_chatflow_routing_telegram(
        contact,
        {"type": "button_reply", "body": {"text": callback_data}},
    )
    logger.info(
        "[_handle_callback_query] callback_data=%s from contact %s (chat_id %s)",
        callback_data,
        contact.pk,
        chat_id,
    )


def _extract_message_content(message: dict) -> dict:
    """
    Extract a normalised content dict from a Telegram message.

    Returns a dict matching the team_inbox content schema:
    {"type": "text|photo|document|video|audio|voice|location|contact", "body": {...}}
    """
    # Text message
    if "text" in message:
        return {"type": "text", "body": {"text": message["text"]}}

    # Photo (take the largest resolution)
    if "photo" in message:
        photos = message["photo"]
        largest = photos[-1] if photos else {}
        return {
            "type": "image",
            "body": {"text": message.get("caption", "")},
            "media": {"file_id": largest.get("file_id", ""), "mime_type": "image/jpeg"},
        }

    # Document
    if "document" in message:
        doc = message["document"]
        return {
            "type": "document",
            "body": {"text": message.get("caption", "")},
            "media": {
                "file_id": doc.get("file_id", ""),
                "file_name": doc.get("file_name", ""),
                "mime_type": doc.get("mime_type", ""),
            },
        }

    # Video
    if "video" in message:
        vid = message["video"]
        return {
            "type": "video",
            "body": {"text": message.get("caption", "")},
            "media": {"file_id": vid.get("file_id", ""), "mime_type": vid.get("mime_type", "video/mp4")},
        }

    # Audio
    if "audio" in message:
        aud = message["audio"]
        return {
            "type": "audio",
            "body": {"text": message.get("caption", "")},
            "media": {"file_id": aud.get("file_id", ""), "mime_type": aud.get("mime_type", "audio/mpeg")},
        }

    # Voice
    if "voice" in message:
        voice = message["voice"]
        return {
            "type": "audio",
            "body": {"text": ""},
            "media": {"file_id": voice.get("file_id", ""), "mime_type": voice.get("mime_type", "audio/ogg")},
        }

    # Location
    if "location" in message:
        loc = message["location"]
        return {
            "type": "location",
            "body": {"latitude": loc.get("latitude"), "longitude": loc.get("longitude")},
        }

    # Contact
    if "contact" in message:
        ct = message["contact"]
        return {
            "type": "contact",
            "body": {
                "phone_number": ct.get("phone_number", ""),
                "first_name": ct.get("first_name", ""),
                "last_name": ct.get("last_name", ""),
            },
        }

    # Fallback
    return {"type": "text", "body": {"text": "[Unsupported message type]"}}


def _handle_chatflow_routing_telegram(contact, message_content: dict):
    """
    Route incoming Telegram message/callback to ChatFlow if the contact is
    assigned to one.  Mirrors the WhatsApp ``_handle_chatflow_routing`` in
    ``wa/tasks.py``.

    Args:
        contact: TenantContact instance
        message_content: Normalised content dict (``{"type": ..., "body": ...}``)
    """
    from contacts.models import AssigneeTypeChoices

    chatflow_id = None

    if contact.assigned_to_type == AssigneeTypeChoices.CHATFLOW:
        chatflow_id = contact.assigned_to_id

    if not chatflow_id:
        from chat_flow.models import UserChatFlowSession

        active_session = (
            UserChatFlowSession.objects.filter(
                contact=contact,
                is_active=True,
                is_complete=False,
            )
            .select_related("flow")
            .first()
        )
        if active_session:
            chatflow_id = active_session.flow_id
        else:
            return  # Not in a chatflow

    try:
        from chat_flow.models import ChatFlow, UserChatFlowSession
        from chat_flow.services.graph_executor import get_executor

        flow = ChatFlow.objects.get(id=chatflow_id)
        session = UserChatFlowSession.objects.filter(contact=contact, flow=flow, is_active=True).first()

        if not session:
            return

        # Extract user input
        msg_type = message_content.get("type", "")
        user_input = None

        if msg_type == "button_reply":
            user_input = message_content.get("body", {}).get("text") or message_content.get("button_id")
        elif msg_type == "text":
            user_input = message_content.get("body", {}).get("text", "")
        else:
            logger.info(
                "[chatflow_routing_telegram] Unhandled type '%s' for contact %s",
                msg_type,
                contact.pk,
            )

        if not user_input:
            return

        executor = get_executor(flow)
        result = executor.process_input(
            contact_id=contact.id,
            user_input=user_input,
            additional_context={"platform": "TELEGRAM"},
        )

        logger.info(
            "[chatflow_routing_telegram] contact=%s flow=%s node=%s complete=%s",
            contact.pk,
            chatflow_id,
            result.get("current_node_id"),
            result.get("is_complete"),
        )

        if result.get("is_complete"):
            contact.assigned_to_type = AssigneeTypeChoices.UNASSIGNED
            contact.assigned_to_id = None
            contact.save(update_fields=["assigned_to_type", "assigned_to_id"])

    except Exception:
        logger.exception(
            "[chatflow_routing_telegram] Error for contact %s in flow %s",
            contact.pk,
            chatflow_id,
        )
