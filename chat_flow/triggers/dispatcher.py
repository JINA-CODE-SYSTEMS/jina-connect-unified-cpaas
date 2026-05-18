"""Trigger dispatcher for chat_flow (#188).

Given a normalised :class:`TriggerEvent`, find every active
:class:`ChatFlow` in the event's tenant whose ``triggers`` config
matches, and queue a session spawn via the existing
``start_chatflow_session_task`` Celery task. Idempotent across
webhook replays via :func:`chat_flow.triggers.base.claim_dispatch`.

Design contracts:

  * A trigger that raises inside ``matches()`` is logged and skipped.
    A single misbehaving trigger never breaks dispatch for the others.

  * At most one session is queued per flow per event — the first
    matching trigger entry wins. Multiple flows can still match the
    same event independently.

  * Dispatcher never writes to the DB itself (no audit table). The
    spawned ``UserChatFlowSession`` carries trigger context inside its
    ``context_data`` JSON; that's the audit trail.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from chat_flow.triggers.base import (
    TriggerEvent,
    claim_dispatch,
    serialize_event,
)
from chat_flow.triggers.registry import _REGISTRY

if TYPE_CHECKING:
    from chat_flow.models import ChatFlow

logger = logging.getLogger(__name__)


def dispatch(event: TriggerEvent) -> int:
    """Fan *event* out to every matching active flow in the tenant.

    Returns the number of flow-session spawn tasks queued. Returns 0
    on idempotency claim failure (the event has already been
    dispatched within the TTL window).
    """
    if not claim_dispatch(event):
        logger.debug(
            "[chat_flow.triggers] dispatch claim denied for event %s/%s/%s",
            event.tenant_id,
            event.channel,
            event.inbound_row_id,
        )
        return 0

    # Late import — avoid pulling models at module-import time so
    # `chat_flow.triggers` stays importable from anywhere.
    from chat_flow.models import ChatFlow

    flows = (
        ChatFlow.objects.filter(tenant_id=event.tenant_id, is_active=True)
        .exclude(triggers=[])
        .exclude(triggers__isnull=True)
    )

    serialised = serialize_event(event)
    spawned = 0
    for flow in flows.iterator():
        if _flow_matches(flow, event):
            _queue_spawn(flow, event, serialised)
            spawned += 1

    return spawned


def _flow_matches(flow: "ChatFlow", event: TriggerEvent) -> bool:
    """True if any trigger entry on *flow* matches *event*. Short-circuits
    on first match. Misbehaving triggers are logged and skipped."""
    for trigger_def in flow.triggers or []:
        type_name = trigger_def.get("type") if isinstance(trigger_def, dict) else None
        if not type_name:
            continue
        cls = _REGISTRY.get(type_name)
        if cls is None:
            # Trigger type was removed since the flow was saved.
            # Save-time validation should normally catch this; we
            # tolerate it here so a code rollback doesn't break dispatch.
            logger.warning(
                "[chat_flow.triggers] flow %s references unknown trigger %r",
                flow.id,
                type_name,
            )
            continue
        try:
            if cls().matches(event, trigger_def.get("config", {})):
                return True
        except Exception as exc:  # noqa: BLE001 — broad: never break dispatch
            logger.exception(
                "[chat_flow.triggers] %s.matches() raised on flow %s: %s",
                type_name,
                flow.id,
                exc,
            )
    return False


def _queue_spawn(flow: "ChatFlow", event: TriggerEvent, serialised: dict) -> None:
    """Queue a session spawn for (flow, event). Never raises."""
    try:
        from chat_flow.tasks import start_chatflow_session_task

        start_chatflow_session_task.delay(
            chatflow_id=str(flow.id),
            contact_id=event.contact_id,
            context={"trigger_event": serialised},
        )
    except Exception as exc:  # noqa: BLE001 — broad: log + move on
        logger.exception(
            "[chat_flow.triggers] failed to queue session spawn for flow %s: %s",
            flow.id,
            exc,
        )


__all__ = ["dispatch"]
