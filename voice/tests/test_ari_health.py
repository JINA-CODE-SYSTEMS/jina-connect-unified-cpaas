"""B1 (#181): Asterisk ARI health endpoint.

Covers the four states the frontend SIP wizard cares about:

  * ARI URL not configured → 503 with a clear reason.
  * ARI configured but unreachable (connection refused, etc.) → 503.
  * ARI configured but returns an error (auth fail, 500) → 503.
  * Happy path → 200 with ``asterisk_version`` + ``endpoints_registered``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser, TenantVoiceApp


@pytest.fixture()
def tenant(db):
    return Tenant.objects.create(name="AriHealthTenant")


@pytest.fixture()
def voice_app(tenant):
    return TenantVoiceApp.objects.create(tenant=tenant, is_enabled=True)


@pytest.fixture()
def user_client(db, tenant, voice_app):
    from django.contrib.auth import get_user_model

    role = TenantRole.objects.get(tenant=tenant, slug="agent")
    user = get_user_model().objects.create_user(
        username="ari_health_user",
        email="arih@t.io",
        mobile="+919600007001",
        password="x",
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


URL = "/voice/v1/api/ari-health/"


class TestAriHealth:
    def test_unauthenticated_blocked(self, db, voice_app):
        resp = APIClient().get(URL)
        assert resp.status_code in (401, 403)

    def test_tenant_without_voice_forbidden(self, db, tenant):
        # No TenantVoiceApp at all on this tenant.
        from django.contrib.auth import get_user_model

        role = TenantRole.objects.get(tenant=tenant, slug="agent")
        user = get_user_model().objects.create_user(
            username="no_voice_user",
            email="nv@t.io",
            mobile="+919600007002",
            password="x",
        )
        TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)
        c = APIClient()
        c.force_authenticate(user=user)
        resp = c.get(URL)
        assert resp.status_code == 403

    def test_returns_503_when_ari_url_unset(self, user_client, settings):
        settings.ASTERISK_ARI_URL = ""
        resp = user_client.get(URL)
        assert resp.status_code == 503
        body = resp.json()
        assert body["ok"] is False
        assert "ASTERISK_ARI_URL" in body["reason"]

    def test_returns_503_on_connection_error(self, user_client, settings):
        settings.ASTERISK_ARI_URL = "http://127.0.0.1:18088"
        with patch(
            "voice.sip_config.ari_client.AriClient.asterisk_info",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            resp = user_client.get(URL)
        assert resp.status_code == 503
        assert resp.json()["ok"] is False

    def test_returns_503_on_ari_error(self, user_client, settings):
        from voice.sip_config.ari_client import AriError

        settings.ASTERISK_ARI_URL = "http://127.0.0.1:18088"
        with patch(
            "voice.sip_config.ari_client.AriClient.asterisk_info",
            side_effect=AriError(401, "unauthorized"),
        ):
            resp = user_client.get(URL)
        assert resp.status_code == 503
        body = resp.json()
        assert body["ok"] is False
        assert "401" in body["reason"]

    def test_happy_path_hides_endpoint_count_for_non_staff(self, user_client, settings):
        # Non-staff users get the boolean + version only; the box-wide
        # endpoints_registered count is staff-gated to prevent
        # cross-tenant inference. (#185 review)
        settings.ASTERISK_ARI_URL = "http://127.0.0.1:8088"
        info = {"system": {"version": "20.6.0"}}
        endpoints = [{"technology": "PJSIP", "resource": "ep-a"}, {"technology": "PJSIP", "resource": "ep-b"}]
        with (
            patch("voice.sip_config.ari_client.AriClient.asterisk_info", return_value=info),
            patch("voice.sip_config.ari_client.AriClient.list_endpoints", return_value=endpoints),
        ):
            resp = user_client.get(URL)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body == {"ok": True, "asterisk_version": "20.6.0"}
        assert "endpoints_registered" not in body

    def test_happy_path_exposes_endpoint_count_for_staff(self, db, tenant, voice_app, settings):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient as _APIClient

        role = TenantRole.objects.get(tenant=tenant, slug="agent")
        user = get_user_model().objects.create_user(
            username="ari_staff",
            email="aris@t.io",
            mobile="+919600007003",
            password="x",
            is_staff=True,
        )
        TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)
        c = _APIClient()
        c.force_authenticate(user=user)
        settings.ASTERISK_ARI_URL = "http://127.0.0.1:8088"
        with (
            patch(
                "voice.sip_config.ari_client.AriClient.asterisk_info",
                return_value={"system": {"version": "20.6.0"}},
            ),
            patch(
                "voice.sip_config.ari_client.AriClient.list_endpoints",
                return_value=[{"resource": "a"}, {"resource": "b"}],
            ),
        ):
            resp = c.get(URL)
        assert resp.status_code == 200
        body = resp.json()
        assert body["endpoints_registered"] == 2

    def test_happy_path_version_fallback(self, user_client, settings):
        # Some Asterisk builds expose ``version`` at the top level rather
        # than under ``system`` — the view should fall back gracefully.
        settings.ASTERISK_ARI_URL = "http://127.0.0.1:8088"
        with (
            patch(
                "voice.sip_config.ari_client.AriClient.asterisk_info",
                return_value={"version": "18.20.0"},
            ),
            patch("voice.sip_config.ari_client.AriClient.list_endpoints", return_value=[]),
        ):
            resp = user_client.get(URL)
        assert resp.status_code == 200
        assert resp.json()["asterisk_version"] == "18.20.0"
        assert "endpoints_registered" not in resp.json()
