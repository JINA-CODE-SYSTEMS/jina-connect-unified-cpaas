"""B4 (#184): webhook reachability probes.

Per-provider answer to "is my webhook actually being hit?". The first
iteration is passive only — we compute the last time each webhook URL
for a given ``VoiceProviderConfig`` received an event by looking at
``VoiceCallEvent.occurred_at`` joined back to the config. That answers
the day-to-day question "my webhook hasn't fired in a week, is it
broken?" without any provider-side coordination.

The endpoint shape is designed so the active-probe path (asking the
provider to fire a test webhook against our URL via a correlation ID
and short-lived Redis flag) can be added later without changing the
response schema. Until then, every provider returns ``probe_type:
"passive"``.

Schema:

```
{
  "config_id": "<uuid>",
  "provider": "twilio",
  "probe_type": "passive",
  "results": [
    {
      "label": "call-status",
      "url": "https://example.com/voice/v1/webhooks/twilio/<uuid>/call-status/",
      "status": "passive_recent" | "passive_stale" | "passive_never",
      "last_received_at": "2026-05-18T..." | null,
      "sample_call_id": "<uuid>" | null,
      "inferred_from": "any_event" | "event_type"
    },
    ...
  ]
}
```

Status thresholds:

  * ``passive_recent`` — last event within the last 15 minutes
  * ``passive_stale``  — last event older than 15 minutes
  * ``passive_never``  — no event ever for this URL on this config

``inferred_from`` ("any_event" vs "event_type") flags how the freshness
was derived. For providers with a single webhook URL (Telnyx, Exotel's
``status``), the row covers all events so we tag it ``any_event`` — the
UI can render a "best-effort" hint instead of pretending the freshness
is per-route. (#185 review)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from urllib.parse import urljoin

from django.conf import settings
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from voice.constants import VoiceProvider
from voice.models import VoiceCallEvent, VoiceProviderConfig

PASSIVE_RECENT_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True)
class WebhookRoute:
    """One named webhook entry-point for a provider.

    ``label`` is a stable identifier the frontend renders next to each
    row (e.g. "call-status", "answer"). ``url_name`` is the Django URL
    name in ``voice/urls.py``.
    """

    label: str
    url_name: str


# Each provider exposes a fixed set of webhook URLs; SIP is intentionally
# omitted because it has no HTTP webhooks (events arrive via the ARI
# WebSocket consumer, not an HTTP callback).
WEBHOOK_ROUTES_BY_PROVIDER: dict[str, list[WebhookRoute]] = {
    VoiceProvider.TWILIO: [
        WebhookRoute("call-status", "voice:twilio-call-status"),
        WebhookRoute("answer", "voice:twilio-answer"),
        WebhookRoute("gather", "voice:twilio-gather"),
        WebhookRoute("recording-status", "voice:twilio-recording-status"),
    ],
    VoiceProvider.PLIVO: [
        WebhookRoute("call-status", "voice:plivo-call-status"),
        WebhookRoute("answer", "voice:plivo-answer"),
        WebhookRoute("recording", "voice:plivo-recording"),
    ],
    VoiceProvider.VONAGE: [
        WebhookRoute("event", "voice:vonage-event"),
        WebhookRoute("answer", "voice:vonage-answer"),
    ],
    VoiceProvider.TELNYX: [
        WebhookRoute("event", "voice:telnyx-event"),
    ],
    VoiceProvider.EXOTEL: [
        WebhookRoute("status", "voice:exotel-status"),
        WebhookRoute("passthru", "voice:exotel-passthru"),
    ],
}


# Each route maps to a list of canonical ``CallEventType`` values it
# represents. An empty list means "any event" — the route covers a
# single webhook URL that receives all events, so its freshness is
# inferred from the latest event of any type. ``inferred_from`` in the
# response surfaces this distinction. Filled in lazily on first probe
# to avoid importing ``CallEventType`` at module load.
_ROUTE_EVENT_TYPES: dict[str, list[str]] = {}


def _route_event_types() -> dict[str, list[str]]:
    global _ROUTE_EVENT_TYPES
    if _ROUTE_EVENT_TYPES:
        return _ROUTE_EVENT_TYPES
    from voice.constants import CallEventType

    _ROUTE_EVENT_TYPES = {
        "call-status": [],  # any event
        "event": [],
        "status": [],
        "answer": [CallEventType.INITIATED, CallEventType.RINGING],
        "gather": [CallEventType.DTMF, CallEventType.SPEECH],
        "recording-status": [
            CallEventType.RECORDING_STARTED,
            CallEventType.RECORDING_COMPLETED,
        ],
        "recording": [
            CallEventType.RECORDING_STARTED,
            CallEventType.RECORDING_COMPLETED,
        ],
        "passthru": [
            CallEventType.RECORDING_STARTED,
            CallEventType.RECORDING_COMPLETED,
        ],
    }
    return _ROUTE_EVENT_TYPES


def _absolute_url(request, url_name: str, config_uuid: str) -> str:
    """Build the public URL for a webhook route.

    Prefers ``request.build_absolute_uri()`` (uses the inbound Host
    header — always reflects how the caller reached us). Falls back to
    ``PUBLIC_BASE_URL`` when no request is available (e.g. called from
    a Celery task). The legacy ``example.invalid`` fallback was loud
    on purpose but confusing in operator output — drop it for an empty
    base path instead. (#185 review)
    """
    path = reverse(url_name, kwargs={"config_uuid": config_uuid})
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, "PUBLIC_BASE_URL", "") or ""
    return urljoin(base.rstrip("/") + "/", path.lstrip("/")) if base else path


def _routes_for(provider: str) -> list[WebhookRoute]:
    return WEBHOOK_ROUTES_BY_PROVIDER.get(provider, [])


def _classify(last_received_at) -> str:
    if last_received_at is None:
        return "passive_never"
    if timezone.now() - last_received_at <= PASSIVE_RECENT_WINDOW:
        return "passive_recent"
    return "passive_stale"


def probe_config(config: VoiceProviderConfig, request=None) -> dict:
    """Run the passive reachability probe for *config*.

    Returns the response dict the API surface emits verbatim. Issues
    one aggregate query that finds ``(event_type, max(occurred_at))``
    per type, then projects per-route freshness in Python — replacing
    the previous N+1 path that ran one ``ORDER BY occurred_at DESC
    LIMIT 1`` per route. Pairs with the composite index on
    ``VoiceCallEvent`` added in migration ``0006``. (#185 review)
    """
    routes = _routes_for(config.provider)
    config_uuid = str(config.id)

    # Single query: for this config, find latest occurrence of each
    # event type, plus a sample call id for that latest occurrence.
    # We can't pull sample_call_id in the GROUP BY without a window
    # function, so do a second cheap query keyed by the (event_type,
    # occurred_at) tuples we found.
    per_type_max = dict(
        VoiceCallEvent.objects.filter(call__provider_config=config)
        .values_list("event_type")
        .annotate(latest=Max("occurred_at"))
        .values_list("event_type", "latest")
    )
    if per_type_max:
        latest_overall = max(per_type_max.values())
        sample_for_latest = (
            VoiceCallEvent.objects.filter(
                call__provider_config=config,
                occurred_at=latest_overall,
            )
            .values_list("event_type", "call_id")
            .first()
        )
    else:
        latest_overall = None
        sample_for_latest = None

    route_event_types = _route_event_types()
    results: list[dict] = []
    for route in routes:
        filter_types = route_event_types.get(route.label, [])
        if filter_types:
            inferred_from = "event_type"
            last_at = None
            sample_call_id: Optional[str] = None
            for et in filter_types:
                t = per_type_max.get(et)
                if t and (last_at is None or t > last_at):
                    last_at = t
            if last_at is not None:
                row = (
                    VoiceCallEvent.objects.filter(
                        call__provider_config=config,
                        event_type__in=filter_types,
                        occurred_at=last_at,
                    )
                    .values_list("call_id")
                    .first()
                )
                if row:
                    sample_call_id = str(row[0])
        else:
            inferred_from = "any_event"
            last_at = latest_overall
            sample_call_id = str(sample_for_latest[1]) if sample_for_latest else None

        results.append(
            {
                "label": route.label,
                "url": _absolute_url(request, route.url_name, config_uuid),
                "status": _classify(last_at),
                "last_received_at": last_at.isoformat() if last_at else None,
                "sample_call_id": sample_call_id,
                "inferred_from": inferred_from,
            }
        )

    return {
        "config_id": config_uuid,
        "provider": config.provider,
        "probe_type": "passive",
        "results": results,
    }


__all__ = [
    "PASSIVE_RECENT_WINDOW",
    "WEBHOOK_ROUTES_BY_PROVIDER",
    "WebhookRoute",
    "probe_config",
]
