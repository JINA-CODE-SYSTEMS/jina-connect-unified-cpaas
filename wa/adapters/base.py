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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from jina_connect.platform_choices import PlatformChoices
from wa.adapters.channel_base import BaseChannelAdapter
from wa.adapters.ctwa_referral import CtwaReferral  # noqa: F401 — re-export

if TYPE_CHECKING:
    from wa.models import WAApp, WASubscription, WATemplate

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Result objects — every adapter method returns one of these so callers don't
# need to know about provider-specific response shapes.
# ──────────────────────────────────────────────────────────────────────────────


def _retry_after_seconds(raw: Any) -> Optional[int]:
    """Parse a ``Retry-After`` header value into seconds.

    RFC 9110 allows two spellings — delta-seconds and an HTTP-date — and
    providers use both. Anything else is treated as absent: a malformed header
    must never take a send down, it just means the caller falls back to its own
    retry interval (#271).

    A non-positive interval is also "absent": the provider is not asking us to
    wait, so there is nothing to honour.
    """
    if raw is None:
        return None

    value = str(raw).strip()
    if not value:
        return None

    try:
        # OverflowError as well as ValueError: "inf" parses as a float and then
        # refuses to be an int.
        seconds = int(float(value))
    except (TypeError, ValueError, OverflowError):
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        # A date with no zone is GMT per the spec; reading it as local time
        # would move the deadline by hours.
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = int((when - datetime.now(tz=timezone.utc)).total_seconds())

    return seconds if seconds > 0 else None


@dataclass
class AdapterResult:
    """Uniform result wrapper returned by every adapter operation."""

    success: bool
    provider: str  # "meta_direct", "gupshup", …
    data: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None  # full provider response for debugging
    # Response headers with lower-cased keys, so the caller reads one spelling
    # whichever provider answered. Filled in on the failure path only — the HTTP
    # clients hand back parsed JSON on success and the headers are gone by then
    # — which is enough, because the header this exists for arrives with a 429
    # or a 503 (#271).
    response_headers: Dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:  # lets you do `if result:`
        return self.success

    @property
    def retry_after_seconds(self) -> Optional[int]:
        """How long the provider asked us to wait, or ``None`` if it didn't ask.

        A 429 was previously re-queued on a fixed five-minute cron, which is
        the wrong timing in both directions — and re-queueing inside the
        provider's window just earns another 429 (#271).
        """
        return _retry_after_seconds((self.response_headers or {}).get("retry-after"))


# ──────────────────────────────────────────────────────────────────────────────
# Abstract base
# ──────────────────────────────────────────────────────────────────────────────


class BaseBSPAdapter(BaseChannelAdapter, ABC):
    """
    Abstract base class that every BSP adapter must implement.

    Extends BaseChannelAdapter for the shared send_text/send_media/send_keyboard
    contract, and adds WhatsApp-BSP-specific template and webhook operations.

    Sub-classes are initialised with a WAApp instance which carries the BSP
    credentials (token, waba_id, bsp_credentials JSON, etc.).
    """

    # Canonical channel identifier (BaseChannelAdapter attribute).
    platform = PlatformChoices.WHATSAPP

    # Human-readable name shown in logs / API responses.
    PROVIDER_NAME: str = "base"

    # BSP-level capability strings (``"templates"``, ``"subscriptions"``,
    # ``"media_upload"``) live in ``capabilities.extra`` — the free-form slot
    # on the ``Capabilities`` dataclass inherited from ``BaseChannelAdapter``.
    # ``CAPABILITIES`` and ``supports()`` below are back-compat shims; new
    # code should read ``adapter.capabilities.extra`` directly.

    def __init__(self, wa_app: "WAApp") -> None:
        self.wa_app = wa_app

    # ── Capability introspection ──────────────────────────────────────────

    @property
    def CAPABILITIES(self) -> frozenset[str]:
        """Back-compat: BSP-level features stored in ``capabilities.extra``."""
        return self.capabilities.extra

    def supports(self, capability: str) -> bool:
        """Return ``True`` if this adapter supports the given BSP capability."""
        return capability in self.capabilities.extra

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

    # ── Account information ──────────────────────────────────────────────

    @abstractmethod
    def fetch_waba_info(self) -> AdapterResult:
        """
        Fetch account + phone-number state for this WAApp from the BSP.

        ``data`` must be keyed by ``WABAInfo`` field names so the caller can
        write it without knowing which provider answered. Only keys the
        provider actually reports should be present — a missing key means
        "unknown", which is different from ``None`` meaning "reported empty",
        and lets provider-specific fields (Gupshup's ``docker_status``) stay
        untouched when the other provider answers.

        Recognised keys: ``account_status``, ``docker_status``,
        ``messaging_limit``, ``mm_lite_status``, ``ownership_type``, ``phone``,
        ``phone_quality``, ``throughput``, ``verified_name``, ``waba_id``,
        ``can_send_message``, ``errors``, ``additional_info``.

        This exists because the tier was previously readable only through a
        Gupshup-shaped parser, so on Meta Direct ``messaging_limit`` stayed
        NULL and every broadcast was capped at the conservative 50-recipient
        fallback (#267).
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

    # ── Sending ───────────────────────────────────────────────────────────
    #
    # Both send paths used to hand-roll this branch — ``broadcast/tasks.py``
    # and ``wa/tasks.py`` each picked an API client from ``wa_app.bsp`` and
    # each read the provider's message id out of the response themselves
    # (#265). They drifted: one documented Gupshup as returning
    # ``{"messages": [{"id": ...}]}`` and the other as ``{"messageId": ...}``,
    # for the same client. Both cannot be right, and whichever is wrong loses
    # the id silently — which disables the duplicate-send guard added in #271,
    # because that guard keys on the id being set.
    #
    # The adapter knows which provider it is; the caller does not. So the
    # response shape is the adapter's business, and each one is responsible
    # for its own.

    @abstractmethod
    def send_template(
        self,
        payload: dict,
        *,
        is_marketing: bool = False,
        template_type: str = "",
    ) -> AdapterResult:
        """
        Send a template message.

        ``payload`` is the provider-shaped body the caller has already built.
        ``template_type`` is the ``WATemplate.template_type``, for adapters
        that validate the send payload — it is not derivable from the body.

        On success ``data`` carries the normalised ids (see
        :meth:`_message_ids` for the contract every adapter fills in):

        ``message_id``
            The id to store and to match webhooks against. Never ``None`` on
            a successful send — an adapter that cannot find one fails instead,
            because a send with no id cannot be deduplicated or tracked.
        ``cloud_api_message_id`` / ``provider_message_id``
            The individual ids, where the provider returns both, so a webhook
            carrying either can still be resolved.
        """
        ...

    @abstractmethod
    def send_session_message(self, payload: dict) -> AdapterResult:
        """
        Send a free-form (session) message inside the service window.

        Same ``data`` contract as :meth:`send_template`.
        """
        ...

    @staticmethod
    def _message_ids(cloud_api_id: str | None, provider_id: str | None) -> dict:
        """Normalise a provider's ids into the shape callers read.

        The Cloud API id (``wamid.…``) is preferred as the primary because it
        is what webhooks normally carry. A provider that returns only its own
        id supplies that instead, so ``message_id`` is set either way.
        """
        return {
            "message_id": cloud_api_id or provider_id,
            "cloud_api_message_id": cloud_api_id,
            "provider_message_id": provider_id,
        }

    @staticmethod
    def _response_headers(source: Any) -> Dict[str, str]:
        """Pull response headers off whatever the HTTP client handed back.

        Both clients raise on a non-2xx and carry the ``requests`` response on
        the exception as ``.response`` (the convention ``requests.HTTPError``
        itself uses), which is the only place a 429's ``Retry-After`` survives
        — everything after that point is a message string.

        Lives on the base rather than in each adapter so both fill the field in
        the same way: the caller honouring the interval must not have to know
        which provider it is talking to (#265, #271).
        """
        response = getattr(source, "response", None)
        headers = getattr(response if response is not None else source, "headers", None)
        if not headers:
            return {}
        try:
            return {str(key).lower(): str(value) for key, value in dict(headers).items()}
        except Exception:
            # A client that puts something other than a mapping on ``.headers``
            # is not worth failing a send over.
            return {}

    # ── BaseChannelAdapter contract ───────────────────────────────────────
    #
    # These stay unimplemented, deliberately. ``WHATSAPP`` is registered in
    # the channel registry (``wa/apps.py``), so ``get_channel_adapter
    # ("WHATSAPP", tenant).send_text(...)`` resolves an adapter and then
    # raises — but nothing in the repo calls it: the only real caller of the
    # registry asks for ``"SMS"``, and the paths #265 named as reaching this
    # (``voice/fallback.py``, ``mcp_server/tools/messaging.py``) both use
    # ``SMSMessageSender`` instead. So this is latent, not live.
    #
    # Implementing them means a per-provider payload builder — the Cloud API
    # and Gupshup's session endpoint take different bodies — which belongs
    # with whoever adds the first caller and knows what shape they need.
    # :meth:`send_session_message` is the method that works today.

    def send_text(self, chat_id: str, text: str, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError(
            f"{self.PROVIDER_NAME} adapter does not implement send_text(). "
            "Build a provider-shaped payload and call send_session_message() instead."
        )

    def send_media(self, chat_id, media_type, media_url, caption=None, **kwargs):
        raise NotImplementedError(
            f"{self.PROVIDER_NAME} adapter does not implement send_media(). "
            "Build a provider-shaped payload and call send_session_message() instead."
        )

    def send_keyboard(self, chat_id, text, keyboard, **kwargs):
        raise NotImplementedError(
            f"{self.PROVIDER_NAME} adapter does not implement send_keyboard(). "
            "Build a provider-shaped payload and call send_session_message() instead."
        )

    def get_channel_name(self) -> str:
        return "WHATSAPP"

    # ── CTWA (#192) ───────────────────────────────────────────────────────

    def parse_referral(self, raw_webhook_payload: dict) -> "CtwaReferral | None":
        """Return a normalised :class:`CtwaReferral` if the inbound webhook
        carries a CTWA referral, or ``None`` if absent.

        Default behaviour: return ``None`` (no CTWA support). BSPs that
        expose the ``referral`` field override this and set
        ``capabilities.supports_ctwa_referral=True``.

        Never raise — missing fields are surfaced as empty strings on
        the dataclass; the caller persists what's available and lets
        downstream attribution downgrade match quality.
        """
        return None

    # ── Helpers ───────────────────────────────────────────────────────────

    def _log(self, level: str, msg: str, **kwargs: Any) -> None:
        """Convenience logger that prefixes the provider name."""
        getattr(logger, level)(
            f"[{self.PROVIDER_NAME}] {msg}",
            **kwargs,
        )
