"""
Channel Adapter Registry — resolves the correct adapter for any platform + tenant.

Usage:
    from jina_connect.channel_registry import get_channel_adapter

    adapter = get_channel_adapter("TELEGRAM", tenant)
    result = adapter.send_text(chat_id=str(contact.telegram_chat_id), text="Hello!")

New channels register at app ready() time via register_channel().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Dict

if TYPE_CHECKING:
    from wa.adapters.channel_base import BaseChannelAdapter

logger = logging.getLogger(__name__)

_CHANNEL_REGISTRY: Dict[str, Callable] = {}


def register_channel(platform: str, factory: Callable) -> None:
    """Register a channel adapter factory.  Called from AppConfig.ready()."""
    _CHANNEL_REGISTRY[platform.upper()] = factory
    logger.info("Registered channel adapter for %s", platform.upper())


def get_channel_adapter(platform: str, tenant) -> "BaseChannelAdapter":
    """
    Resolve the correct channel adapter for a platform + tenant.

    Raises:
        NotImplementedError: If no adapter is registered for the platform.
        ValueError: If the tenant has no active app for the requested channel.
    """
    factory = _CHANNEL_REGISTRY.get(platform.upper())
    if not factory:
        raise NotImplementedError(f"No channel adapter for '{platform}'. Available: {list(_CHANNEL_REGISTRY.keys())}")
    return factory(tenant)
