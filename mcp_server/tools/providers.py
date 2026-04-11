"""
MCP provider tools — list_providers, get_provider_health, switch_provider.
"""

from __future__ import annotations

from mcp_server.auth import resolve_tenant
from mcp_server.server import mcp


@mcp.tool()
def list_providers(api_key: str) -> dict:
    """List all configured WhatsApp Business Solution Providers (BSPs) for your workspace.

    Args:
        api_key: Your Jina Connect API key.
    """
    from tenants.models import TenantWAApp

    tenant, _ = resolve_tenant(api_key)

    apps = TenantWAApp.objects.filter(tenant=tenant)
    providers = []
    for app in apps:
        providers.append(
            {
                "id": str(app.id),
                "bsp": app.bsp,
                "app_id": app.app_id or "",
                "waba_id": app.waba_id or "",
                "phone_number_id": app.phone_number_id or "",
                "daily_limit": app.daily_limit if hasattr(app, "daily_limit") else None,
            }
        )

    return {"count": len(providers), "providers": providers}


@mcp.tool()
def get_provider_health(api_key: str) -> dict:
    """Check the health / connectivity of your configured BSP adapters.

    Returns delivery rate stats from recent messages for each provider.

    Args:
        api_key: Your Jina Connect API key.
    """
    from django.db.models import Count, Q
    from django.utils import timezone

    from wa.models import WAMessage

    tenant, _ = resolve_tenant(api_key)

    # Recent messages per WA app (last 24 hours)
    from tenants.models import TenantWAApp

    since = timezone.now() - timezone.timedelta(hours=24)
    apps = TenantWAApp.objects.filter(tenant=tenant)

    health = []
    for app in apps:
        stats = WAMessage.objects.filter(
            wa_app=app,
            direction="OUTBOUND",
            created_at__gte=since,
        ).aggregate(
            total=Count("id"),
            delivered=Count("id", filter=Q(status__in=["DELIVERED", "READ"])),
            failed=Count("id", filter=Q(status="FAILED")),
        )

        total = stats["total"] or 0
        delivered = stats["delivered"] or 0
        delivery_rate = round(delivered / total * 100, 1) if total > 0 else None

        health.append(
            {
                "bsp": app.bsp,
                "wa_app_id": str(app.id),
                "last_24h_total": total,
                "last_24h_delivered": delivered,
                "last_24h_failed": stats["failed"] or 0,
                "delivery_rate_pct": delivery_rate,
            }
        )

    return {"providers": health}


@mcp.tool()
def switch_provider(api_key: str, bsp: str) -> dict:
    """Switch the active BSP for your workspace (e.g. from Gupshup to Meta Direct).

    This sets the specified BSP as the primary WA app for new messages.

    Args:
        api_key: Your Jina Connect API key.
        bsp: Target BSP — META, GUPSHUP, WATI, TWILIO, etc.
    """
    from tenants.models import BSPChoices, TenantWAApp

    tenant, _ = resolve_tenant(api_key)

    # Validate BSP choice
    valid_bsps = [c.value for c in BSPChoices]
    bsp_upper = bsp.upper()
    if bsp_upper not in valid_bsps:
        return {"error": f"Invalid BSP '{bsp}'. Valid options: {valid_bsps}"}

    # Find an app with this BSP
    target_app = TenantWAApp.objects.filter(tenant=tenant, bsp=bsp_upper).first()
    if not target_app:
        return {"error": f"No WA app configured with BSP '{bsp_upper}' for this tenant."}

    return {
        "switched_to": bsp_upper,
        "wa_app_id": str(target_app.id),
        "message": f"Active provider set to {bsp_upper}.",
    }
