"""
Tests for #266: Atomic connection counting in WebSocketSecurityManager.
Verifies check_connection_limits and decrement_connection_count use
atomic cache operations to prevent race conditions.
"""

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from team_inbox.security import WebSocketSecurityManager

User = get_user_model()


class ConnectionLimitTests(TestCase):
    """Verify atomic connection counting."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="conn_user", email="conn@t.com",
            mobile="+910000088881", password="testpass123",
        )

    def setUp(self):
        self.manager = WebSocketSecurityManager()
        self.manager.max_connections_per_user = 3
        cache.clear()

    def tearDown(self):
        cache.clear()

    # ── check_connection_limits ──────────────────────────────────

    def test_allows_under_limit(self):
        """Connections under the limit are allowed."""
        result = async_to_sync(self.manager.check_connection_limits)(self.user)
        self.assertTrue(result)

    def test_allows_up_to_limit(self):
        """Exactly max_connections are allowed."""
        for _ in range(3):
            result = async_to_sync(self.manager.check_connection_limits)(self.user)
            self.assertTrue(result)

    def test_rejects_over_limit(self):
        """Connection beyond the limit is rejected."""
        for _ in range(3):
            async_to_sync(self.manager.check_connection_limits)(self.user)
        result = async_to_sync(self.manager.check_connection_limits)(self.user)
        self.assertFalse(result)

    def test_reject_does_not_increment_count(self):
        """A rejected connection doesn't inflate the counter."""
        for _ in range(3):
            async_to_sync(self.manager.check_connection_limits)(self.user)
        # Attempt over-limit
        async_to_sync(self.manager.check_connection_limits)(self.user)
        # Counter should still be 3, not 4
        cache_key = f"ws_connections:{self.user.id}"
        self.assertEqual(cache.get(cache_key), 3)

    def test_counter_increments_atomically(self):
        """Each allowed connection increments the counter by exactly 1."""
        async_to_sync(self.manager.check_connection_limits)(self.user)
        async_to_sync(self.manager.check_connection_limits)(self.user)
        cache_key = f"ws_connections:{self.user.id}"
        self.assertEqual(cache.get(cache_key), 2)

    # ── decrement_connection_count ───────────────────────────────

    def test_decrement_reduces_count(self):
        """Disconnecting decrements the counter."""
        async_to_sync(self.manager.check_connection_limits)(self.user)
        async_to_sync(self.manager.check_connection_limits)(self.user)
        async_to_sync(self.manager.decrement_connection_count)(self.user)
        cache_key = f"ws_connections:{self.user.id}"
        self.assertEqual(cache.get(cache_key), 1)

    def test_decrement_to_zero_cleans_up(self):
        """Decrementing to zero deletes the cache key."""
        async_to_sync(self.manager.check_connection_limits)(self.user)
        async_to_sync(self.manager.decrement_connection_count)(self.user)
        cache_key = f"ws_connections:{self.user.id}"
        self.assertIsNone(cache.get(cache_key))

    def test_decrement_without_key_is_safe(self):
        """Decrementing when no key exists doesn't raise."""
        # Should not raise
        async_to_sync(self.manager.decrement_connection_count)(self.user)

    def test_slot_freed_after_disconnect(self):
        """After disconnect, a new connection is allowed again."""
        for _ in range(3):
            async_to_sync(self.manager.check_connection_limits)(self.user)
        # At limit — next should fail
        self.assertFalse(
            async_to_sync(self.manager.check_connection_limits)(self.user)
        )
        # Disconnect one
        async_to_sync(self.manager.decrement_connection_count)(self.user)
        # Now one slot is free
        self.assertTrue(
            async_to_sync(self.manager.check_connection_limits)(self.user)
        )
