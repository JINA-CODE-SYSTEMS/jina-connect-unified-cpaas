"""Base classes for the chat_flow event-trigger subsystem (#188).

The substrate is intentionally small:

  * :class:`TriggerEvent` is a frozen dataclass — the normalised
    cross-channel payload a webhook processor emits after persisting
    an inbound row.

  * :class:`BaseTrigger` is the ABC every concrete trigger type
    subclasses. Subclasses declare:

      - ``type_name`` (set by ``@register_trigger``)
      - ``config_model`` (a Pydantic model; validates the per-flow
        config payload at save time)
      - ``matches(event, config) -> bool`` (pure function; never
        writes to the DB, never raises)

Idempotency helpers live here too so trigger implementations can stay
unaware of Redis. The dispatcher claims a SETNX lock keyed on
``(tenant, channel, inbound_row_id)`` before iterating flows; replays
of the same webhook are a no-op.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

Channel = Literal["wa", "sms", "voice", "rcs", "telegram"]


@dataclass(frozen=True)
class TriggerEvent:
    """A normalised inbound event that may match one or more triggers.

    Channel-specific fields go into ``extra`` so the trigger subsystem
    stays channel-agnostic. Triggers that read channel-specific data
    (e.g. CTWA ``referral_source_id`` on a WA message) read from
    ``extra`` by an agreed key — see ``triggers/README.md``.

    ``inbound_row_id`` is a string because not every inbound model
    uses integer PKs (voice uses UUIDs, others use BIGSERIAL). The
    ``inbound_row_model`` field disambiguates the source so support
    can correlate a dispatched session back to its triggering event.
    """

    tenant_id: int
    channel: Channel
    contact_id: int
    inbound_row_id: str
    inbound_row_model: str
    body_text: str | None
    received_at: str  # ISO8601; used inside the idempotency key
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Enforce ``body_text``'s annotation, which the dataclass does not.

        #270 shipped a ``dict`` here for every WhatsApp inbound. Nothing
        objected: a non-empty dict is truthy, so it cleared the keyword
        trigger's own empty-check, and then either raised ``AttributeError``
        inside a deliberately broad handler or — with ``case_sensitive`` set —
        quietly tested membership against the dict's *keys* and returned
        ``False``. Either way the flow never spawned and nothing surfaced.

        Emitters already wrap ``emit()`` in try/except so ingestion cannot
        break, which means raising here converts a silent non-match into one
        logged error naming the offending type, at the site that built it.
        """
        if self.body_text is not None and not isinstance(self.body_text, str):
            raise TypeError(f"TriggerEvent.body_text must be str | None, got {type(self.body_text).__name__}")


class BaseTrigger:
    """Abstract base for every registered trigger type.

    Subclasses MUST set :attr:`config_model` (a Pydantic ``BaseModel``
    subclass) and implement :meth:`matches`. The :attr:`type_name`
    attribute is stamped by ``@register_trigger`` at decorator time.

    Implementations are pure functions of ``(event, config) -> bool``:

      * MUST NOT write to the DB.
      * MUST NOT raise. Return ``False`` on missing / partial data.
      * MUST NOT mutate ``event`` or ``config``.

    Violating any of the above breaks dispatcher idempotency or
    cross-trigger isolation.
    """

    # Stamped by @register_trigger. Subclasses should leave this alone.
    type_name: ClassVar[str] = ""

    # Required: subclass declares its config schema as a Pydantic model.
    # Used both for save-time validation of ChatFlow.triggers entries
    # and for the frontend introspection endpoint.
    config_model: ClassVar[type[BaseModel]]

    def matches(self, event: TriggerEvent, config: dict) -> bool:  # pragma: no cover
        raise NotImplementedError


def make_dispatch_key(event: TriggerEvent) -> str:
    """Return the Redis idempotency key for *event*.

    Exposed so tests can mock or assert key shape. Format is stable —
    operators may grep for it in Redis dumps.
    """
    return f"chatflow:trigger:dispatch:{event.tenant_id}:{event.channel}:{event.inbound_row_id}"


def claim_dispatch(event: TriggerEvent, ttl_seconds: int = 86400) -> bool:
    """Atomically claim the right to dispatch *event*. Returns True the
    first time, False on every subsequent call within the TTL window.

    24h covers any plausible webhook replay window; events older than
    that won't reach dispatch via the normal channel paths anyway. If
    Redis is unreachable, fail open (return True) — the alternative is
    to silently drop events on infrastructure hiccups.
    """
    try:
        from django_redis import get_redis_connection

        r = get_redis_connection("default")
        return bool(r.set(make_dispatch_key(event), "1", nx=True, ex=ttl_seconds))
    except Exception as exc:  # noqa: BLE001 — broad: any Redis failure
        logger.warning("[chat_flow.triggers] Redis claim failed (failing open): %s", exc)
        return True


def serialize_event(event: TriggerEvent) -> dict[str, Any]:
    """Make a JSON-serialisable dict copy of *event* for Celery payloads.

    Frozen dataclasses don't serialise through Django's default JSON
    encoder unless we project to a dict; this helper centralises that
    so all dispatch sites produce the same shape.
    """
    return {
        "tenant_id": event.tenant_id,
        "channel": event.channel,
        "contact_id": event.contact_id,
        "inbound_row_id": event.inbound_row_id,
        "inbound_row_model": event.inbound_row_model,
        "body_text": event.body_text,
        "received_at": event.received_at,
        "extra": dict(event.extra),
    }


__all__ = [
    "BaseTrigger",
    "Channel",
    "TriggerEvent",
    "claim_dispatch",
    "make_dispatch_key",
    "serialize_event",
]
