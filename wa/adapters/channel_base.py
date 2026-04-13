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
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BaseChannelAdapter(ABC):
    """
    Abstract base for channel adapters.

    Sub-classes are initialised with a tenant and carry the credentials
    needed to send messages on that channel.
    """

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

    @abstractmethod
    def get_channel_name(self) -> str:
        """Return the canonical channel name (e.g. ``'WHATSAPP'``, ``'TELEGRAM'``)."""
        ...
