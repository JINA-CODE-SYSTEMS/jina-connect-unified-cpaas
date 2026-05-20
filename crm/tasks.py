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

    # Push the "expires_at within REFRESH_AHEAD" predicate into the
    # queryset. v1 scanned every enabled non-generic-webhook connection
    # and discarded the not-yet-expiring ones in Python. (#201 second
    # review) Note: connections with NULL ``expires_at`` are skipped
    # — production refresh logic should set ``expires_at`` on every
    # successful refresh; a NULL there means "we don't know" which is
    # exactly when we DON'T want to spam the provider with refresh
    # calls on every beat.
    qs = (
        CrmConnection.objects.filter(enabled=True)
        .exclude(provider="generic_webhook")
        .filter(expires_at__isnull=False, expires_at__lt=cutoff)
    )

    refreshed = 0
    failed = 0
    for conn in qs.iterator():
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
    """Provider-dispatched refresh. Production fills in each branch.

    Splitting by provider here (rather than waiting until #198 lands
    live OAuth) means a future PR adds only an ``elif`` plus the HTTP
    body — no risk of one provider's refresh code accidentally
    running for another. (#201 third review style nit #5)
    """
    provider = connection.provider
    if provider == "hubspot":
        _refresh_hubspot(connection)
    elif provider == "salesforce":
        _refresh_salesforce(connection)
    elif provider == "generic_webhook":
        # Generic webhook connectors don't have refresh semantics —
        # the operator owns secret rotation out-of-band.
        return
    else:
        logger.warning(
            "[crm.refresh] unknown provider %s for connection=%s; skipping",
            provider,
            connection.id,
        )


def _refresh_hubspot(connection) -> None:
    """Stub. Production: POST https://api.hubapi.com/oauth/v1/token with
    grant_type=refresh_token + the stored refresh_token; update
    access_token + expires_at on success; raise on 401 so the outer
    loop can flag needs_reauth."""
    logger.info("[crm.refresh] STUB refresh HubSpot connection=%s", connection.id)


def _refresh_salesforce(connection) -> None:
    """Stub. Production: POST {instance_url}/services/oauth2/token with
    grant_type=refresh_token. If no refresh_token (legacy
    password-flow connections), raise so the outer loop sets
    needs_reauth and surfaces a re-auth prompt to the tenant."""
    logger.info("[crm.refresh] STUB refresh Salesforce connection=%s", connection.id)


__all__ = ["refresh_expiring_connections"]
