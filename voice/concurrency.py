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


# Atomic acquire script: increment, set TTL on the *current* key value
# (always, so a long-running campaign keeps the slot fresh), then check
# the cap. If exceeded, roll back atomically. Returns 1 on success, 0
# on cap-reached. (#179 review — splitting INCR + EXPIRE leaves the
# key without TTL if the worker dies between the two round-trips, and
# the per-call DECR rollback can race.)
_ACQUIRE_LUA = """
local v = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
if v > tonumber(ARGV[1]) then
    redis.call('DECR', KEYS[1])
    return 0
end
return 1
"""


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
    try:
        result = client.eval(_ACQUIRE_LUA, 1, key, max_concurrent, SEMAPHORE_TTL_SECONDS)
        return bool(int(result))
    except Exception:  # noqa: BLE001 — defensive; degrade to non-atomic path
        logger.exception("[voice.concurrency] Lua acquire failed for %s; falling back to non-atomic", key)
        new_value = client.incr(key)
        if new_value == 1:
            client.expire(key, SEMAPHORE_TTL_SECONDS)
        if new_value > max_concurrent:
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
