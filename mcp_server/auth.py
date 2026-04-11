"""
Tenant resolution for MCP tool calls.

Every tool receives an `api_key` parameter. This module resolves it to
a (Tenant, TenantWAApp) pair so tools operate in the correct tenant context.
"""

from __future__ import annotations

from typing import Optional, Tuple

from tenants.models import Tenant, TenantAccessKey, TenantWAApp


def resolve_tenant(api_key: str) -> Tuple[Tenant, Optional[TenantWAApp]]:
    """
    Resolve an API key to its Tenant and (optionally) the first active WA app.

    Raises ValueError with a human-readable message on failure.
    """
    try:
        access_key = TenantAccessKey.objects.select_related("tenant").get(key=api_key)
    except TenantAccessKey.DoesNotExist:
        raise ValueError("Invalid API key. Check your Jina Connect access key.")

    tenant = access_key.tenant

    # Grab the first WA app for this tenant (if any)
    wa_app = TenantWAApp.objects.filter(tenant=tenant).first()

    return tenant, wa_app
