"""Per-tenant BUC token-bucket rate limiter for Meta Marketing API
calls (#196).

Meta's Business Use Case (BUC) limits are per-app — without isolation
one tenant's $10k/day campaign can exhaust the per-app quota and stall
every other tenant. This module keys the bucket on
``(tenant_id, ad_account_id)`` so noisy tenants throttle themselves.

Atomic via a Lua script — refill + decrement happen inside a single
Redis round-trip so two concurrent acquires can't both pass the
capacity check. (#201 review)

Redis-down behaviour: fails open (allows the call). Backpressure is
preferable when Redis is reachable; when it isn't, dropping every
call is the strictly worse failure mode.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Conservative defaults; tune per-tenant once we see real usage.
DEFAULT_BUCKET_SIZE = 60  # tokens
DEFAULT_REFILL_PER_SECOND = 1.0  # tokens / s (≈ 60 calls/min steady)

# Lua script — runs atomically inside Redis.
#   KEYS[1] = bucket key
#   ARGV[1] = unix timestamp (seconds, float)
#   ARGV[2] = bucket size
#   ARGV[3] = refill rate (tokens/sec)
#   ARGV[4] = TTL seconds
#
# Returns 1 if a token was acquired, 0 if the bucket was empty.
_LUA_ACQUIRE = """
local now = tonumber(ARGV[1])
local bucket_size = tonumber(ARGV[2])
local refill_per_sec = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = data[1] and tonumber(data[1]) or bucket_size
local ts = data[2] and tonumber(data[2]) or now

local elapsed = math.max(0, now - ts)
tokens = math.min(bucket_size, tokens + elapsed * refill_per_sec)

if tokens < 1 then
    redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
    redis.call('EXPIRE', KEYS[1], ttl)
    return 0
end

tokens = tokens - 1
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)
return 1
"""


class RateLimited(Exception):
    """Raised when the bucket has no token and the caller should
    back off + retry."""


def _bucket_key(tenant_id: int, ad_account_id: str) -> str:
    return f"meta:buc:{tenant_id}:{ad_account_id}"


def _redis_connection():
    """Indirection layer so tests can patch the Redis client without
    needing ``django_redis`` installed locally. CI runs without
    ``django_redis`` (it's only present on the prod server), so a
    test patching ``django_redis.get_redis_connection`` would fail
    at patch-resolution time. Tests patch ``_redis_connection``
    instead.
    """
    from django_redis import get_redis_connection

    return get_redis_connection("default")


def acquire(
    *,
    tenant_id: int,
    ad_account_id: str,
    bucket_size: int = DEFAULT_BUCKET_SIZE,
    refill_per_second: float = DEFAULT_REFILL_PER_SECOND,
) -> None:
    """Atomically acquire a single token from the per-tenant bucket.

    Returns when a token is granted. Raises :class:`RateLimited` if
    the bucket is empty. Caller decides whether to retry, queue, or
    surface the throttle to the tenant UI.
    """
    try:
        r = _redis_connection()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ads.rate_limit] Redis unavailable (allowing call): %s", exc)
        return

    try:
        result = r.eval(
            _LUA_ACQUIRE,
            1,
            _bucket_key(tenant_id, ad_account_id),
            str(time.time()),
            str(bucket_size),
            str(refill_per_second),
            "3600",
        )
    except Exception as exc:  # noqa: BLE001
        # Redis returned an error (e.g. cluster mode reject); fail
        # open per module contract.
        logger.warning("[ads.rate_limit] Lua acquire failed (allowing call): %s", exc)
        return

    if int(result) != 1:
        raise RateLimited(f"BUC bucket exhausted for tenant={tenant_id} ad_account={ad_account_id}")


# The ``status()`` helper that v1 shipped reported ``DEFAULT_BUCKET_SIZE``
# as capacity regardless of what the caller passed to ``acquire()`` —
# misleading when per-tenant capacity overrides are in play.
# Dropped in favour of building an operator dashboard properly later
# (capacity stored alongside tokens in the bucket hash). (#201 second
# review Low #12)

__all__ = ["RateLimited", "acquire"]
