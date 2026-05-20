"""Thin Meta Graph API client used by the ``meta/`` and ``ads/`` apps.

Stubbed where it matters until #190 Meta App Review approves the
relevant scopes. Production fills in the actual requests; the call
surface stays the same so swapping the stub is a one-class change.

All callers go through this client so we can wire the per-tenant BUC
token-bucket rate limiter (#196) in one place.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MetaAuthExpired(Exception):
    """Raised on Meta 190-series errors so callers can set
    ``MetaBusinessConnection.needs_reauth=True``."""


class MetaApiClient:
    """Façade over Meta Graph API.

    Each method here is a 1:1 wrapper around a Graph API endpoint.
    The actual HTTP call is delegated to :meth:`_call`, which is the
    seam tests and the BUC rate limiter hook into.
    """

    GRAPH_API_VERSION = "v22.0"
    BASE_URL = "https://graph.facebook.com"

    def __init__(self, connection):
        self.connection = connection

    # ── Public surface ───────────────────────────────────────────────────

    def owned_whatsapp_business_accounts(self) -> list[dict]:
        """``GET /me/businesses/{biz_id}/owned_whatsapp_business_accounts``.

        Returns the list of WABAs the connection's Meta Business Account
        owns. Used by the WABA-link detection step in the campaign
        wizard.
        """
        path = f"/{self.connection.meta_business_id}/owned_whatsapp_business_accounts"
        resp = self._call("GET", path)
        return resp.get("data", []) if isinstance(resp, dict) else []

    def asterisk_passthrough(self, *args, **kwargs):  # pragma: no cover
        """Placeholder for the eventual passthrough to non-Marketing-API
        calls (recordings, etc). Not part of CTWA scope."""
        raise NotImplementedError

    # ── Marketing API: campaign lifecycle (#196) ─────────────────────────

    def create_campaign(self, *, ad_account_id: str, payload: dict) -> dict:
        """``POST /act_{ad_account_id}/campaigns``."""
        return self._call("POST", f"/act_{ad_account_id}/campaigns", json_body=payload)

    def create_adset(self, *, ad_account_id: str, payload: dict) -> dict:
        return self._call("POST", f"/act_{ad_account_id}/adsets", json_body=payload)

    def create_ad_creative(self, *, ad_account_id: str, payload: dict) -> dict:
        return self._call("POST", f"/act_{ad_account_id}/adcreatives", json_body=payload)

    def create_ad(self, *, ad_account_id: str, payload: dict) -> dict:
        return self._call("POST", f"/act_{ad_account_id}/ads", json_body=payload)

    def pause_ad(self, *, ad_id: str) -> dict:
        return self._call("POST", f"/{ad_id}", json_body={"status": "PAUSED"})

    def resume_ad(self, *, ad_id: str) -> dict:
        return self._call("POST", f"/{ad_id}", json_body={"status": "ACTIVE"})

    def ad_insights(self, *, ad_id: str, since: str, until: str) -> dict:
        return self._call(
            "GET",
            f"/{ad_id}/insights",
            params={"time_range": {"since": since, "until": until}},
        )

    # ── Internals ────────────────────────────────────────────────────────

    def _call(self, method: str, path: str, *, params=None, json_body=None) -> Any:
        """Single seam for HTTP. Stub in this PR — production fills in
        the actual ``requests`` call + retries + ``X-Business-Use-Case-Usage``
        header parsing + rate-limit bucket update.

        Returns an empty dict so callers don't crash during tests.
        Production replaces this body with an authenticated call.
        """
        # Don't log the body or its values — once production wires
        # the real call this would expose tokens / PII. ``has_body``
        # is the only useful operator signal at this layer. (#201
        # second review Low #9)
        logger.info(
            "[meta.client] STUB %s %s params=%s has_body=%s",
            method,
            path,
            params,
            json_body is not None,
        )
        return {}


__all__ = ["MetaApiClient", "MetaAuthExpired"]
