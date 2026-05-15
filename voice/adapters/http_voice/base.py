"""HttpVoiceAdapter — base for Twilio / Plivo / Vonage / Telnyx / Exotel adapters.

Provides shared scaffolding the per-provider subclasses (#160, #164, #165,
#166, #167) build on:

  * Stash a ``VoiceProviderConfig`` and lazy-decode its encrypted
    credentials.
  * A configured ``requests.Session`` with sensible timeouts/retries.
  * Helpers to map provider-native status / hangup-cause strings to the
    canonical ``CallStatus`` / ``HangupCause`` enums.

The class is still abstract — concrete adapters must implement the voice
API surface from ``VoiceAdapter``.
"""

from __future__ import annotations

import json
import logging
from abc import abstractmethod
from typing import Any

import requests

from voice.adapters.base import VoiceAdapter
from voice.constants import HangupCause

logger = logging.getLogger(__name__)


# Sensible defaults — providers retry aggressively, so don't sit on a
# slow connection for too long. Individual subclasses may override.
DEFAULT_TIMEOUT_SECONDS = 15


class HttpVoiceAdapter(VoiceAdapter):
    """Shared scaffolding for HTTP-API voice providers.

    Subclasses provide their provider-specific REST endpoints, auth
    scheme, and status / hangup-cause normalisation tables. The base
    handles HTTP session construction and credential decoding.
    """

    # ``BaseChannelAdapter`` carries platform = "" by default; ``VoiceAdapter``
    # sets it to PlatformChoices.VOICE. We don't touch it here.

    def __init__(self, provider_config) -> None:
        # ``provider_config`` is a ``voice.models.VoiceProviderConfig``.
        # We don't import the model class at module level to keep the
        # adapter package importable before Django apps are loaded.
        self.provider_config = provider_config
        self._session: requests.Session | None = None
        self._credentials_cache: dict[str, Any] | None = None

    # ── Credentials ──────────────────────────────────────────────────────

    @property
    def credentials(self) -> dict[str, Any]:
        """Decoded credentials dict.

        ``VoiceProviderConfig.credentials`` is an encrypted JSON-serialised
        string; this property decodes it once per adapter instance.
        """
        if self._credentials_cache is None:
            raw = self.provider_config.credentials or "{}"
            try:
                self._credentials_cache = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"Provider config {self.provider_config.id} has invalid JSON credentials") from e
        return self._credentials_cache

    # ── HTTP session ─────────────────────────────────────────────────────

    @property
    def session(self) -> requests.Session:
        """Shared ``requests.Session``. Cached per adapter instance."""
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Perform an HTTP request with the adapter's default timeout."""
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
        return self.session.request(method, url, **kwargs)

    # ── Status / cause normalisation surface (subclass-specific maps) ────

    #: Mapping from provider-native call status to our ``CallStatus``.
    #: Subclasses override.
    STATUS_MAP: dict[str, str] = {}

    #: Mapping from provider-native hangup cause to our ``HangupCause``.
    #: Subclasses override.
    HANGUP_CAUSE_MAP: dict[str, str] = {}

    def _normalize_status(self, provider_status: str | None) -> str | None:
        """Best-effort normalisation; unknown values return ``None``."""
        if provider_status is None:
            return None
        return self.STATUS_MAP.get(provider_status)

    def _normalize_hangup_cause(self, provider_cause: str | None) -> str:
        """Map provider hangup cause to canonical; unknown → UNKNOWN."""
        if provider_cause is None:
            return HangupCause.UNKNOWN
        return self.HANGUP_CAUSE_MAP.get(provider_cause, HangupCause.UNKNOWN)

    # ── Abstract — concrete adapters still must implement these ──────────

    @abstractmethod
    def verify_webhook(self, request) -> bool:
        """Verify the signature on an incoming webhook request.

        Each provider has its own signing scheme (Twilio HMAC-SHA1 of
        URL+params; Plivo HMAC-SHA512; Vonage JWT; Telnyx Ed25519;
        Exotel path-token). Subclasses implement.
        """
        ...
