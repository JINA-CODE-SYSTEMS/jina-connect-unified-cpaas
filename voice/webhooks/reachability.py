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
      "sample_call_id": "<uuid>" | null
    },
    ...
  ]
}
```

Status thresholds (configurable via ``settings``):

  * ``passive_recent`` — last event within the last 15 minutes
  * ``passive_stale``  — last event older than 15 minutes
  * ``passive_never``  — no event ever for this URL on this config
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from urllib.parse import urljoin

from django.conf import settings
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


def _public_base_url() -> str:
    """Best-effort guess at the public origin for webhook URLs.

    Falls back to a placeholder host so the URL is still parseable when
    the project hasn't set ``PUBLIC_BASE_URL`` — the frontend cares
    about the *path*, not the host, in the passive case.
    """
    return getattr(settings, "PUBLIC_BASE_URL", "") or "https://example.invalid/"


def _absolute_url(url_name: str, config_uuid: str) -> str:
    path = reverse(url_name, kwargs={"config_uuid": config_uuid})
    return urljoin(_public_base_url(), path)


def _routes_for(provider: str) -> list[WebhookRoute]:
    return WEBHOOK_ROUTES_BY_PROVIDER.get(provider, [])


def _classify(last_received_at) -> str:
    if last_received_at is None:
        return "passive_never"
    if timezone.now() - last_received_at <= PASSIVE_RECENT_WINDOW:
        return "passive_recent"
    return "passive_stale"


def _last_event_for_route(config: VoiceProviderConfig, label: str) -> tuple[Optional[object], Optional[str]]:
    """Return ``(occurred_at, sample_call_id)`` for the most recent event
    on a config that we can attribute to *label*.

    The route-label mapping to ``VoiceCallEvent.event_type`` is coarse —
    we do not record which webhook URL fired which event, only the
    canonical event type. For the passive probe this is good enough:

      * "call-status" / "event" / "status" route → any non-recording event
      * "answer" route → INITIATED or RINGING events (the answer hook
        is invoked at call setup)
      * "gather" route → DTMF_RECEIVED / SPEECH_RECEIVED events
      * "recording-status" / "recording" / "passthru" route → RECORDING_*
        events

    For providers like Telnyx that have a single event webhook, all
    events count toward freshness — exactly what the user wants.
    """
    from voice.constants import CallEventType

    label_filters: dict[str, list[str]] = {
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
    events = VoiceCallEvent.objects.filter(call__provider_config=config).order_by("-occurred_at")
    type_filter = label_filters.get(label)
    if type_filter:
        events = events.filter(event_type__in=type_filter)
    last = events.values("occurred_at", "call_id").first()
    if not last:
        return None, None
    return last["occurred_at"], str(last["call_id"])


def probe_config(config: VoiceProviderConfig) -> dict:
    """Run the passive reachability probe for *config*.

    Returns the response dict the API surface emits verbatim.
    """
    routes = _routes_for(config.provider)
    results: list[dict] = []
    config_uuid = str(config.id)
    for route in routes:
        last_at, sample_call_id = _last_event_for_route(config, route.label)
        results.append(
            {
                "label": route.label,
                "url": _absolute_url(route.url_name, config_uuid),
                "status": _classify(last_at),
                "last_received_at": last_at.isoformat() if last_at else None,
                "sample_call_id": sample_call_id,
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
