"""
Tests for the /healthz probe.

Run with: python manage.py test jina_connect.tests.test_health

This endpoint feeds an SLA availability figure, so the failure paths matter
more than the happy one: a probe that reports "up" while a dependency is down
turns into a contractual claim that cannot be defended.
"""

from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse


class HealthzTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse("healthz")

    def test_returns_200_when_dependencies_respond(self):
        with patch("jina_connect.health._check_redis"):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["checks"], {"database": "ok", "redis": "ok"})

    def test_returns_503_when_redis_is_down(self):
        with patch("jina_connect.health._check_redis", side_effect=ConnectionError("refused")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["checks"]["redis"], "error")
        self.assertEqual(body["checks"]["database"], "ok")

    def test_returns_503_when_the_database_is_down(self):
        with (
            patch("jina_connect.health._check_database", side_effect=Exception("no connection")),
            patch("jina_connect.health._check_redis"),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["database"], "error")

    def test_failure_detail_is_not_disclosed(self):
        """The endpoint is unauthenticated, so failures are logged, not described."""
        secret = "postgres://user:hunter2@db.internal:5432/prod"

        with (
            patch("jina_connect.health._check_database", side_effect=Exception(secret)),
            patch("jina_connect.health._check_redis"),
        ):
            response = self.client.get(self.url)

        self.assertNotIn("hunter2", response.content.decode())
        self.assertNotIn("db.internal", response.content.decode())

    def test_response_is_not_cacheable(self):
        """A cached 200 would report "up" during an outage."""
        with patch("jina_connect.health._check_redis"):
            response = self.client.get(self.url)

        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))

    def test_requires_no_authentication(self):
        with patch("jina_connect.health._check_redis"):
            response = Client().get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_rejects_non_get_methods(self):
        """require_GET rejects a POST.

        The test client disables CSRF, so the view's own guard is what answers
        here. Against a running server CSRF middleware rejects the POST first
        and the caller sees 403 — verified manually. Either way it is not 200,
        which is all an uptime probe depends on.
        """
        with patch("jina_connect.health._check_redis"):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 405)
