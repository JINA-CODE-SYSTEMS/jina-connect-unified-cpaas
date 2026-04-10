"""
Base BSP Adapter — Abstract interface for WhatsApp Business Solution Providers.

All BSP adapters (META Direct, Gupshup, WATI, Twilio, etc.) must implement
this interface so the rest of the application can work with templates,
messages, and media in a provider-agnostic way.

Architecture:
    ViewSet / Signal
        └── get_bsp_adapter(wa_app)   ← factory (see __init__.py)
                └── MetaDirectAdapter | GupshupAdapter | …
                        └── provider-specific HTTP client

Usage:
    from wa.adapters import get_bsp_adapter

    adapter = get_bsp_adapter(wa_app)
    result  = adapter.submit_template(template)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from wa.models import WAApp, WASubscription, WATemplate

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Result objects — every adapter method returns one of these so callers don't
# need to know about provider-specific response shapes.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AdapterResult:
    """Uniform result wrapper returned by every adapter operation."""

    success: bool
    provider: str  # "meta_direct", "gupshup", …
    data: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None  # full provider response for debugging

    def __bool__(self) -> bool:          # lets you do `if result:`
        return self.success


# ──────────────────────────────────────────────────────────────────────────────
# Abstract base
# ──────────────────────────────────────────────────────────────────────────────

class BaseBSPAdapter(ABC):
    """
    Abstract base class that every BSP adapter must implement.

    Sub-classes are initialised with a WAApp instance which carries the BSP
    credentials (token, waba_id, bsp_credentials JSON, etc.).
    """

    # Human-readable name shown in logs / API responses.
    PROVIDER_NAME: str = "base"

    # Capabilities this adapter supports.  Sub-classes should override with
    # a frozenset of capability strings, e.g.
    #   CAPABILITIES = frozenset({"templates", "subscriptions", "media_upload"})
    # Recognised capabilities:
    #   "templates"      – submit / sync / delete message templates
    #   "subscriptions"  – register / unregister / list BSP-level webhooks
    #   "media_upload"   – upload media and obtain a handle / URL
    CAPABILITIES: frozenset = frozenset()

    def __init__(self, wa_app: "WAApp") -> None:
        self.wa_app = wa_app

    # ── Capability introspection ──────────────────────────────────────────

    def supports(self, capability: str) -> bool:
        """Return *True* if this adapter supports the given capability."""
        return capability in self.CAPABILITIES

    # ── Template operations ───────────────────────────────────────────────

    @abstractmethod
    def submit_template(self, template: "WATemplate") -> AdapterResult:
        """
        Submit a template to the BSP for META approval.

        Implementations should:
        1. Build the provider-specific payload (e.g. ``template.to_meta_payload()``).
        2. Call the provider's HTTP API.
        3. On success — populate ``template.meta_template_id`` / ``bsp_template_id``,
           set ``status = PENDING``, ``needs_sync = False``, ``error_message = None``.
        4. On failure — set ``error_message``, leave status as-is.
        5. Save the template.
        6. Return an ``AdapterResult``.
        """
        ...

    @abstractmethod
    def get_template_status(self, template: "WATemplate") -> AdapterResult:
        """
        Fetch the current approval status of a template from the BSP.

        On success ``data`` should include at least ``{"status": "<STATUS>"}``
        using our canonical ``TemplateStatus`` values.
        """
        ...

    @abstractmethod
    def delete_template(self, template: "WATemplate") -> AdapterResult:
        """
        Delete / deregister a template with the BSP.

        Not all BSPs support this — implementations may return a
        "not_supported" ``AdapterResult``.
        """
        ...

    @abstractmethod
    def list_templates(self) -> AdapterResult:
        """
        Fetch all templates from the BSP for this app.

        On success ``data`` should contain ``{"templates": [...]}``.
        Each item is a raw dict from the BSP (Gupshup, META, etc.).
        The sync service is responsible for mapping to canonical fields.
        """
        ...

    # ── Media operations ─────────────────────────────────────────────────

    @abstractmethod
    def upload_media(
        self,
        file_obj,
        filename: str,
        file_type: Optional[str] = None,
    ) -> AdapterResult:
        """
        Upload a media file to the BSP and return a **handle ID**.

        The handle ID is a permanent reference that can be used as
        ``exampleMedia`` when submitting IMAGE / VIDEO / DOCUMENT
        templates (and carousel cards).

        Implementations should:
        1. Call the BSP's media-upload endpoint.
        2. Return ``AdapterResult(data={"handle_id": "..."})``. on success.
        3. Return a failed ``AdapterResult`` with ``error_message`` on failure.

        Args:
            file_obj: File-like object (e.g. Django ``InMemoryUploadedFile``).
            filename: Original file name (used for MIME-type detection).
            file_type: Explicit MIME type.  ``None`` → auto-detect.
        """
        ...

    def upload_session_media(
        self,
        file_obj,
        filename: str,
        file_type: Optional[str] = None,
    ) -> AdapterResult:
        """
        Upload media for **session** (non-template) messages.

        The returned handle/ID must be valid for use as ``image.id``,
        ``video.id``, etc. in Cloud API session message payloads.

        Default implementation falls back to ``upload_media()``.
        BSPs where template handles differ from session media IDs
        (e.g. META Direct) should override this method.
        """
        return self.upload_media(
            file_obj=file_obj,
            filename=filename,
            file_type=file_type,
        )

    # ── Webhook Subscription operations ───────────────────────────────────

    @abstractmethod
    def register_webhook(self, subscription: "WASubscription") -> AdapterResult:
        """
        Register a webhook subscription with the BSP.

        Implementations should:
        1. Build a BSP-specific subscription payload from the canonical
           ``WASubscription`` (webhook_url, event_types, etc.).
        2. Call the provider's subscription API.
        3. On success — set ``subscription.bsp_subscription_id``,
           ``status = ACTIVE``, ``error_message = None``.
        4. On failure — set ``error_message``, ``status = FAILED``.
        5. Save the subscription.
        6. Return an ``AdapterResult``.
        """
        ...

    @abstractmethod
    def unregister_webhook(self, subscription: "WASubscription") -> AdapterResult:
        """
        Unregister / delete a webhook subscription from the BSP.

        On success set ``status = INACTIVE``.
        Not all BSPs support this — implementations may return a
        "not_supported" ``AdapterResult``.
        """
        ...

    @abstractmethod
    def list_webhooks(self) -> AdapterResult:
        """
        List all webhook subscriptions registered with the BSP for this app.

        ``data`` should contain ``{"subscriptions": [...]}``.  Each item
        is a dict with at least ``{"id": ..., "url": ..., "events": [...]}``.
        """
        ...

    @abstractmethod
    def purge_all_webhooks(self) -> AdapterResult:
        """
        Delete ALL webhook subscriptions on the BSP side for this app.

        Used before re-registering to avoid hitting BSP limits
        (e.g. Gupshup allows max 5 subscriptions per app).

        Returns ``AdapterResult`` with ``data.deleted_count``.
        """
        ...

    # ── Helpers ───────────────────────────────────────────────────────────

    def _log(self, level: str, msg: str, **kwargs: Any) -> None:
        """Convenience logger that prefixes the provider name."""
        getattr(logger, level)(
            f"[{self.PROVIDER_NAME}] {msg}",
            **kwargs,
        )
