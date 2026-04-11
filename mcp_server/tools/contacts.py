"""
MCP contact tools — search_contacts, create_contact, update_contact.
"""

from __future__ import annotations

from typing import Optional

from mcp_server.auth import resolve_tenant
from mcp_server.server import mcp


@mcp.tool()
def search_contacts(
    api_key: str,
    query: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """Search contacts in your Jina Connect workspace.

    Args:
        api_key: Your Jina Connect API key.
        query: Search by phone number, first name, or last name.
        tag: Filter by tag.
        limit: Max results to return (default 20, max 50).
    """
    from django.db.models import Q

    from contacts.models import TenantContact

    tenant, _ = resolve_tenant(api_key)
    qs = TenantContact.objects.filter(tenant=tenant)

    if query:
        qs = qs.filter(
            Q(phone__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )

    if tag:
        qs = qs.filter(tag__icontains=tag)

    limit = min(limit, 50)
    contacts = []
    for c in qs.order_by("-created_at")[:limit]:
        contacts.append(
            {
                "id": str(c.id),
                "phone": str(c.phone),
                "first_name": c.first_name,
                "last_name": c.last_name or "",
                "tag": c.tag or "",
                "source": c.source,
                "status": c.status,
                "lead_status": c.lead_status or "",
            }
        )

    return {"count": len(contacts), "contacts": contacts}


@mcp.tool()
def create_contact(
    api_key: str,
    phone: str,
    first_name: str,
    last_name: Optional[str] = None,
    tag: Optional[str] = None,
) -> dict:
    """Create a new contact in your Jina Connect workspace.

    Args:
        api_key: Your Jina Connect API key.
        phone: Phone number with country code (e.g. +919876543210).
        first_name: Contact's first name.
        last_name: Contact's last name (optional).
        tag: Tag to assign (optional).
    """
    from contacts.models import TenantContact

    tenant, _ = resolve_tenant(api_key)

    contact, created = TenantContact.objects.get_or_create(
        tenant=tenant,
        phone=phone,
        defaults={
            "first_name": first_name,
            "last_name": last_name or "",
            "tag": tag or "",
            "source": "MANUAL",
        },
    )

    if not created:
        return {
            "id": str(contact.id),
            "phone": str(contact.phone),
            "created": False,
            "message": "Contact with this phone number already exists.",
        }

    return {
        "id": str(contact.id),
        "phone": str(contact.phone),
        "first_name": contact.first_name,
        "created": True,
    }


@mcp.tool()
def update_contact(
    api_key: str,
    phone: str,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    tag: Optional[str] = None,
) -> dict:
    """Update an existing contact by phone number.

    Args:
        api_key: Your Jina Connect API key.
        phone: Phone number of the contact to update.
        first_name: New first name (optional).
        last_name: New last name (optional).
        tag: New tag (optional).
    """
    from contacts.models import TenantContact

    tenant, _ = resolve_tenant(api_key)

    try:
        contact = TenantContact.objects.get(tenant=tenant, phone=phone)
    except TenantContact.DoesNotExist:
        return {"error": f"Contact with phone {phone} not found."}

    if first_name is not None:
        contact.first_name = first_name
    if last_name is not None:
        contact.last_name = last_name
    if tag is not None:
        contact.tag = tag

    contact.save()

    return {
        "id": str(contact.id),
        "phone": str(contact.phone),
        "first_name": contact.first_name,
        "last_name": contact.last_name or "",
        "tag": contact.tag or "",
        "updated": True,
    }
