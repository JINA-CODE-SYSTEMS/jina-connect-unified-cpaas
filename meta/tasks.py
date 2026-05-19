"""Meta Business connection upkeep tasks (#201 review).

Meta system-user tokens are long-lived but can be revoked or have
implicit expiry. A daily Celery beat keeps them fresh and surfaces
the ``needs_reauth=True`` banner on connections we can't refresh.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# How frequently we proactively touch each token. System-user tokens
# don't have a fixed lifetime but stale ones occasionally 190 — a
# weekly probe smokes that out before tenants hit the wall.
REFRESH_INTERVAL = timedelta(days=7)


@shared_task
def refresh_expiring_connections() -> dict:
    """Daily beat. Probe each connection that hasn't been refreshed
    in the last week. On Meta auth errors, flip ``needs_reauth=True``
    so the campaign-wizard banner appears.

    The actual Meta API call is stubbed until #190 approval lands;
    once it does, replace :func:`_probe` with a real call (e.g.
    ``GET /me`` against the connection's token).
    """
    from django.db.models import Q

    from meta.models import MetaBusinessConnection

    threshold = timezone.now() - REFRESH_INTERVAL
    # Push the "never refreshed OR refreshed before threshold" predicate
    # into the queryset. v1 had ``.filter(**{})`` (no-op) followed by a
    # Python ``continue`` — every non-revoked connection was loaded and
    # then most were discarded in-process. The Q expression keeps the
    # ``refreshed_at`` index path available. (#201 second review)
    qs = MetaBusinessConnection.objects.filter(
        revoked_at__isnull=True,
        needs_reauth=False,
    ).filter(Q(refreshed_at__isnull=True) | Q(refreshed_at__lt=threshold))

    probed = 0
    flipped_reauth = 0
    for conn in qs.iterator():
        probed += 1
        try:
            _probe(conn)
            conn.refreshed_at = timezone.now()
            conn.save(update_fields=["refreshed_at", "updated_at"])
        except _MetaAuthExpiredLocal:
            conn.needs_reauth = True
            conn.save(update_fields=["needs_reauth", "updated_at"])
            flipped_reauth += 1
            logger.warning("[meta.refresh] connection %s flipped needs_reauth=True", conn.id)
        except Exception as exc:  # noqa: BLE001 — probe should never crash the batch
            logger.warning("[meta.refresh] probe failed for %s: %s", conn.id, exc)

    result = {"probed": probed, "flipped_reauth": flipped_reauth}
    logger.info("[meta.refresh] %s", result)
    return result


class _MetaAuthExpiredLocal(Exception):
    """Locally raised when the stub probe wants to simulate a 190."""


def _probe(connection) -> None:
    """Stub probe. Production replaces with::

        client = MetaApiClient(connection)
        client._call('GET', '/me')  # raises MetaAuthExpired on 190

    Today the stub no-ops so the worker loop logic can be exercised
    in tests without external creds.
    """
    logger.info("[meta.refresh] STUB probe connection=%s", connection.id)


__all__ = ["refresh_expiring_connections"]
