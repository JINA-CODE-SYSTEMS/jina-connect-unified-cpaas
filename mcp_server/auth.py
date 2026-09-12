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
    # Resolved through the model, not a lookup on the key column: the key is
    # stored as a digest now, so matching means hashing what was presented
    # (#301). resolve() also skips revoked keys, which a raw get() did not.
    access_key = TenantAccessKey.resolve(api_key)
    if access_key is None:
        raise ValueError("Invalid API key. Check your Jina Connect access key.")

    tenant = access_key.tenant

    # Grab the first WA app for this tenant (if any)
    wa_app = TenantWAApp.objects.filter(tenant=tenant).first()

    return tenant, wa_app
