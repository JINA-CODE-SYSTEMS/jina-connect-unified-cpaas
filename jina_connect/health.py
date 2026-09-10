"""Health probe for external uptime monitoring.

This endpoint's output is what an SLA availability figure is computed from
(partner agreement Cl. 5.4), so two properties matter more than thoroughness:

Deliberately shallow. It checks only the dependencies whose absence makes the
product genuinely unusable — the database and Redis. A probe that reaches
further turns a slow non-critical dependency into a reported outage, and you
would be paying SLA credits for a degraded queue no customer noticed.

Never cached. A proxy or CDN caching a 200 would show "up" while the service
is down, which is the one failure mode an uptime probe must not have.

The response body deliberately carries no exception detail: it is unauthenticated,
so failures are logged server-side rather than described to the caller.
"""

import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

# Bounded so a hung dependency fails fast rather than holding the probe open
# until the monitor's own timeout fires.
REDIS_TIMEOUT_SECONDS = 1


def _check_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _check_redis():
    import redis

    client = redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )
    try:
        client.ping()
    finally:
        client.close()


# HEAD is allowed as well as GET: plenty of uptime monitors probe with
# HEAD by default, and rejecting it would report a false outage on the
# very SLA figure this endpoint exists to measure.
@require_http_methods(["GET", "HEAD"])
@never_cache
def healthz(request):
    """Return 200 when every critical dependency responds, 503 otherwise."""
    # Resolved per call rather than bound at import, so the checks stay
    # patchable in tests.
    checks = (
        ("database", _check_database),
        ("redis", _check_redis),
    )

    results = {}
    healthy = True

    for name, check in checks:
        try:
            check()
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - any failure is an unhealthy dependency
            logger.warning("healthz: %s check failed: %s", name, exc)
            results[name] = "error"
            healthy = False

    return JsonResponse(
        {"status": "ok" if healthy else "error", "checks": results},
        status=200 if healthy else 503,
    )
