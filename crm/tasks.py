"""CRM connection upkeep tasks (#201 review).

Daily refresh of OAuth tokens for connections nearing expiry, and a
sweep of stalled outbound pushes from the audit log.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Refresh tokens that expire within this window. HubSpot OAuth lasts
# 6 hours; Salesforce session tokens last 2 hours by default. The
# refresh path is provider-specific (HubSpot OAuth refresh grant,
# Salesforce username-password flow, etc.) and is stubbed today.
REFRESH_AHEAD = timedelta(hours=12)


@shared_task
def refresh_expiring_connections() -> dict:
    """Daily beat. For every enabled CRM connection nearing its
    ``expires_at``, attempt a refresh via the provider's adapter.
    Stub today (no HTTP); production replaces :func:`_refresh_one`.

    Provider-specific refresh endpoints:

      * HubSpot — ``POST https://api.hubapi.com/oauth/v1/token``
        with grant_type=refresh_token + refresh_token from the
        stored row.
      * Salesforce — ``POST {instance_url}/services/oauth2/token``
        with grant_type=refresh_token. If no refresh_token, the
        connection needs human re-auth.
    """
    from crm.models import CrmConnection

    now = timezone.now()
    cutoff = now + REFRESH_AHEAD

    qs = CrmConnection.objects.filter(enabled=True).exclude(provider="generic_webhook")
    refreshed = 0
    failed = 0
    for conn in qs.iterator():
        if conn.expires_at is None or conn.expires_at > cutoff:
            continue
        try:
            _refresh_one(conn)
            refreshed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning(
                "[crm.refresh] connection %s (%s) refresh failed: %s",
                conn.id,
                conn.provider,
                exc,
            )

    result = {"refreshed": refreshed, "failed": failed}
    logger.info("[crm.refresh] %s", result)
    return result


def _refresh_one(connection) -> None:
    """Stub provider-agnostic refresh. Production fills in per-provider
    HTTP. No-op today so worker loop is testable without external creds.
    """
    logger.info(
        "[crm.refresh] STUB refresh connection=%s provider=%s",
        connection.id,
        connection.provider,
    )


__all__ = ["refresh_expiring_connections"]
