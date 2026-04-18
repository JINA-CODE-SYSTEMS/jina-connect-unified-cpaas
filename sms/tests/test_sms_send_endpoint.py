"""Tests for SMS ad-hoc message send endpoint (#100)."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from sms.models import SMSApp


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name="SMS Send Tenant")


@pytest.fixture()
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="sms_send", email="smssend@test.com", mobile="+919600000001", password="testpass123"
    )


@pytest.fixture()
def role(tenant):
    from tenants.models import TenantRole

    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    return role


@pytest.fixture()
def tenant_user(tenant, user, role):
    from tenants.models import TenantUser

    return TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)


@pytest.fixture()
def sms_app(tenant):
    return SMSApp.objects.create(
        tenant=tenant,
        provider="TWILIO",
        sender_id="+14155550000",
        provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        is_active=True,
    )


@pytest.fixture()
def api_client(tenant_user):
    client = APIClient()
    client.force_authenticate(user=tenant_user.user)
    return client


def _fake_send_text(self, chat_id, text, **kwargs):
    return {"success": True, "message_id": "SM-FAKE", "outbound_id": "fake-uuid", "segments": 1, "error": None}


@pytest.mark.django_db
class TestSMSSendEndpoint:
    def test_send_text_success(self, api_client, sms_app, monkeypatch):
        """POST /sms/v1/messages/send/ returns 200 on success."""
        from sms.services.message_sender import SMSMessageSender

        monkeypatch.setattr(SMSMessageSender, "send_text", _fake_send_text)

        resp = api_client.post(
            "/sms/v1/messages/send/",
            {"phone": "+919200000001", "text": "Hello SMS"},
            format="json",
        )

        assert resp.status_code == 200
        assert resp.data["success"] is True

    def test_missing_text_returns_400(self, api_client, sms_app):
        """Phone without text fails validation."""
        resp = api_client.post(
            "/sms/v1/messages/send/",
            {"phone": "+919200000001"},
            format="json",
        )

        assert resp.status_code == 400

    def test_no_sms_app_returns_400(self, api_client, tenant):
        """If no active SMSApp exists, returns 400."""
        resp = api_client.post(
            "/sms/v1/messages/send/",
            {"phone": "+919200000001", "text": "hi"},
            format="json",
        )

        assert resp.status_code == 400
        assert "No active SMS app" in resp.data["error"]

    def test_unauthenticated_returns_401(self, sms_app):
        """Unauthenticated request returns 401."""
        client = APIClient()
        resp = client.post(
            "/sms/v1/messages/send/",
            {"phone": "+919200000001", "text": "hi"},
            format="json",
        )

        assert resp.status_code == 401
