"""Token-bucket tests (#196 + #201 review).

Tests patch ``ads.rate_limit._redis_connection`` (the in-module
indirection) rather than ``django_redis.get_redis_connection``
directly — ``django_redis`` isn't installed in CI today, only on the
prod server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ads.rate_limit import RateLimited, acquire


class TestRateLimiter:
    def test_redis_unavailable_fails_open(self):
        """If Redis isn't reachable, ``acquire`` returns silently —
        per module contract the rate limiter never blocks ingestion
        on infrastructure hiccups."""
        with patch("ads.rate_limit._redis_connection", side_effect=RuntimeError("redis down")):
            acquire(tenant_id=1, ad_account_id="act_1")

    def test_lua_eval_failure_fails_open(self):
        """Lua eval errors (cluster mode reject, etc.) also fail open."""
        mock_r = MagicMock()
        mock_r.eval.side_effect = RuntimeError("lua boom")
        with patch("ads.rate_limit._redis_connection", return_value=mock_r):
            acquire(tenant_id=1, ad_account_id="act_1")
        mock_r.eval.assert_called_once()

    def test_lua_returns_1_means_token_acquired(self):
        mock_r = MagicMock()
        mock_r.eval.return_value = 1
        with patch("ads.rate_limit._redis_connection", return_value=mock_r):
            acquire(tenant_id=1, ad_account_id="act_1")  # should not raise
        mock_r.eval.assert_called_once()

    def test_lua_returns_0_means_rate_limited(self):
        mock_r = MagicMock()
        mock_r.eval.return_value = 0
        with patch("ads.rate_limit._redis_connection", return_value=mock_r):
            with pytest.raises(RateLimited):
                acquire(tenant_id=1, ad_account_id="act_1")

    def test_call_carries_correct_args(self):
        # The Lua script takes (key, now, bucket_size, refill, ttl).
        # Args are positional; we assert the bucket_size + refill_per_second
        # passed by the caller make it into the eval call.
        mock_r = MagicMock()
        mock_r.eval.return_value = 1
        with patch("ads.rate_limit._redis_connection", return_value=mock_r):
            acquire(
                tenant_id=42,
                ad_account_id="act_42",
                bucket_size=120,
                refill_per_second=2.0,
            )
        call = mock_r.eval.call_args
        # eval(script, numkeys, key, now, bucket_size, refill, ttl)
        args = call.args
        assert args[1] == 1  # numkeys
        assert args[2] == "meta:buc:42:act_42"  # key
        assert args[4] == "120"  # bucket_size
        assert args[5] == "2.0"  # refill_per_second
        assert args[6] == "3600"  # ttl
