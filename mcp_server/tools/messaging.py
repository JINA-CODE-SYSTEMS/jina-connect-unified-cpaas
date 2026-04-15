"""
MCP messaging tools — send_template, send_message, get_message_status, list_templates.
"""

from __future__ import annotations

from typing import Optional

import phonenumbers

from mcp_server.auth import resolve_tenant
from mcp_server.server import mcp


@mcp.tool()
def send_template(
    api_key: str,
    phone: str,
    template_name: str,
    params: Optional[dict] = None,
    language_code: str = "en",
) -> dict:
    """Send a WhatsApp template message to a phone number.

    Args:
        api_key: Your Jina Connect API key (from tenant access keys).
        phone: Recipient phone number with country code (e.g. +919876543210).
        template_name: The element_name of an APPROVED template.
        params: Template placeholder values, e.g. {"1": "John", "2": "Order #123"}.
        language_code: Template language code (default: "en").
    """
    from contacts.models import TenantContact
    from wa.models import MessageDirection, MessageStatus, MessageType, WAMessage, WATemplate

    tenant, wa_app = resolve_tenant(api_key)
    if not wa_app:
        return {"error": "No WhatsApp app configured for this tenant."}

    # Find the template
    try:
        template = WATemplate.objects.get(
            wa_app=wa_app,
            element_name=template_name,
            language_code=language_code,
            status="APPROVED",
        )
    except WATemplate.DoesNotExist:
        return {
            "error": f"Template '{template_name}' (lang={language_code}) not found or not approved.",
        }

    # Get or create contact
    contact, _ = TenantContact.objects.get_or_create(
        tenant=tenant,
        phone=phone,
        defaults={"first_name": phone, "source": "WHATSAPP"},
    )

    # Create WAMessage — the post_save signal queues it for sending
    message = WAMessage.objects.create(
        wa_app=wa_app,
        contact=contact,
        direction=MessageDirection.OUTBOUND,
        message_type=MessageType.TEMPLATE,
        status=MessageStatus.PENDING,
        template=template,
        template_params=params or {},
        text=template.content,
    )

    return {
        "message_id": str(message.id),
        "status": message.status,
        "template": template_name,
        "phone": phone,
    }


@mcp.tool()
def send_message(
    api_key: str,
    phone: str,
    text: str,
    channel: str = "WHATSAPP",
) -> dict:
    """Send a plain text message to a phone number or Telegram user.

    Note: For WhatsApp, the recipient must have an active conversation window (24-hour rule).
    For Telegram, the user must have started a conversation with the bot.

    Args:
        api_key: Your Jina Connect API key.
        phone: Recipient phone number (WhatsApp) or Telegram chat ID.
        text: The message text to send.
        channel: Channel — WHATSAPP (default), TELEGRAM, or SMS.
    """
    _ALLOWED_CHANNELS = {"WHATSAPP", "TELEGRAM", "SMS", "RCS"}
    normalized = channel.strip().upper()
    if normalized not in _ALLOWED_CHANNELS:
        return {"error": f"Unsupported channel '{channel}'. Allowed: {', '.join(sorted(_ALLOWED_CHANNELS))}"}

    if normalized == "TELEGRAM":
        return _send_telegram_message(api_key, phone, text)
    if normalized == "SMS":
        return _send_sms_message(api_key, phone, text)
    if normalized == "RCS":
        return _send_rcs_message(api_key, phone, text)

    from contacts.models import TenantContact
    from wa.models import MessageDirection, MessageStatus, MessageType, WAMessage

    tenant, wa_app = resolve_tenant(api_key)
    if not wa_app:
        return {"error": "No WhatsApp app configured for this tenant."}

    contact, _ = TenantContact.objects.get_or_create(
        tenant=tenant,
        phone=phone,
        defaults={"first_name": phone, "source": "WHATSAPP"},
    )

    message = WAMessage.objects.create(
        wa_app=wa_app,
        contact=contact,
        direction=MessageDirection.OUTBOUND,
        message_type=MessageType.TEXT,
        status=MessageStatus.PENDING,
        text=text,
    )

    return {
        "message_id": str(message.id),
        "status": message.status,
        "phone": phone,
    }


@mcp.tool()
def get_message_status(api_key: str, message_id: str) -> dict:
    """Get the delivery status of a WhatsApp message.

    Args:
        api_key: Your Jina Connect API key.
        message_id: The message UUID returned by send_template or send_message.
    """
    from wa.models import WAMessage

    tenant, _ = resolve_tenant(api_key)

    try:
        msg = WAMessage.objects.select_related("contact", "template").get(
            id=message_id,
            wa_app__tenant=tenant,
        )
    except WAMessage.DoesNotExist:
        return {"error": f"Message {message_id} not found."}

    result = {
        "message_id": str(msg.id),
        "status": msg.status,
        "direction": msg.direction,
        "message_type": msg.message_type,
        "phone": str(msg.contact.phone) if msg.contact else None,
        "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
        "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
        "read_at": msg.read_at.isoformat() if msg.read_at else None,
    }

    if msg.error_message:
        result["error_message"] = msg.error_message

    return result


@mcp.tool()
def list_templates(
    api_key: str,
    status: Optional[str] = None,
) -> dict:
    """List WhatsApp message templates for your account.

    Args:
        api_key: Your Jina Connect API key.
        status: Optional filter — DRAFT, PENDING, APPROVED, REJECTED, PAUSED, DISABLED, FAILED.
    """
    from wa.models import WATemplate

    _, wa_app = resolve_tenant(api_key)
    if not wa_app:
        return {"error": "No WhatsApp app configured for this tenant."}

    qs = WATemplate.objects.filter(wa_app=wa_app).order_by("-created_at")
    if status:
        qs = qs.filter(status=status.upper())

    templates = []
    for t in qs[:50]:
        templates.append(
            {
                "id": str(t.id),
                "name": t.element_name,
                "status": t.status,
                "category": t.category,
                "language": t.language_code,
                "content": t.content[:200] if t.content else None,
                "type": t.template_type,
            }
        )

    return {"count": len(templates), "templates": templates}


# ── Telegram helpers ──────────────────────────────────────────────────────────


def _send_telegram_message(api_key: str, chat_id: str, text: str) -> dict:
    """Send a plain text message via Telegram Bot API."""
    from telegram.models import TelegramBotApp
    from telegram.services.message_sender import TelegramMessageSender

    tenant, _ = resolve_tenant(api_key)

    bot_app = TelegramBotApp.objects.filter(tenant=tenant, is_active=True).first()
    if not bot_app:
        return {"error": "No active Telegram bot configured for this tenant."}

    sender = TelegramMessageSender(bot_app)
    result = sender.send_text(chat_id=str(chat_id), text=text)

    if result.get("success"):
        return {
            "message_id": result.get("message_id", ""),
            "status": "SENT",
            "chat_id": chat_id,
            "channel": "TELEGRAM",
        }
    return {"error": result.get("error", "Failed to send Telegram message.")}


def _send_sms_message(api_key: str, phone: str, text: str) -> dict:
    """Send a plain text message via SMS provider."""
    from sms.models import SMSApp
    from sms.services.message_sender import SMSMessageSender

    normalized_text = (text or "").strip()
    if not normalized_text:
        return {"error": "SMS text cannot be empty."}

    try:
        parsed = phonenumbers.parse(str(phone), None)
        if not phonenumbers.is_valid_number(parsed):
            return {"error": f"Invalid SMS phone number: {phone!r}. Use E.164 format, e.g. +14155552671."}
    except phonenumbers.NumberParseException:
        return {"error": f"Invalid SMS phone number: {phone!r}. Use E.164 format, e.g. +14155552671."}

    tenant, _ = resolve_tenant(api_key)

    sms_app = SMSApp.objects.filter(tenant=tenant, is_active=True).first()
    if not sms_app:
        return {"error": "No active SMS app configured for this tenant."}

    sender = SMSMessageSender(sms_app)
    result = sender.send_text(chat_id=str(phone), text=normalized_text)

    if result.get("success"):
        return {
            "message_id": result.get("message_id", ""),
            "status": "SENT",
            "phone": phone,
            "channel": "SMS",
        }
    return {"error": result.get("error", "Failed to send SMS message.")}


# ── RCS helpers & tools ───────────────────────────────────────────────────────


def _send_rcs_message(api_key: str, phone: str, text: str) -> dict:
    """Send a plain RCS text message (used by send_message channel=RCS)."""
    from rcs.models import RCSApp
    from rcs.services.message_sender import RCSMessageSender

    tenant, _ = resolve_tenant(api_key)

    rcs_app = RCSApp.objects.filter(tenant=tenant, is_active=True).first()
    if not rcs_app:
        return {"error": "No active RCS app configured for this tenant."}

    sender = RCSMessageSender(rcs_app)
    result = sender.send_text(chat_id=str(phone), text=text)

    if result.get("success"):
        return {
            "message_id": result.get("message_id", ""),
            "status": "SENT",
            "phone": phone,
            "channel": "RCS",
        }
    return {"error": result.get("error", "Failed to send RCS message.")}


@mcp.tool()
def send_rcs_message(
    api_key: str,
    phone: str,
    text: str,
    suggestions: Optional[list] = None,
) -> dict:
    """Send an RCS message with optional quick-reply suggestions.

    Automatically falls back to SMS when the recipient device is not RCS-capable.

    Args:
        api_key: Your Jina Connect API key.
        phone: Recipient phone in E.164 format (e.g. +919876543210).
        text: Plain text body of the message.
        suggestions: Optional list of quick-reply dicts, e.g.
            [{"type": "reply", "text": "Yes", "postbackData": "yes"}].
    """
    from rcs.models import RCSApp
    from rcs.services.message_sender import RCSMessageSender

    tenant, _ = resolve_tenant(api_key)

    rcs_app = RCSApp.objects.filter(tenant=tenant, is_active=True).first()
    if not rcs_app:
        return {"error": "No active RCS app configured for this tenant."}

    sender = RCSMessageSender(rcs_app)

    if suggestions:
        result = sender.send_keyboard(
            chat_id=str(phone),
            text=text,
            keyboard=suggestions,
        )
    else:
        result = sender.send_text(chat_id=str(phone), text=text)

    if result.get("success"):
        return {
            "message_id": result.get("message_id", ""),
            "status": "SENT",
            "phone": phone,
            "channel": "RCS",
            "fallback_used": result.get("fallback_used", False),
        }
    return {"error": result.get("error", "Failed to send RCS message.")}


@mcp.tool()
def check_rcs_capability(
    api_key: str,
    phone: str,
) -> dict:
    """Check whether a phone number is RCS-capable on Google RBM.

    Args:
        api_key: Your Jina Connect API key.
        phone: Phone number to check in E.164 format (e.g. +919876543210).
    """
    from rcs.models import RCSApp
    from rcs.providers import get_rcs_provider
    from rcs.services.capability_checker import RCSCapabilityChecker

    tenant, _ = resolve_tenant(api_key)

    rcs_app = RCSApp.objects.filter(tenant=tenant, is_active=True).first()
    if not rcs_app:
        return {"error": "No active RCS app configured for this tenant."}

    provider = get_rcs_provider(rcs_app)
    checker = RCSCapabilityChecker(provider)
    capability = checker.get_capability(str(phone))

    return {
        "phone": phone,
        "is_capable": capability is not None,
        "provider": rcs_app.provider,
        "features": capability.features if capability else [],
    }


@mcp.tool()
def get_rcs_message_status(
    api_key: str,
    message_id: str,
) -> dict:
    """Get the delivery status of an RCS outbound message.

    Args:
        api_key: Your Jina Connect API key.
        message_id: UUID returned by send_rcs_message or send_message (RCS).
    """
    from rcs.models import RCSOutboundMessage

    tenant, _ = resolve_tenant(api_key)

    try:
        msg = RCSOutboundMessage.objects.select_related("contact").get(
            id=message_id,
            rcs_app__tenant=tenant,
        )
    except RCSOutboundMessage.DoesNotExist:
        return {"error": f"RCS message {message_id} not found."}

    result = {
        "message_id": str(msg.id),
        "status": msg.status,
        "message_type": msg.message_type,
        "phone": str(msg.to_phone),
        "provider": msg.rcs_app.provider if msg.rcs_app else None,
        "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
        "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
        "read_at": msg.read_at.isoformat() if msg.read_at else None,
        "fallback_used": msg.fallback_sms_id is not None,
    }

    if hasattr(msg, "error_message") and msg.error_message:
        result["error_message"] = msg.error_message

    return result
