"""Per-config concurrency semaphore (#162).

Caps simultaneous in-flight calls per ``VoiceProviderConfig`` so a single
broadcast doesn't blow past the provider's per-account concurrency limit
(``max_concurrent_calls`` on the config).

Uses Redis INCR + a TTL key per slot. ``acquire`` returns ``True`` if
there's room; ``release`` decrements. The semaphore is intentionally
*soft* — a hard-crashed Celery worker that doesn't release just consumes
its slot until the TTL expires, which is the right failure mode for a
provider concurrency cap.
"""

from __future__ import annotations

import logging
from uuid import UUID

from abstract.webhooks import _get_redis_client

logger = logging.getLogger(__name__)


def _key(tenant_id: "UUID | str", provider_config_id: "UUID | str") -> str:
    return f"voice:concurrency:{tenant_id}:{provider_config_id}"


# A call shouldn't realistically be in flight longer than the
# ``VOICE_MAX_CALL_DURATION_SECONDS`` setting (default 3600). The
# semaphore key gets a slightly longer TTL so a slow drain doesn't
# orphan a slot but a crashed worker can't lock it forever.
SEMAPHORE_TTL_SECONDS = 4200


def acquire(tenant_id, provider_config_id, max_concurrent: int) -> bool:
    """Try to take one in-flight slot.

    Returns ``True`` on success (semaphore incremented), ``False`` when
    the configured ceiling has been reached.
    """
    if max_concurrent <= 0:
        # Misconfiguration — assume unlimited rather than block voice
        # entirely.
        return True

    key = _key(tenant_id, provider_config_id)
    client = _get_redis_client()
    new_value = client.incr(key)
    if new_value == 1:
        # Fresh key — set TTL so a stuck slot eventually frees.
        client.expire(key, SEMAPHORE_TTL_SECONDS)
    if new_value > max_concurrent:
        # Roll back — we exceeded the cap.
        client.decr(key)
        return False
    return True


def release(tenant_id, provider_config_id) -> None:
    """Free one in-flight slot.

    Idempotent: calling release more times than acquire just drives the
    counter to zero (and Redis won't go negative thanks to INCR/DECR
    behaviour around expiration — but we guard anyway).
    """
    key = _key(tenant_id, provider_config_id)
    client = _get_redis_client()
    value = client.decr(key)
    if value is not None and int(value) < 0:
        # Reset to zero rather than leave the counter negative.
        client.set(key, 0, ex=SEMAPHORE_TTL_SECONDS)


def current_count(tenant_id, provider_config_id) -> int:
    """Return the current semaphore count (mostly for tests / admin)."""
    key = _key(tenant_id, provider_config_id)
    client = _get_redis_client()
    val = client.get(key)
    if val is None:
        return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0
