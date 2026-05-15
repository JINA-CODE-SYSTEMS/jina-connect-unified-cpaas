"""
Node-type registry for ``ChatFlow.flow_data``.

The ReactFlow JSON blob stored in ``ChatFlow.flow_data`` is opaque to the
backend. This registry gives the backend a way to:

  * Declare which node types are valid per platform
  * Validate required fields on each node
  * Reject flows that mix incompatible nodes (e.g. ``voice.play`` in a
    WhatsApp flow) at save time

Voice is the first user; existing channels can register their node types
in a separate cleanup PR.

Usage::

    from chat_flow.node_registry import register_node_type, NodeTypeSpec

    register_node_type(NodeTypeSpec(
        type_id="voice.play",
        display_name="Play audio or TTS",
        description="Play pre-recorded audio or TTS prompt",
        supported_platforms=frozenset(["VOICE"]),
        required_data_fields=frozenset(),
        optional_data_fields=frozenset(["audio_url", "tts_text"]),
        validator=lambda d: (
            ["voice.play requires audio_url or tts_text"]
            if not d.get("audio_url") and not d.get("tts_text") else []
        ),
    ))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeTypeSpec:
    """
    Declarative spec for one node type that can appear in ``flow_data``.

    Attributes:
        type_id: ``flow_data["nodes"][i]["type"]`` value (e.g. ``"voice.play"``).
        display_name: Human-readable name for UI.
        description: One-line description for the registry index.
        supported_platforms: Set of ``PlatformChoices`` values where this
            node is allowed. Empty set means "platform-agnostic — works
            anywhere" (use for ``conditional``, ``set_variable``, etc.).
        required_data_fields: Keys that must appear in ``node["data"]``.
        optional_data_fields: Keys that may appear; documented but unenforced.
        validator: Optional callable that receives ``node["data"]`` and
            returns a list of error strings. Empty list = valid.
    """

    type_id: str
    display_name: str
    description: str
    supported_platforms: frozenset[str]
    required_data_fields: frozenset[str]
    optional_data_fields: frozenset[str] = field(default_factory=frozenset)
    validator: Callable[[dict], list[str]] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, NodeTypeSpec] = {}


def register_node_type(spec: NodeTypeSpec) -> None:
    """Register a node type.

    Idempotent: re-registering an *identical* spec is a no-op. Django
    autoreload, Celery worker forks, and test re-imports may all re-execute
    registration modules, and forcing them to all raise would be brittle.

    Raises ``ValueError`` only when the same ``type_id`` is registered with
    a *different* spec — that's a real bug worth flagging loudly.
    """
    existing = _REGISTRY.get(spec.type_id)
    if existing is not None:
        if existing == spec:
            return
        raise ValueError(
            f"Node type {spec.type_id!r} already registered with a different spec (existing={existing!r}, new={spec!r})"
        )
    _REGISTRY[spec.type_id] = spec


def unregister_node_type(type_id: str) -> None:
    """Remove a registration. Mostly for tests."""
    _REGISTRY.pop(type_id, None)


def get_node_type(type_id: str) -> NodeTypeSpec | None:
    """Look up a registered node type by id, or ``None`` if unknown."""
    return _REGISTRY.get(type_id)


def list_node_types_for_platform(platform: str) -> list[NodeTypeSpec]:
    """Return every spec whose ``supported_platforms`` allows ``platform``.

    Platform-agnostic specs (empty ``supported_platforms``) are always
    included.
    """
    return [spec for spec in _REGISTRY.values() if not spec.supported_platforms or platform in spec.supported_platforms]


def validate_flow_for_platform(flow_data: dict, platform: str) -> list[str]:
    """
    Validate every node in ``flow_data`` against the registry for ``platform``.

    Returns a list of error strings (empty list = valid).

    Behaviour:
      * Unknown node types (not registered) are logged as warnings when
        ``settings.WARN_ON_UNKNOWN_NODE_TYPES`` is truthy, but do NOT raise.
        Rationale: ReactFlow may add types ahead of backend; existing
        channels haven't migrated to the registry yet. Strict rejection
        would break every legacy flow.
      * Registered node, unsupported platform → error.
      * Registered node, missing required field → error.
      * Custom validator errors are appended.
    """
    if not isinstance(flow_data, dict):
        # Defensive: callers may pass None for draft flows.
        return []

    errors: list[str] = []
    nodes = flow_data.get("nodes") or []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        type_id = node.get("type")
        if not type_id:
            continue

        spec = _REGISTRY.get(type_id)
        if spec is None:
            if getattr(settings, "WARN_ON_UNKNOWN_NODE_TYPES", True):
                logger.warning(
                    "Unknown node type %r in flow (node id=%s)",
                    type_id,
                    node.get("id"),
                )
            continue

        if spec.supported_platforms and platform not in spec.supported_platforms:
            errors.append(f"Node {node.get('id')!r} of type {type_id!r} is not supported on platform {platform!r}")
            continue

        data = node.get("data") or {}
        missing = spec.required_data_fields - set(data.keys())
        if missing:
            errors.append(f"Node {node.get('id')!r} ({type_id!r}) missing required fields: {sorted(missing)}")

        if spec.validator is not None:
            errors.extend(spec.validator(data))

    return errors
