"""
BaseWebhookHandler — shared base for incoming provider webhooks.

Every channel adapter that receives webhooks (WhatsApp BSPs, SMS providers,
Telegram bots, voice providers, etc.) eventually needs the same three
guarantees:

  1. **CSRF-exempt** — incoming POSTs from external providers don't carry
     a Django CSRF token.
  2. **Signature verification first** — the request body must be
     authenticated before any DB read or expensive parsing.
  3. **Idempotency** — providers retry aggressively; a webhook fingerprint
     is claimed via Redis ``SETNX`` so duplicate deliveries return ``200``
     silently without re-running side effects.

This base enforces the order so subclasses only fill in the
provider-specific parts (signature scheme, idempotency-key derivation,
the actual handler).

Subclasses MUST implement ``verify_signature``, ``get_idempotency_key``,
and ``handle_verified``. They MAY override ``idempotency_ttl_seconds`` or
``redis_key_prefix``.

Voice is the first user of this base; existing channels (``wa/``, ``sms/``,
``telegram/``, ``rcs/``) are not migrated in this PR — the base coexists.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import redis
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

# Module-level cache for the Redis client. redis.from_url() creates a new
# connection pool each call, which is wasteful under voice-grade QPS. The
# client is thread-safe so a single shared instance is fine. Tests reset
# this via ``_reset_redis_client_cache()``.
_REDIS_CLIENT: redis.Redis | None = None


def _get_redis_client() -> redis.Redis:
    """Return the shared Redis client, creating it on first use."""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.from_url(settings.REDIS_URL)
    return _REDIS_CLIENT


def _reset_redis_client_cache() -> None:
    """Test hook: drop the cached client so the next call rebuilds it."""
    global _REDIS_CLIENT
    _REDIS_CLIENT = None


@method_decorator(csrf_exempt, name="dispatch")
class BaseWebhookHandler(View, ABC):
    """
    Abstract base for incoming-webhook views.

    Enforced order of operations on ``POST``:

        1. ``verify_signature(request)``
           - Returns ``False`` ⇒ short-circuit with **403** (no DB read,
             no idempotency claim).
        2. ``get_idempotency_key(request)``
           - May return ``None`` to skip the idempotency check.
           - Otherwise we attempt a Redis ``SETNX`` with TTL.
           - On a duplicate (key already exists) ⇒ short-circuit with
             **200** so the provider stops retrying.
        3. ``handle_verified(request)``
           - Runs only when both checks pass. Should remain fast
             (queue Celery tasks for heavy work).

    Subclasses are CBVs, so route them via ``ConcreteHandler.as_view()``.
    """

    #: Seconds an idempotency key lives in Redis. Providers typically
    #: retry within minutes; 24 hours is a comfortable safety margin.
    idempotency_ttl_seconds: int = 86400

    #: Prefix for the Redis key. Subclasses can override to isolate
    #: namespaces (e.g. ``"webhook:idempotency:voice:twilio"``).
    redis_key_prefix: str = "webhook:idempotency"

    # ── Abstract surface for subclasses ──────────────────────────────────

    @abstractmethod
    def verify_signature(self, request: HttpRequest) -> bool:
        """Return ``True`` if the request's signature is valid for this provider."""
        ...

    @abstractmethod
    def get_idempotency_key(self, request: HttpRequest) -> str | None:
        """
        Return a deterministic key that uniquely identifies *this event*.

        Returning ``None`` disables the idempotency check (useful for
        endpoints where every request is genuinely unique, e.g. real-time
        media streams). Most concrete handlers should return a key.
        """
        ...

    @abstractmethod
    def handle_verified(self, request: HttpRequest) -> HttpResponse:
        """
        Handle the request *after* signature + idempotency pass.

        Keep this fast (p95 < 200ms is the provider-retry budget). Queue
        Celery tasks for anything heavier than parsing + ack.
        """
        ...

    # ── Enforced flow ────────────────────────────────────────────────────

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not self.verify_signature(request):
            logger.warning(
                "[%s] webhook signature verification failed",
                self.__class__.__name__,
            )
            return HttpResponseForbidden("invalid signature")

        key = self.get_idempotency_key(request)
        if key is not None and not self._claim_idempotency(key):
            logger.info(
                "[%s] duplicate webhook key=%s — replying 200 silently",
                self.__class__.__name__,
                key,
            )
            return HttpResponse(status=200)

        return self.handle_verified(request)

    # ── Internal helpers ─────────────────────────────────────────────────

    def _claim_idempotency(self, key: str) -> bool:
        """
        Atomically claim an idempotency key.

        Returns ``True`` if the key was newly claimed (first delivery),
        ``False`` if it already existed (duplicate). Uses Redis ``SET``
        with ``NX`` + ``EX``, which is atomic and TTL'd in one round-trip.
        """
        full_key = f"{self.redis_key_prefix}:{key}"
        client = _get_redis_client()
        # r.set(..., ex=ttl, nx=True) returns True on success, None if the
        # key already exists. Coerce to bool.
        claimed = client.set(full_key, "1", ex=self.idempotency_ttl_seconds, nx=True)
        return bool(claimed)
