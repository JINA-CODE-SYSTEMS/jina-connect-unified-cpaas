"""
MCP campaign tools — create_broadcast, get_broadcast_status, list_broadcasts.
"""

from __future__ import annotations

from typing import List, Optional

from mcp_server.auth import resolve_tenant
from mcp_server.server import mcp


@mcp.tool()
def create_broadcast(
    api_key: str,
    name: str,
    template_name: str,
    phone_numbers: List[str],
    language_code: str = "en",
) -> dict:
    """Create a WhatsApp broadcast campaign to multiple recipients.

    Args:
        api_key: Your Jina Connect API key.
        name: Campaign name for tracking.
        template_name: The element_name of an APPROVED template.
        phone_numbers: List of recipient phone numbers with country codes.
        language_code: Template language code (default: "en").
    """
    from broadcast.models import Broadcast
    from contacts.models import TenantContact
    from wa.models import WATemplate

    tenant, wa_app = resolve_tenant(api_key)
    if not wa_app:
        return {"error": "No WhatsApp app configured for this tenant."}

    # Validate template
    try:
        template = WATemplate.objects.get(
            wa_app=wa_app,
            element_name=template_name,
            language_code=language_code,
            status="APPROVED",
        )
    except WATemplate.DoesNotExist:
        return {"error": f"Template '{template_name}' not found or not approved."}

    # Resolve contacts
    contacts = []
    for phone in phone_numbers:
        contact, _ = TenantContact.objects.get_or_create(
            tenant=tenant,
            phone=phone,
            defaults={"first_name": phone, "source": "WHATSAPP"},
        )
        contacts.append(contact)

    if not contacts:
        return {"error": "No valid phone numbers provided."}

    # Check if template has a TemplateNumber
    template_number = None
    if hasattr(template, "template_numbers"):
        template_number = template.template_numbers.first()

    broadcast = Broadcast.objects.create(
        tenant=tenant,
        name=name,
        platform="WHATSAPP",
        status="DRAFT",
        template_number=template_number,
    )
    broadcast.recipients.set(contacts)

    return {
        "broadcast_id": str(broadcast.id),
        "name": name,
        "status": broadcast.status,
        "recipient_count": len(contacts),
        "template": template_name,
    }


@mcp.tool()
def get_broadcast_status(api_key: str, broadcast_id: str) -> dict:
    """Get the status and delivery stats of a broadcast campaign.

    Args:
        api_key: Your Jina Connect API key.
        broadcast_id: The broadcast UUID.
    """
    from broadcast.models import Broadcast

    tenant, _ = resolve_tenant(api_key)

    try:
        bc = Broadcast.objects.get(id=broadcast_id, tenant=tenant)
    except Broadcast.DoesNotExist:
        return {"error": f"Broadcast {broadcast_id} not found."}

    return {
        "broadcast_id": str(bc.id),
        "name": bc.name,
        "status": bc.status,
        "platform": bc.platform,
        "recipient_count": bc.recipients.count(),
        "scheduled_time": bc.scheduled_time.isoformat() if bc.scheduled_time else None,
        "created_at": bc.created_at.isoformat() if hasattr(bc, "created_at") else None,
    }


@mcp.tool()
def list_broadcasts(
    api_key: str,
    status: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List broadcast campaigns for your workspace.

    Args:
        api_key: Your Jina Connect API key.
        status: Optional filter — DRAFT, SCHEDULED, QUEUED, SENDING, SENT, PARTIALLY_SENT, FAILED, CANCELLED.
        limit: Max results (default 20, max 50).
    """
    from broadcast.models import Broadcast

    tenant, _ = resolve_tenant(api_key)
    qs = Broadcast.objects.filter(tenant=tenant).order_by("-created_at")

    if status:
        qs = qs.filter(status=status.upper())

    limit = min(limit, 50)
    broadcasts = []
    for bc in qs[:limit]:
        broadcasts.append(
            {
                "id": str(bc.id),
                "name": bc.name,
                "status": bc.status,
                "platform": bc.platform,
                "recipient_count": bc.recipients.count(),
                "scheduled_time": bc.scheduled_time.isoformat() if bc.scheduled_time else None,
            }
        )

    return {"count": len(broadcasts), "broadcasts": broadcasts}
