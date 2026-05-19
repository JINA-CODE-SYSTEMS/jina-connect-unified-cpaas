"""Trigger registry for chat_flow (#188).

Mirrors the established pattern in :mod:`voice.adapters.registry`:
module-level dict plus a ``@register_trigger("name")`` decorator. Each
trigger type module imports the decorator and decorates its class;
``chat_flow.apps.ChatFlowConfig.ready()`` imports the types package
at app boot so the registry is populated before any flow saves.
"""

from __future__ import annotations

from chat_flow.triggers.base import BaseTrigger

_REGISTRY: dict[str, type[BaseTrigger]] = {}


def register_trigger(type_name: str):
    """Decorator that registers a :class:`BaseTrigger` subclass under
    *type_name*.

    Re-registering the same ``(type_name, cls)`` pair is a no-op —
    handy for module-reload paths under pytest. Registering a
    different class under an existing name raises ``RuntimeError`` so
    accidental collisions surface immediately.
    """

    def _wrap(cls: type[BaseTrigger]) -> type[BaseTrigger]:
        existing = _REGISTRY.get(type_name)
        if existing is not None and existing is not cls:
            raise RuntimeError(
                f"Trigger {type_name!r} already registered to "
                f"{existing.__module__}.{existing.__qualname__}; refusing to "
                f"replace with {cls.__module__}.{cls.__qualname__}"
            )
        cls.type_name = type_name
        _REGISTRY[type_name] = cls
        return cls

    return _wrap


def get_trigger_cls(type_name: str) -> type[BaseTrigger]:
    """Return the registered class for *type_name* or raise ``LookupError``."""
    try:
        return _REGISTRY[type_name]
    except KeyError as exc:
        raise LookupError(f"No trigger registered for {type_name!r}. Known: {list_trigger_types()}") from exc


def list_trigger_types() -> list[str]:
    """Sorted snapshot of registered type names. Used by the frontend
    introspection endpoint."""
    return sorted(_REGISTRY)


def trigger_introspection() -> list[dict]:
    """Shape returned by ``GET /api/chat_flow/triggers/types/``.

    Each entry is ``{type_name, config_schema}`` where ``config_schema``
    is the Pydantic-generated JSON Schema for the trigger's config
    payload. The frontend renders the trigger-config form from this.
    """
    return [
        {
            "type_name": name,
            "config_schema": cls.config_model.model_json_schema(),
        }
        for name, cls in sorted(_REGISTRY.items())
    ]


__all__ = [
    "get_trigger_cls",
    "list_trigger_types",
    "register_trigger",
    "trigger_introspection",
]
