"""Direct unit tests for Telegram rate limiter."""

from unittest.mock import MagicMock

import pytest

from telegram.services.rate_limiter import check_rate_limit


@pytest.mark.django_db
class TestTelegramRateLimiter:
    def test_under_limit_increments_counter(self, settings, monkeypatch):
        settings.PLATFORM_RATE_LIMITS = {"telegram": 2}

        fake_cache = MagicMock()
        fake_cache.incr.side_effect = [1, 2, 3]
        monkeypatch.setattr("telegram.services.rate_limiter.cache", fake_cache)

        assert check_rate_limit("bot-1") is True
        assert check_rate_limit("bot-1") is True
        assert check_rate_limit("bot-1") is False

        assert fake_cache.add.call_count == 3
        fake_cache.add.assert_called_with("tg_rate:bot-1", 0, timeout=60)

    def test_key_expiry_between_add_and_incr_recovers(self, settings, monkeypatch):
        settings.PLATFORM_RATE_LIMITS = {"telegram": 30}

        fake_cache = MagicMock()
        fake_cache.incr.side_effect = ValueError("Key not found")
        monkeypatch.setattr("telegram.services.rate_limiter.cache", fake_cache)

        assert check_rate_limit("bot-2") is True

        fake_cache.add.assert_called_once_with("tg_rate:bot-2", 0, timeout=60)
        fake_cache.set.assert_called_once_with("tg_rate:bot-2", 1, timeout=60)
