"""Ending chat-flow sessions that stopped advancing.

``UserChatFlowSession`` had no expiry. It ended at an end node, on an explicit
reset, or when the flow was deactivated — and nowhere else. Both the model
docstring ("until end node reached or session expires") and ``ended_at``
("completed or expired") describe a state nothing could produce, and the index
on ``("is_active", "started_at")`` exists for a sweep that was never written.

The consequence is not an untidy table. Editing a flow is refused while any
session is active, so one contact who started a flow and never replied blocks
that flow from being edited *permanently*.

What this must not do is end a conversation that is merely waiting on purpose.
A delay node parks the session and schedules a Celery task to resume it —
``trigger_at`` can be days or weeks out — so an idle sweep that did not know
about delay nodes would quietly cancel every scheduled flow on the platform,
which is a far worse bug than the one it fixes.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Reason written to ``cancellation_reason`` so an expired session is
#: distinguishable from one a person ended.
EXPIRY_REASON = "Idle timeout"

#: Node types that are waiting on purpose and must never be swept.
PARKED_NODE_TYPES = frozenset({"delay"})


def idle_cutoff(hours: int | None = None):
    """The moment before which a session counts as idle."""
    if hours is None:
        hours = getattr(settings, "CHATFLOW_SESSION_IDLE_TIMEOUT_HOURS", 72)
    return timezone.now() - timedelta(hours=hours)


def stale_sessions(*, flow=None, hours: int | None = None) -> QuerySet:
    """Active sessions that have not advanced since the cutoff.

    ``updated_at`` rather than ``started_at``: a session that is still being
    answered is saved on every step, so ``started_at`` would expire a long but
    live conversation while ``updated_at`` expires only one that stopped.

    Sessions parked at a delay node are excluded — see the module docstring.

    Args:
        flow: limit to one ``ChatFlow``, or None for every flow.
        hours: override the configured timeout.

    Returns:
        QuerySet of ``UserChatFlowSession``.
    """
    from chat_flow.models import ChatFlowNode, UserChatFlowSession

    sessions = UserChatFlowSession.objects.filter(is_active=True, updated_at__lt=idle_cutoff(hours))
    if flow is not None:
        sessions = sessions.filter(flow=flow)

    # Resolved per flow rather than globally: a node_id is only unique within
    # its own flow, so a global list of "delay node ids" would exclude sessions
    # in other flows that happen to share an id.
    parked_pairs = set(ChatFlowNode.objects.filter(node_type__in=PARKED_NODE_TYPES).values_list("flow_id", "node_id"))
    if not parked_pairs:
        return sessions

    parked_ids = [
        session.pk
        for session in sessions.only("pk", "flow_id", "current_node_id")
        if (session.flow_id, session.current_node_id) in parked_pairs
    ]
    return sessions.exclude(pk__in=parked_ids)


def expire_idle_sessions(*, flow=None, hours: int | None = None) -> int:
    """End every stale session and report how many were ended.

    Args:
        flow: limit to one ``ChatFlow``, or None for every flow.
        hours: override the configured timeout.

    Returns:
        Number of sessions ended.
    """
    # Materialised before the update, because the queryset is defined by the
    # very field the update changes and would otherwise match nothing.
    stale_ids = list(stale_sessions(flow=flow, hours=hours).values_list("pk", flat=True))
    if not stale_ids:
        return 0

    from chat_flow.models import UserChatFlowSession

    ended = UserChatFlowSession.objects.filter(pk__in=stale_ids).update(
        is_active=False,
        ended_at=timezone.now(),
        cancellation_reason=EXPIRY_REASON,
    )
    logger.info("[chat_flow] expired %s idle session(s)", ended)
    return ended
