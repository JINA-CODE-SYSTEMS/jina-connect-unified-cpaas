"""IVR compiler (#168).

Walks a ``ChatFlow.flow_data`` graph and produces a provider-specific
response (TwiML / Plivo XML / NCCO list / Telnyx command list / ExoML /
ARI command list) by dispatching each node's ``type`` to the
adapter's dialect.

Same compiler regardless of provider; each adapter exposes
``get_dialect()`` returning its dialect module
(``voice.ivr.dialects.twiml`` / ``plivo_xml`` / ``ncco`` /
``telnyx_cc`` / ``exotel_xml`` / ``ari_commands``).

Compiler invariants:
  * Walks nodes in declaration order; control flow happens at runtime
    via the IVR session (``voice.ivr.session``) when a gather/record
    node sends the caller back through the gather webhook.
  * Unknown node types are skipped with a logged warning so a new
    node type in the visual editor doesn't bomb every call.
  * ``conditional`` / ``set_variable`` / ``http_call`` / ``wait`` are
    channel-agnostic — they don't have voice dialect handlers but
    they're processed by ``chat_flow``'s flow engine before this
    compiler runs (compiler only sees the resolved path).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class VoiceDialect(Protocol):
    """Structural type a dialect module satisfies.

    Every module under ``voice.ivr.dialects`` exports
    ``get_handler(type_id)`` and ``assemble(chunks)`` matching this.
    """

    def get_handler(self, type_id: str): ...

    def assemble(self, chunks: list): ...


class IvrCompilationError(Exception):
    """Raised when the compiler can't produce a usable response.

    Typically because the flow's entry node id points at a missing
    node, or every node was unknown to the dialect.
    """


def compile_for_adapter(
    flow_data: dict[str, Any],
    adapter,
    context: dict[str, Any] | None = None,
):
    """Compile a flow into the adapter's dialect output.

    Args:
        flow_data: The ``ChatFlow.flow_data`` dict — ``{nodes: [...],
            edges: [...], entry_node_id: "..."}``. For non-WhatsApp
            flows ``entry_node_id`` keys the first node to render.
            Falls back to the first node when omitted.
        adapter: A ``VoiceAdapter`` subclass instance. Must expose
            ``get_dialect()`` returning a dialect module.
        context: Optional render context — call-specific data the
            dialect emitters can use (e.g. ``gather_action_url``,
            ``default_tts_voice``).

    Returns whatever the dialect's ``assemble`` returns — a string of
    TwiML / Plivo XML / ExoML, a JSON-serialisable list (NCCO,
    Telnyx commands, ARI commands).

    Raises ``IvrCompilationError`` if the flow is empty / the entry
    node id is bogus / no node produces dialect output.
    """
    if context is None:
        context = {}

    nodes = flow_data.get("nodes") or []
    if not nodes:
        raise IvrCompilationError("Flow has no nodes")

    dialect = adapter.get_dialect()
    ordered_nodes = _ordered_walk(flow_data)

    chunks = []
    for node in ordered_nodes:
        type_id = node.get("type")
        handler = dialect.get_handler(type_id) if type_id else None
        if handler is None:
            if type_id and type_id not in _CHANNEL_AGNOSTIC_NODE_TYPES:
                logger.warning(
                    "[voice.ivr.compiler] no dialect handler for node type %r — skipping",
                    type_id,
                )
            continue
        chunks.append(handler(node.get("data") or {}, context))

    if not chunks:
        raise IvrCompilationError("Flow produced no dialect output (all nodes unknown or unhandled)")

    return dialect.assemble(chunks)


# Nodes that ``chat_flow``'s flow engine resolves before the compiler
# runs — the compiler should silently skip them rather than warn.
_CHANNEL_AGNOSTIC_NODE_TYPES = frozenset({"conditional", "set_variable", "http_call", "wait"})


def _ordered_walk(flow_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return nodes in execution order starting at ``entry_node_id``.

    Walks edges greedily — picks the first outgoing edge from each
    node. ``chat_flow``'s richer branch resolution happens at runtime
    via the IVR session + gather webhooks; this compile-time walk
    produces the linear "happy path" the answer webhook returns.

    Falls back to declaration order if no edges / no entry id.
    """
    nodes_by_id = {n.get("id"): n for n in (flow_data.get("nodes") or []) if n.get("id")}
    entry_id = flow_data.get("entry_node_id")
    if entry_id is None:
        # No explicit entry — use declaration order.
        return list(flow_data.get("nodes") or [])
    if entry_id not in nodes_by_id:
        raise IvrCompilationError(f"entry_node_id {entry_id!r} not in flow_data.nodes")

    edges = flow_data.get("edges") or []
    edges_by_source: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        src = e.get("source")
        if src:
            edges_by_source.setdefault(src, []).append(e)

    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()
    current = entry_id
    while current and current not in visited:
        visited.add(current)
        node = nodes_by_id.get(current)
        if node is None:
            break
        ordered.append(node)
        outgoing = edges_by_source.get(current) or []
        if not outgoing:
            break
        current = outgoing[0].get("target")

    return ordered
