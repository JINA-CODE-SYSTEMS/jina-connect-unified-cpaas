"""REST endpoints + admin masking tests (#174).

Covers:

  * Auth/permission: ``IsVoiceEnabledForTenant`` blocks tenants
    without ``TenantVoiceApp.is_enabled``; ``IsVoiceAdmin`` keeps
    non-staff out of provider-config / rate-card endpoints.
  * Tenant scoping: cross-tenant access returns 404, never 403.
  * Credential redaction: write-only on the serializer, never in
    the response.
  * Each ViewSet exercises its happy path (list / retrieve / custom
    action) with a mocked adapter / storage so we don't hit real
    providers.
  * ``initiate`` validates inputs; ``hangup`` invokes the adapter;
    ``download`` returns a signed URL with the requested TTL;
    ``preview`` renders the template.
  * Admin: ``VoiceProviderConfigAdmin.fieldsets`` / form has no
    ``credentials`` field (encrypted secret stays out of admin).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from contacts.models import TenantContact
from tenants.models import Tenant, TenantRole, TenantUser, TenantVoiceApp
from voice.constants import CallDirection, CallStatus, VoiceProvider
from voice.models import (
    RecordingConsent,
    VoiceCall,
    VoiceProviderConfig,
    VoiceRateCard,
    VoiceRecording,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tenant(db):
    return Tenant.objects.create(name="REST Voice Tenant")


@pytest.fixture()
def other_tenant(db):
    return Tenant.objects.create(name="REST Voice Other Tenant")


@pytest.fixture()
def admin_role(tenant):
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    return role


@pytest.fixture()
def agent_role(tenant):
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="agent", defaults={"name": "Agent", "priority": 10})
    return role


@pytest.fixture()
def admin_user(db, tenant, admin_role):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username="voice_admin",
        email="va@test.com",
        mobile="+919600000010",
        password="x",
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=admin_role, is_active=True)
    return user


@pytest.fixture()
def agent_user(db, tenant, agent_role):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username="voice_agent",
        email="vag@test.com",
        mobile="+919600000011",
        password="x",
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=agent_role, is_active=True)
    return user


@pytest.fixture()
def voice_app(tenant):
    return TenantVoiceApp.objects.create(tenant=tenant, is_enabled=True)


@pytest.fixture()
def config(tenant, voice_app):
    return VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="REST Twilio",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "secret-token-XYZ"}),
        from_numbers=["+14155550100"],
    )


@pytest.fixture()
def admin_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture()
def agent_client(agent_user):
    client = APIClient()
    client.force_authenticate(user=agent_user)
    return client


def _make_call(tenant, config, **extra):
    return VoiceCall.objects.create(
        tenant=tenant,
        name=extra.pop("name", "rest-call"),
        provider_config=config,
        provider_call_id=extra.pop("provider_call_id", "CA_rest"),
        direction=CallDirection.OUTBOUND,
        from_number="+14155550100",
        to_number=extra.pop("to_number", "+14155550199"),
        status=extra.pop("status", CallStatus.COMPLETED),
        duration_seconds=extra.pop("duration", 42),
        **extra,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Permission gates
# ─────────────────────────────────────────────────────────────────────────────


class TestVoicePermissions:
    def test_unauthenticated_blocked(self, db, voice_app):
        client = APIClient()
        resp = client.get("/voice/v1/api/calls/")
        assert resp.status_code in (401, 403)

    def test_tenant_without_voice_app_forbidden(self, db, tenant, admin_user, admin_role):
        # No TenantVoiceApp at all.
        client = APIClient()
        client.force_authenticate(user=admin_user)
        resp = client.get("/voice/v1/api/calls/")
        assert resp.status_code == 403

    def test_tenant_with_voice_disabled_forbidden(self, db, tenant, admin_user):
        TenantVoiceApp.objects.create(tenant=tenant, is_enabled=False)
        client = APIClient()
        client.force_authenticate(user=admin_user)
        resp = client.get("/voice/v1/api/calls/")
        assert resp.status_code == 403

    def test_voice_enabled_allows_call_listing(self, agent_client, voice_app):
        resp = agent_client.get("/voice/v1/api/calls/")
        assert resp.status_code == 200

    def test_non_admin_cannot_create_provider_config(self, agent_client, voice_app):
        resp = agent_client.post(
            "/voice/v1/api/provider-configs/",
            data={
                "name": "Cfg from agent",
                "provider": VoiceProvider.TWILIO,
                "credentials": {"account_sid": "AC", "auth_token": "T"},
            },
            format="json",
        )
        assert resp.status_code == 403

    def test_admin_can_create_provider_config(self, admin_client, voice_app):
        resp = admin_client.post(
            "/voice/v1/api/provider-configs/",
            data={
                "name": "Cfg from admin",
                "provider": VoiceProvider.TWILIO,
                "credentials": {"account_sid": "AC", "auth_token": "T"},
            },
            format="json",
        )
        assert resp.status_code == 201
        # Tenant stamped server-side, not from the request.
        body = resp.json()
        assert body["name"] == "Cfg from admin"


# ─────────────────────────────────────────────────────────────────────────────
# Credential redaction
# ─────────────────────────────────────────────────────────────────────────────


class TestCredentialRedaction:
    def test_get_provider_config_omits_credentials(self, admin_client, config):
        resp = admin_client.get(f"/voice/v1/api/provider-configs/{config.id}/")
        assert resp.status_code == 200
        body = resp.json()
        # ``credentials`` is write-only, so the response shouldn't carry it
        # at all. ``credentials_set`` reports presence without revealing the value.
        assert "credentials" not in body
        assert body["credentials_set"] is True
        # Defensive — the auth token must not be anywhere in the response.
        assert "secret-token-XYZ" not in json.dumps(body)

    def test_list_provider_configs_omits_credentials(self, admin_client, config):
        resp = admin_client.get("/voice/v1/api/provider-configs/")
        assert resp.status_code == 200
        assert "secret-token-XYZ" not in resp.content.decode()


# ─────────────────────────────────────────────────────────────────────────────
# Tenant scoping
# ─────────────────────────────────────────────────────────────────────────────


class TestTenantScoping:
    def test_other_tenants_call_returns_404(self, admin_client, voice_app, other_tenant):
        other_cfg = VoiceProviderConfig.objects.create(
            tenant=other_tenant,
            name="Other Cfg",
            provider=VoiceProvider.TWILIO,
            credentials=json.dumps({"account_sid": "AC", "auth_token": "tok"}),
        )
        other_call = _make_call(other_tenant, other_cfg, provider_call_id="CA_other")

        resp = admin_client.get(f"/voice/v1/api/calls/{other_call.id}/")
        # 404 (not 403) so the API doesn't leak whether the UUID exists.
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Calls: list / initiate / hangup
# ─────────────────────────────────────────────────────────────────────────────


class TestCallsEndpoint:
    def test_list_returns_call(self, admin_client, voice_app, config):
        _make_call(config.tenant, config, provider_call_id="CA_list")
        resp = admin_client.get("/voice/v1/api/calls/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["results"][0]["provider_call_id"] == "CA_list"

    def test_list_filters_status(self, admin_client, voice_app, config):
        _make_call(config.tenant, config, provider_call_id="CA_ok", status=CallStatus.COMPLETED)
        _make_call(config.tenant, config, provider_call_id="CA_bad", status=CallStatus.FAILED)

        resp = admin_client.get("/voice/v1/api/calls/", {"status": CallStatus.FAILED})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["results"][0]["provider_call_id"] == "CA_bad"

    def test_initiate_requires_exactly_one_of_flow_or_text(self, admin_client, voice_app, config):
        resp = admin_client.post(
            "/voice/v1/api/calls/initiate/",
            data={"to_number": "+14155550199"},
            format="json",
        )
        assert resp.status_code == 400
        assert "exactly one" in resp.json()["error"].lower()

    @patch("voice.tasks.initiate_call.delay")
    def test_initiate_creates_call_and_queues_task(self, mock_delay, admin_client, voice_app, config):
        TenantVoiceApp.objects.filter(tenant=config.tenant).update(default_outbound_config=config)
        resp = admin_client.post(
            "/voice/v1/api/calls/initiate/",
            data={"to_number": "+14155550199", "tts_text": "Hello"},
            format="json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == CallStatus.QUEUED
        mock_delay.assert_called_once_with(body["id"])

    def test_hangup_terminal_call_no_op(self, admin_client, voice_app, config):
        call = _make_call(config.tenant, config, status=CallStatus.COMPLETED, provider_call_id="CA_term")
        resp = admin_client.post(f"/voice/v1/api/calls/{call.id}/hangup/")
        assert resp.status_code == 200
        assert resp.json()["hung_up"] is False

    @patch("voice.adapters.registry.get_voice_adapter_cls")
    def test_hangup_invokes_adapter(self, mock_get_cls, admin_client, voice_app, config):
        adapter_instance = MagicMock()
        mock_get_cls.return_value = MagicMock(return_value=adapter_instance)

        call = _make_call(config.tenant, config, status=CallStatus.IN_PROGRESS, provider_call_id="CA_hu")
        resp = admin_client.post(f"/voice/v1/api/calls/{call.id}/hangup/")

        assert resp.status_code == 200
        assert resp.json()["hung_up"] is True
        adapter_instance.hangup.assert_called_once_with("CA_hu")


# ─────────────────────────────────────────────────────────────────────────────
# Templates: list / preview
# ─────────────────────────────────────────────────────────────────────────────


class TestTemplateEndpoint:
    def test_create_and_preview(self, admin_client, voice_app, config):
        resp = admin_client.post(
            "/voice/v1/api/templates/",
            data={
                "name": "Welcome",
                "template_kind": "tts",
                "tts_text": "Hi {{first_name}}!",
                "tts_voice": "alice",
                "tts_language": "en-US",
            },
            format="json",
        )
        assert resp.status_code == 201
        tpl_id = resp.json()["id"]

        preview = admin_client.post(
            f"/voice/v1/api/templates/{tpl_id}/preview/",
            data={"variables": {"first_name": "Riya"}},
            format="json",
        )
        assert preview.status_code == 200
        assert preview.json()["rendered_text"] == "Hi Riya!"


# ─────────────────────────────────────────────────────────────────────────────
# Recordings: detail + download
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordingEndpoint:
    @patch("voice.recordings.storage.signed_url")
    def test_list_returns_signed_url_in_storage_url(self, mock_signed, admin_client, voice_app, config):
        mock_signed.return_value = "https://signed/example?ttl=3600"
        call = _make_call(config.tenant, config, provider_call_id="CA_rec")
        VoiceRecording.objects.create(
            call=call,
            name="rec",
            provider_recording_id="RE_1",
            storage_url="t/c/rec.mp3",
            duration_seconds=42,
            size_bytes=100,
            format="mp3",
        )

        resp = admin_client.get("/voice/v1/api/recordings/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        # The wire ``storage_url`` is the signed URL — bucket key never leaks.
        assert body["results"][0]["storage_url"] == "https://signed/example?ttl=3600"
        assert "t/c/rec.mp3" not in resp.content.decode()

    @patch("voice.recordings.storage.signed_url")
    def test_download_action_respects_ttl(self, mock_signed, admin_client, voice_app, config):
        mock_signed.return_value = "https://signed/x"
        call = _make_call(config.tenant, config, provider_call_id="CA_dl")
        rec = VoiceRecording.objects.create(
            call=call,
            name="rec",
            provider_recording_id="RE_dl",
            storage_url="t/c/rec.mp3",
            duration_seconds=42,
            size_bytes=100,
            format="mp3",
        )

        resp = admin_client.get(f"/voice/v1/api/recordings/{rec.id}/download/", {"expires_seconds": 900})
        assert resp.status_code == 200
        assert resp.json()["expires_seconds"] == 900
        mock_signed.assert_called_once()
        assert mock_signed.call_args.kwargs["expires_seconds"] == 900


# ─────────────────────────────────────────────────────────────────────────────
# Rate cards: admin-only
# ─────────────────────────────────────────────────────────────────────────────


class TestRateCardEndpoint:
    def test_agent_cannot_create_rate_card(self, agent_client, voice_app, config):
        resp = agent_client.post(
            "/voice/v1/api/rate-cards/",
            data={
                "name": "RC India",
                "provider_config": str(config.id),
                "destination_prefix": "+91",
                "rate_per_minute": "0.015",
                "currency": "USD",
                "billing_increment_seconds": 60,
                "valid_from": "2025-01-01T00:00:00Z",
            },
            format="json",
        )
        assert resp.status_code == 403

    def test_admin_creates_rate_card(self, admin_client, voice_app, config):
        resp = admin_client.post(
            "/voice/v1/api/rate-cards/",
            data={
                "name": "RC India",
                "provider_config": str(config.id),
                "destination_prefix": "+91",
                "rate_per_minute": "0.015",
                "currency": "USD",
                "billing_increment_seconds": 60,
                "valid_from": "2025-01-01T00:00:00Z",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert VoiceRateCard.objects.filter(provider_config=config).count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Recording consent: regular tenant user can create
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordingConsentEndpoint:
    def test_agent_can_record_consent(self, agent_client, voice_app):
        contact = TenantContact.objects.create(tenant=voice_app.tenant, phone="+14155550199", first_name="Riya")
        resp = agent_client.post(
            "/voice/v1/api/recording-consents/",
            data={
                "name": "Consent Riya",
                "contact": str(contact.id),
                "consent_given": True,
                "consent_method": "web_form",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert RecordingConsent.objects.filter(contact=contact).count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Admin: provider-config form excludes ``credentials``
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminCredentialMasking:
    def test_provider_config_admin_excludes_credentials_field(self):
        from django.contrib import admin as dj_admin

        from voice.models import VoiceProviderConfig

        admin_cls = dj_admin.site._registry[VoiceProviderConfig].__class__
        assert "credentials" in tuple(admin_cls.exclude or ())
