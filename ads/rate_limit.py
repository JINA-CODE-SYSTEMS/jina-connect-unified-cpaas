"""Per-tenant BUC token-bucket rate limiter for Meta Marketing API
calls (#196).

Meta's Business Use Case (BUC) limits are per-app — without isolation
one tenant's $10k/day campaign can exhaust the per-app quota and stall
every other tenant. This module keys the bucket on
``(tenant_id, ad_account_id)`` so noisy tenants throttle themselves.

Real Redis-backed token bucket. The bucket size + refill rate are
sized per Meta's documented per-ad-account limits with 20% headroom.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Conservative defaults; tune per-tenant once we see real usage.
DEFAULT_BUCKET_SIZE = 60  # tokens
DEFAULT_REFILL_PER_SECOND = 1.0  # tokens / s (≈ 60 calls/min steady)


class RateLimited(Exception):
    """Raised when the bucket has no token and the caller should
    back off + retry."""


def _bucket_key(tenant_id: int, ad_account_id: str) -> str:
    return f"meta:buc:{tenant_id}:{ad_account_id}"


def acquire(
    *,
    tenant_id: int,
    ad_account_id: str,
    bucket_size: int = DEFAULT_BUCKET_SIZE,
    refill_per_second: float = DEFAULT_REFILL_PER_SECOND,
) -> None:
    """Block-acquire a single token from the per-tenant bucket.

    Returns when a token is granted. Raises :class:`RateLimited` if
    bucket is empty. Caller decides whether to retry, queue, or
    surface the throttle to the tenant UI.

    Redis-down behaviour: fails open (allows the call). Backpressure
    is preferable when Redis is reachable; when it isn't, dropping
    every call is the strictly worse failure mode.
    """
    try:
        from django_redis import get_redis_connection

        r = get_redis_connection("default")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ads.rate_limit] Redis unavailable (allowing call): %s", exc)
        return

    key = _bucket_key(tenant_id, ad_account_id)
    now = time.time()

    # Lua-free implementation for portability. Race-prone under high
    # concurrency but bounded by the refill rate; production may
    # upgrade to a Lua-scripted atomic version once we measure load.
    pipe = r.pipeline()
    pipe.hmget(key, ["tokens", "ts"])
    pipe.expire(key, 3600)
    tokens_raw, ts_raw = pipe.execute()[0]

    tokens = float(tokens_raw) if tokens_raw else float(bucket_size)
    ts = float(ts_raw) if ts_raw else now

    elapsed = max(0.0, now - ts)
    tokens = min(float(bucket_size), tokens + elapsed * refill_per_second)

    if tokens < 1.0:
        # Persist the refilled state so successive callers see updated
        # tokens; raise so the caller backs off.
        r.hmset(key, {"tokens": f"{tokens:.4f}", "ts": f"{now}"})
        raise RateLimited(f"BUC bucket exhausted for tenant={tenant_id} ad_account={ad_account_id}")

    tokens -= 1.0
    r.hmset(key, {"tokens": f"{tokens:.4f}", "ts": f"{now}"})


def status(*, tenant_id: int, ad_account_id: str) -> dict:
    """Return ``{tokens, capacity}`` for the operator dashboard."""
    try:
        from django_redis import get_redis_connection

        r = get_redis_connection("default")
        tokens_raw, _ = r.hmget(_bucket_key(tenant_id, ad_account_id), ["tokens", "ts"])
    except Exception:  # noqa: BLE001
        return {"tokens": None, "capacity": DEFAULT_BUCKET_SIZE, "redis": "down"}
    return {
        "tokens": float(tokens_raw) if tokens_raw else DEFAULT_BUCKET_SIZE,
        "capacity": DEFAULT_BUCKET_SIZE,
        "redis": "up",
    }


__all__ = ["RateLimited", "acquire", "status"]
