"""
BaseWebhookHandler tests
========================

Verifies the enforced flow:

  bad signature           → 403, no DB read, no idempotency claim
  duplicate idempotency   → 200 silent, handle_verified NOT invoked
  fresh request           → handle_verified IS invoked
  missing abstract method → TypeError at instantiation
  TTL + key prefix        → forwarded to Redis SETNX call

We patch ``redis.from_url`` rather than running against a live Redis so
the tests don't depend on infrastructure.

HOW TO RUN:
    DJANGO_SETTINGS_MODULE=jina_connect.settings python -m pytest abstract/tests/test_webhooks.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from abstract.webhooks import BaseWebhookHandler

# ─────────────────────────────────────────────────────────────────────────────
# Concrete test handlers
# ─────────────────────────────────────────────────────────────────────────────


class _PassHandler(BaseWebhookHandler):
    """Handler whose signature check always passes."""

    handled = False
    handled_request = None

    def verify_signature(self, request):
        return True

    def get_idempotency_key(self, request):
        return "fixed-key"

    def handle_verified(self, request):
        type(self).handled = True
        type(self).handled_request = request
        return HttpResponse("ok")


class _FailHandler(BaseWebhookHandler):
    """Handler whose signature check always fails."""

    handle_verified_was_called = False

    def verify_signature(self, request):
        return False

    def get_idempotency_key(self, request):
        return "should-not-matter"

    def handle_verified(self, request):
        type(self).handle_verified_was_called = True
        return HttpResponse("should not reach here")


class _NoKeyHandler(BaseWebhookHandler):
    """Handler that opts out of idempotency (returns None)."""

    times_handled = 0

    def verify_signature(self, request):
        return True

    def get_idempotency_key(self, request):
        return None

    def handle_verified(self, request):
        type(self).times_handled += 1
        return HttpResponse("no-key")


class _CustomTTLHandler(BaseWebhookHandler):
    idempotency_ttl_seconds = 60
    redis_key_prefix = "webhook:idempotency:custom"

    def verify_signature(self, request):
        return True

    def get_idempotency_key(self, request):
        return "custom-key"

    def handle_verified(self, request):
        return HttpResponse("custom")


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class BaseWebhookHandlerFlowTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        # Reset class-level test recorders
        _PassHandler.handled = False
        _PassHandler.handled_request = None
        _FailHandler.handle_verified_was_called = False
        _NoKeyHandler.times_handled = 0

    @patch("abstract.webhooks.redis.from_url")
    def test_bad_signature_returns_403_and_skips_handler(self, redis_from_url):
        """Bad signature short-circuits with 403 and never claims a key."""
        response = _FailHandler.as_view()(self.factory.post("/wh/"))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(_FailHandler.handle_verified_was_called)
        # No Redis call should have happened.
        redis_from_url.assert_not_called()

    @patch("abstract.webhooks.redis.from_url")
    def test_fresh_request_runs_handler(self, redis_from_url):
        """Signature passes + key is fresh ⇒ handle_verified runs."""
        client = MagicMock()
        client.set.return_value = True  # SETNX succeeded (key was new)
        redis_from_url.return_value = client

        response = _PassHandler.as_view()(self.factory.post("/wh/"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertTrue(_PassHandler.handled)
        # Redis SET called with nx=True and the configured TTL.
        client.set.assert_called_once()
        args, kwargs = client.set.call_args
        self.assertEqual(args[0], "webhook:idempotency:fixed-key")
        self.assertEqual(args[1], "1")
        self.assertEqual(kwargs.get("ex"), 86400)
        self.assertTrue(kwargs.get("nx"))

    @patch("abstract.webhooks.redis.from_url")
    def test_duplicate_key_returns_200_silently(self, redis_from_url):
        """Duplicate idempotency key ⇒ 200, handle_verified does NOT run."""
        client = MagicMock()
        client.set.return_value = None  # SETNX returns None when key exists
        redis_from_url.return_value = client

        response = _PassHandler.as_view()(self.factory.post("/wh/"))

        self.assertEqual(response.status_code, 200)
        # No body content from handle_verified (it never ran).
        self.assertEqual(response.content, b"")
        self.assertFalse(_PassHandler.handled)

    @patch("abstract.webhooks.redis.from_url")
    def test_none_idempotency_key_skips_redis(self, redis_from_url):
        """Returning None from get_idempotency_key bypasses the Redis claim."""
        response = _NoKeyHandler.as_view()(self.factory.post("/wh/"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_NoKeyHandler.times_handled, 1)
        redis_from_url.assert_not_called()

    @patch("abstract.webhooks.redis.from_url")
    def test_subclass_overrides_ttl_and_prefix(self, redis_from_url):
        """Subclass-level overrides flow through to the Redis call."""
        client = MagicMock()
        client.set.return_value = True
        redis_from_url.return_value = client

        _CustomTTLHandler.as_view()(self.factory.post("/wh/"))

        args, kwargs = client.set.call_args
        self.assertEqual(args[0], "webhook:idempotency:custom:custom-key")
        self.assertEqual(kwargs.get("ex"), 60)


class BaseWebhookHandlerEnforcementTests(TestCase):
    def test_instantiating_without_abstract_methods_raises(self):
        """A subclass missing any abstract method cannot be instantiated."""

        class Incomplete(BaseWebhookHandler):
            # Intentionally missing verify_signature / get_idempotency_key.
            def handle_verified(self, request):
                return HttpResponse("nope")

        with self.assertRaises(TypeError):
            Incomplete()

    def test_csrf_exempt_class_decorator_applied(self):
        """dispatch is csrf_exempt at the class level."""
        # csrf_exempt sets ``csrf_exempt = True`` on the view function.
        # Calling as_view() returns the wrapped callable.
        view = _PassHandler.as_view()
        self.assertTrue(getattr(view, "csrf_exempt", False))
