"""
MCP messaging tools — send_template, send_message, get_message_status, list_templates.
"""

from __future__ import annotations

from typing import Optional

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
) -> dict:
    """Send a plain text WhatsApp message to a phone number.

    Note: The recipient must have an active conversation window (24-hour rule).

    Args:
        api_key: Your Jina Connect API key.
        phone: Recipient phone number with country code.
        text: The message text to send.
    """
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
