"""Read synthetic-monitoring results out of Grafana Cloud's Prometheus API.

Grafana Cloud Synthetic Monitoring writes a ``probe_success`` sample (1 or 0)
for every check execution, from every probe location the check runs from. This
module turns that raw stream into one verdict per check interval, which is the
unit ``DailyAvailability`` records.

Three decisions are made here rather than left implicit:

**A check interval is judged on the majority of probe locations, not on any
one of them.** A single location failing is far more often a fault on that
location's own network path than an outage of the platform, and counting it as
downtime would make the contractual figure a measure of the internet rather
than of us. Requiring a majority also means a two-location check needs both to
fail, which is the conservative reading. The threshold is settable, and the
report states the basis.

**Windows tile the day exactly.** Each evaluated point averages the samples in
the interval that precedes it, so consecutive points cover the day end to end
with no overlap and no gap. The alternative — asking for an instant value every
two minutes — lets Prometheus carry a stale sample forward for up to five
minutes, which quietly converts a monitoring gap into reported uptime.

**A gap in the data produces fewer points, never a healthy one.** If the
monitor was down, those windows have no samples and Prometheus returns nothing
for them. The caller sees a short day and can flag it, which is the whole
reason the coverage count exists.

The aggregation is grouped by check rather than collapsed to a single value,
so a selector broad enough to match two checks comes back as two series and is
refused. Collapsing would have averaged the API and the UI into one number and
reported it as either.
"""

import logging
from datetime import datetime
from urllib.parse import urljoin

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Grafana Cloud rejects a range query asking for more than 11,000 points. A day
# at the default two-minute interval is 720, so this only ever trips on a
# misconfigured interval — worth catching before the request rather than
# reading a 400 back.
MAX_POINTS = 11000

DEFAULT_TIMEOUT = 30


class GrafanaNotConfigured(Exception):
    """Credentials or probe selectors are missing from the deployment's env."""


class GrafanaUnavailable(Exception):
    """Grafana answered with an error, or did not answer."""


def _query_url() -> str:
    """Full range-query endpoint, derived from the configured base.

    Grafana Cloud's Prometheus base already ends in ``/api/prom``, giving the
    doubled-looking ``/api/prom/api/v1/query_range``. That trips people up, so
    a base that already carries the ``/api/v1`` suffix is accepted too.
    """
    base = (settings.GRAFANA_PROM_URL or "").strip().rstrip("/")
    if not base:
        raise GrafanaNotConfigured(
            "GRAFANA_PROM_URL is not set. Point it at the deployment's Grafana Cloud "
            "Prometheus base, e.g. https://prometheus-prod-01-eu-west-0.grafana.net/api/prom"
        )
    if not base.endswith("/api/v1"):
        base = f"{base}/api/v1"
    return urljoin(f"{base}/", "query_range")


def _credentials() -> tuple[str, str]:
    user = (settings.GRAFANA_PROM_USER or "").strip()
    token = (settings.GRAFANA_PROM_TOKEN or "").strip()
    if not user or not token:
        raise GrafanaNotConfigured(
            "GRAFANA_PROM_USER (the Prometheus instance ID) and GRAFANA_PROM_TOKEN "
            "(a Grafana Cloud access policy token with metrics:read) must both be set."
        )
    return user, token


def probe_selector(target: str) -> str:
    """PromQL label selector identifying one probe target's check."""
    selector = (settings.AVAILABILITY_PROBE_SELECTORS.get(target) or "").strip()
    if not selector:
        raise GrafanaNotConfigured(
            f"No probe selector configured for target {target!r}. Set "
            f"AVAILABILITY_PROBE_SELECTOR_{target.upper()} to the label selector of the "
            f'synthetic check, e.g. job="jina-connect-api".'
        )
    return selector


def query_range(query: str, start: datetime, end: datetime, step: int) -> list[tuple[float, float]]:
    """Run a Prometheus range query, returning ``(timestamp, value)`` pairs.

    Raises ``GrafanaNotConfigured`` when the deployment has no credentials, and
    ``GrafanaUnavailable`` for anything that goes wrong at or beyond the wire.
    """
    if step <= 0:
        raise ValueError("step must be positive")

    points = int((end - start).total_seconds() // step) + 1
    if points > MAX_POINTS:
        raise ValueError(
            f"A {step}s step over this range asks for {points} points; Prometheus allows {MAX_POINTS}. "
            f"Check AVAILABILITY_PROBE_INTERVAL_SECONDS."
        )

    url = _query_url()
    auth = _credentials()
    params = {
        "query": query,
        "start": start.timestamp(),
        "end": end.timestamp(),
        "step": step,
    }

    try:
        response = requests.get(url, params=params, auth=auth, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise GrafanaUnavailable(f"Could not reach Grafana at {url}: {exc}") from exc

    if response.status_code in (401, 403):
        raise GrafanaNotConfigured(
            f"Grafana rejected the credentials ({response.status_code}). Check GRAFANA_PROM_USER "
            f"is the numeric instance ID and that the token still carries metrics:read."
        )
    if response.status_code != 200:
        raise GrafanaUnavailable(f"Grafana returned {response.status_code}: {response.text[:400]}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GrafanaUnavailable(f"Grafana returned a non-JSON body: {response.text[:400]}") from exc

    if payload.get("status") != "success":
        raise GrafanaUnavailable(f"Grafana reported {payload.get('status')}: {payload.get('error', 'no detail')}")

    result = payload.get("data", {}).get("result", [])
    if not result:
        return []
    # The query aggregates to a single series; if a selector accidentally
    # matches several checks, say so rather than silently reading the first.
    if len(result) > 1:
        raise GrafanaUnavailable(
            f"Expected one aggregated series, got {len(result)}. The probe selector matches more than "
            f"one check, so the figure would cover the wrong thing."
        )

    return [(float(ts), float(value)) for ts, value in result[0].get("values", [])]


def fetch_probe_results(target: str, start: datetime, end: datetime, interval: int) -> tuple[int, int]:
    """Count check intervals and failures for one target over one period.

    ``start`` is exclusive and ``end`` inclusive: the point at ``start + interval``
    covers the first window of the period. Returns ``(total_checks, failed_checks)``.
    """
    selector = probe_selector(target)
    # Grouped by check rather than collapsed to one number. Averaging across
    # probe locations is the point; averaging across two different checks
    # would produce one meaningless figure and no error. job and instance
    # identify the check and probe identifies the location, so grouping on
    # the first two collapses locations and leaves a selector that matches
    # more than one check returning more than one series — which
    # ``query_range`` refuses.
    query = f"avg by (job, instance) (avg_over_time(probe_success{{{selector}}}[{interval}s]))"

    # Absolute arithmetic, not wall-clock: adding a timedelta to a zoneinfo-aware
    # datetime shifts the wall clock, which lands an hour out across a DST change.
    first_point = datetime.fromtimestamp(start.timestamp() + interval, tz=start.tzinfo)
    points = query_range(query, first_point, end, interval)

    threshold = float(settings.AVAILABILITY_FAILURE_THRESHOLD)
    failed = sum(1 for _, value in points if value < threshold)

    logger.info(
        "Grafana reported %d intervals for target %s between %s and %s, %d failed",
        len(points),
        target,
        start.isoformat(),
        end.isoformat(),
        failed,
    )
    return len(points), failed
