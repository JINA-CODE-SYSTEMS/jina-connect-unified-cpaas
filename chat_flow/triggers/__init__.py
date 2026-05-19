"""Event-trigger subsystem for chat_flow (#188).

Bridges inbound events (WA message, voice call, SMS, RCS, Telegram)
to flow invocation. Channel-agnostic substrate with a registry of
named trigger types. Each channel's webhook processor emits a
normalised ``TriggerEvent`` after persisting the inbound row; the
dispatcher fans out to every active flow whose configured trigger
matches.

Public surface:

  * :class:`TriggerEvent` — the normalised cross-channel payload.
  * :class:`BaseTrigger` — ABC every trigger subclasses.
  * :func:`register_trigger` — decorator used by trigger modules.
  * :func:`emit` — single entry point for channel processors.
  * :func:`list_trigger_types` — used by the frontend introspection
    endpoint to render the trigger-config panel.
"""

from __future__ import annotations

from chat_flow.triggers.base import BaseTrigger, TriggerEvent
from chat_flow.triggers.dispatcher import dispatch
from chat_flow.triggers.registry import (
    get_trigger_cls,
    list_trigger_types,
    register_trigger,
)


def emit(event: TriggerEvent) -> int:
    """Public entry point for channel webhook processors.

    Returns the number of flow sessions queued for this event. Safe to
    call from synchronous code paths — the dispatcher itself never
    raises and is idempotent across replays (Redis SETNX keyed on
    ``(tenant, channel, inbound_row_id)`` with a 24h TTL).
    """
    return dispatch(event)


__all__ = [
    "BaseTrigger",
    "TriggerEvent",
    "emit",
    "register_trigger",
    "get_trigger_cls",
    "list_trigger_types",
]
