"""
BaseChannelAdapter — Abstract interface for all messaging channels.

Every channel (WhatsApp, Telegram, SMS, etc.) must provide an adapter that
implements this interface. The channel registry resolves the correct adapter
at runtime via ``get_channel_adapter(platform, tenant)``.

Architecture:
    get_channel_adapter("TELEGRAM", tenant)
        └── TelegramAdapter(BaseChannelAdapter)
                └── TelegramBotClient (HTTP)

    get_channel_adapter("WHATSAPP", tenant)
        └── BaseBSPAdapter(BaseChannelAdapter)
                └── MetaDirectAdapter | GupshupAdapter

Capability declarations:
    Each adapter declares its ``platform`` and ``capabilities`` so callers can
    introspect what the channel supports before invoking a method. See
    ``Capabilities`` below.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Capabilities:
    """
    Declared capabilities of a channel adapter.

    Flags are floor declarations — if a flag is True the corresponding method
    must be implemented and functional. Defaults are conservative (most False)
    so a new adapter only opts in to what it can actually do.

    Callers (broadcast pre-flight, voice node validation, MCP) introspect
    these flags rather than catching ``NotImplementedError``.
    """

    # ── Text-channel capabilities ────────────────────────────────────────
    supports_text: bool = True
    supports_media: bool = False
    supports_keyboards: bool = False
    supports_templates: bool = False
    supports_template_buttons: bool = False
    supports_reactions: bool = False
    supports_typing_indicator: bool = False

    # ── Voice capabilities ───────────────────────────────────────────────
    supports_voice_call: bool = False
    supports_recording: bool = False
    supports_dtmf_gather: bool = False
    supports_speech_gather: bool = False
    supports_call_transfer: bool = False
    supports_sip_refer: bool = False
    supports_conference: bool = False

    # ── Billing ──────────────────────────────────────────────────────────
    supports_provider_cost: bool = False

    # Free-form extension slot for adapter-specific flags that don't warrant
    # a typed field yet.
    extra: frozenset[str] = field(default_factory=frozenset)


class BaseChannelAdapter(ABC):
    """
    Abstract base for channel adapters.

    Sub-classes are initialised with a tenant and carry the credentials
    needed to send messages on that channel.
    """

    # Canonical platform identifier (a ``PlatformChoices`` value such as
    # ``"WHATSAPP"``, ``"TELEGRAM"``, ``"VOICE"``). Subclasses set this.
    platform: str = ""

    # Declared capabilities for this adapter. Subclasses override.
    capabilities: Capabilities = Capabilities()

    @abstractmethod
    def send_text(self, chat_id: str, text: str, **kwargs: Any) -> Dict[str, Any]:
        """
        Send a plain text message.

        Args:
            chat_id: Channel-specific recipient identifier
                     (phone for WA/SMS, chat_id for Telegram).
            text: Message body.

        Returns:
            Dict with at least ``{"success": bool, "message_id": str}``.
        """
        ...

    @abstractmethod
    def send_media(
        self,
        chat_id: str,
        media_type: str,
        media_url: str,
        caption: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Send a media message (image, video, document, audio, etc.).

        Args:
            chat_id: Recipient identifier.
            media_type: One of "image", "video", "document", "audio", "voice".
            media_url: URL or file reference for the media.
            caption: Optional caption text.

        Returns:
            Dict with at least ``{"success": bool, "message_id": str}``.
        """
        ...

    @abstractmethod
    def send_keyboard(
        self,
        chat_id: str,
        text: str,
        keyboard: list,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Send a message with an interactive keyboard / quick-reply buttons.

        Args:
            chat_id: Recipient identifier.
            text: Body text shown with the keyboard.
            keyboard: Channel-specific button specification.

        Returns:
            Dict with at least ``{"success": bool, "message_id": str}``.
        """
        ...

    def get_channel_name(self) -> str:
        """Return the canonical channel name (e.g. ``'WHATSAPP'``, ``'TELEGRAM'``).

        Default implementation returns ``self.platform``. Subclasses may
        still override if they need different semantics, but new adapters
        should just set ``platform`` instead.
        """
        return self.platform
