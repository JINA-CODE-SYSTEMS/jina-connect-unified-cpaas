"""Voice adapter registry.

Each concrete ``VoiceAdapter`` subclass calls ``register_voice_adapter``
at import time (typically from its module body). The factory then
resolves a config to its adapter class:

    tenant → TenantVoiceApp.default_outbound_config (a VoiceProviderConfig)
           → VoiceProviderConfig.provider (a VoiceProvider value)
           → registered VoiceAdapter subclass

The channel registry (``jina_connect.channel_registry``) dispatches
``PlatformChoices.VOICE`` to ``voice_adapter_factory(tenant)``, which
does the resolution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from voice.exceptions import VoiceError

if TYPE_CHECKING:
    from tenants.models import Tenant
    from voice.adapters.base import VoiceAdapter
    from voice.models import VoiceProviderConfig


_ADAPTER_BY_PROVIDER: dict[str, type["VoiceAdapter"]] = {}


def register_voice_adapter(provider: str, cls: type["VoiceAdapter"]) -> None:
    """Register a concrete adapter for a provider id.

    Idempotent: re-registering the same ``(provider, cls)`` pair is a
    no-op (autoreload / worker fork friendly). Re-registering a different
    class for the same provider raises — that's a real bug.
    """
    existing = _ADAPTER_BY_PROVIDER.get(provider)
    if existing is not None:
        if existing is cls:
            return
        raise ValueError(
            f"Voice adapter for {provider!r} already registered as "
            f"{existing.__name__}; refusing to overwrite with {cls.__name__}"
        )
    _ADAPTER_BY_PROVIDER[provider] = cls


def get_voice_adapter_cls(provider: str) -> type["VoiceAdapter"]:
    """Look up the registered adapter class for ``provider``.

    Raises ``NotImplementedError`` when no adapter is registered — that's
    the signal that this provider's adapter PR hasn't landed yet (Twilio
    in #160, Plivo in #164, etc.).
    """
    cls = _ADAPTER_BY_PROVIDER.get(provider)
    if cls is None:
        raise NotImplementedError(
            f"No voice adapter registered for provider {provider!r}. Available: {sorted(_ADAPTER_BY_PROVIDER)}"
        )
    return cls


def _resolve_config(tenant: "Tenant") -> "VoiceProviderConfig":
    """Pick the tenant's default outbound voice config.

    Raises ``VoiceError`` if the tenant has no ``TenantVoiceApp`` row,
    has voice disabled, or has no default outbound config set.
    """
    # Imported lazily to keep the registry module Django-app-loading-safe.
    from tenants.models import TenantVoiceApp

    try:
        app = tenant.voice_app  # OneToOneField reverse accessor
    except TenantVoiceApp.DoesNotExist as e:
        raise VoiceError(f"Tenant {tenant.id} has no TenantVoiceApp row; voice is not provisioned.") from e

    if not app.is_enabled:
        raise VoiceError(f"Voice is disabled for tenant {tenant.id}.")

    if app.default_outbound_config is None:
        raise VoiceError(f"Tenant {tenant.id} has no default outbound voice config configured.")

    return app.default_outbound_config


def voice_adapter_factory(tenant: "Tenant") -> "VoiceAdapter":
    """Channel-registry entry point. Resolve tenant → config → adapter.

    Registered in ``voice/apps.py:VoiceConfig.ready()`` via
    ``register_channel(PlatformChoices.VOICE, voice_adapter_factory)``.
    """
    config = _resolve_config(tenant)
    cls = get_voice_adapter_cls(config.provider)
    return cls(config)


# ─────────────────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────────────────


def _reset_voice_adapter_registry() -> None:
    """Drop all registrations. Tests-only — exposed so test setUp can
    snapshot / restore the registry without leaking state across tests."""
    _ADAPTER_BY_PROVIDER.clear()
