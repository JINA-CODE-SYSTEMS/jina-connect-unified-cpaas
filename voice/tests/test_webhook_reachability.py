"""B4 (#184): webhook reachability probe.

Covers:

  * Per-provider URL discovery — Twilio surfaces 4 webhooks, Plivo 3,
    Vonage 2, Telnyx 1, Exotel 2, SIP 0.
  * Passive freshness classification — recent / stale / never.
  * The ``test-webhooks`` action gates on ``IsVoiceAdmin`` like the rest
    of provider-config actions.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser, TenantVoiceApp
from voice.constants import CallDirection, CallEventType, CallStatus, VoiceProvider
from voice.models import VoiceCall, VoiceCallEvent, VoiceProviderConfig


def _make_cfg(tenant, provider, **extra):
    creds = {
        VoiceProvider.TWILIO: {"account_sid": "AC1", "auth_token": "t"},
        VoiceProvider.PLIVO: {"auth_id": "MA1", "auth_token": "t"},
        VoiceProvider.VONAGE: {
            "api_key": "k",
            "api_secret": "s",
            "application_id": "00000000-0000-0000-0000-000000000001",
            "private_key_pem": "p",
        },
        VoiceProvider.TELNYX: {"api_key": "tx", "connection_id": "c"},
        VoiceProvider.EXOTEL: {"sid": "s", "api_key": "k", "api_token": "t"},
        VoiceProvider.SIP: {
            "sip_username": "u",
            "sip_password": "p",
            "sip_realm": "sip.example.com",
            "sip_proxy": "sip.example.com",
        },
    }[provider]
    return VoiceProviderConfig.objects.create(
        tenant=tenant,
        name=extra.pop("name", f"{provider}-cfg"),
        provider=provider,
        credentials=json.dumps(creds),
        from_numbers=["+14155550100"],
        **extra,
    )


def _make_event(call, event_type, occurred_at):
    seq = VoiceCallEvent.objects.filter(call=call).count() + 1
    return VoiceCallEvent.objects.create(
        call=call,
        event_type=event_type,
        occurred_at=occurred_at,
        sequence=seq,
        payload={},
    )


def _make_call(tenant, config):
    return VoiceCall.objects.create(
        tenant=tenant,
        name="rc-call",
        provider_config=config,
        provider_call_id="CA_reach",
        direction=CallDirection.OUTBOUND,
        from_number="+14155550100",
        to_number="+14155550199",
        status=CallStatus.COMPLETED,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pure function: probe_config
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProbeConfig:
    def test_twilio_returns_four_routes(self):
        tenant = Tenant.objects.create(name="ReachTwilio")
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        from voice.webhooks.reachability import probe_config

        result = probe_config(cfg)
        assert result["provider"] == VoiceProvider.TWILIO
        assert result["probe_type"] == "passive"
        labels = {r["label"] for r in result["results"]}
        assert labels == {"call-status", "answer", "gather", "recording-status"}

    def test_telnyx_returns_single_event_route(self):
        tenant = Tenant.objects.create(name="ReachTelnyx")
        cfg = _make_cfg(tenant, VoiceProvider.TELNYX)
        from voice.webhooks.reachability import probe_config

        labels = {r["label"] for r in probe_config(cfg)["results"]}
        assert labels == {"event"}

    def test_sip_returns_empty_results(self):
        tenant = Tenant.objects.create(name="ReachSIP")
        cfg = _make_cfg(tenant, VoiceProvider.SIP)
        from voice.webhooks.reachability import probe_config

        # SIP has no HTTP webhooks — events arrive via the ARI consumer.
        assert probe_config(cfg)["results"] == []

    def test_never_status_when_no_events(self):
        tenant = Tenant.objects.create(name="ReachNoEvent")
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        from voice.webhooks.reachability import probe_config

        for row in probe_config(cfg)["results"]:
            assert row["status"] == "passive_never"
            assert row["last_received_at"] is None
            assert row["sample_call_id"] is None

    def test_recent_status_classifies_correctly(self):
        tenant = Tenant.objects.create(name="ReachRecent")
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        call = _make_call(tenant, cfg)
        _make_event(call, CallEventType.COMPLETED, timezone.now() - timedelta(minutes=2))
        from voice.webhooks.reachability import probe_config

        result = probe_config(cfg)
        row = next(r for r in result["results"] if r["label"] == "call-status")
        assert row["status"] == "passive_recent"
        assert row["last_received_at"] is not None
        assert row["sample_call_id"] == str(call.id)

    def test_stale_status_when_event_old(self):
        tenant = Tenant.objects.create(name="ReachStale")
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        call = _make_call(tenant, cfg)
        _make_event(call, CallEventType.COMPLETED, timezone.now() - timedelta(days=2))
        from voice.webhooks.reachability import probe_config

        row = next(r for r in probe_config(cfg)["results"] if r["label"] == "call-status")
        assert row["status"] == "passive_stale"

    def test_label_filters_route_specific(self):
        # A recording event should mark the recording-status route as
        # recent but the gather route as never (no DTMF/SPEECH event).
        tenant = Tenant.objects.create(name="ReachLabel")
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        call = _make_call(tenant, cfg)
        _make_event(call, CallEventType.RECORDING_COMPLETED, timezone.now())
        from voice.webhooks.reachability import probe_config

        rows = {r["label"]: r for r in probe_config(cfg)["results"]}
        assert rows["recording-status"]["status"] == "passive_recent"
        assert rows["gather"]["status"] == "passive_never"
        # call-status accepts any event so it's also recent.
        assert rows["call-status"]["status"] == "passive_recent"

    def test_inferred_from_flag(self):
        # call-status / event / status routes are "any event" -> inferred_from=any_event.
        # answer / gather / recording-status routes are filtered by event type.
        tenant = Tenant.objects.create(name="ReachInferred")
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        from voice.webhooks.reachability import probe_config

        rows = {r["label"]: r for r in probe_config(cfg)["results"]}
        assert rows["call-status"]["inferred_from"] == "any_event"
        assert rows["answer"]["inferred_from"] == "event_type"
        assert rows["gather"]["inferred_from"] == "event_type"
        assert rows["recording-status"]["inferred_from"] == "event_type"

    def test_aggregate_query_count_is_bounded(self, django_assert_num_queries):
        # B4 review feedback: probe_config used to issue N+1 queries
        # (one per route). The aggregate version should land a small,
        # constant number regardless of how many routes the provider
        # has. Twilio has 4 routes; we cap at a generous bound that
        # still catches the regression if the loop comes back.
        tenant = Tenant.objects.create(name="ReachQueryCount")
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        call = _make_call(tenant, cfg)
        for et in (
            CallEventType.INITIATED,
            CallEventType.RINGING,
            CallEventType.RECORDING_COMPLETED,
            CallEventType.COMPLETED,
        ):
            _make_event(call, et, timezone.now())
        from voice.webhooks.reachability import probe_config

        # 1 aggregate + 1 sample for any-event routes + up to 3 per
        # filtered route that has a hit. Be generous; the goal is
        # "constant, not N+1 per route count".
        with django_assert_num_queries(10):
            probe_config(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: POST /provider-configs/{id}/test-webhooks/
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tenant(db):
    return Tenant.objects.create(name="ReachAPITenant")


@pytest.fixture()
def voice_app(tenant):
    return TenantVoiceApp.objects.create(tenant=tenant, is_enabled=True)


@pytest.fixture()
def admin_client(db, tenant, voice_app):
    from django.contrib.auth import get_user_model

    role = TenantRole.objects.get(tenant=tenant, slug="owner")
    user = get_user_model().objects.create_user(
        username="reach_admin", email="ra@t.io", mobile="+919600008001", password="x"
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture()
def agent_client(db, tenant, voice_app):
    from django.contrib.auth import get_user_model

    role = TenantRole.objects.get(tenant=tenant, slug="agent")
    user = get_user_model().objects.create_user(
        username="reach_agent", email="ra2@t.io", mobile="+919600008002", password="x"
    )
    TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestTestWebhooksAction:
    def test_admin_can_probe(self, admin_client, tenant, voice_app):
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        resp = admin_client.post(f"/voice/v1/api/provider-configs/{cfg.id}/test-webhooks/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["config_id"] == str(cfg.id)
        assert body["provider"] == VoiceProvider.TWILIO
        assert body["probe_type"] == "passive"
        assert len(body["results"]) == 4

    def test_agent_blocked(self, agent_client, tenant, voice_app):
        cfg = _make_cfg(tenant, VoiceProvider.TWILIO)
        resp = agent_client.post(f"/voice/v1/api/provider-configs/{cfg.id}/test-webhooks/")
        assert resp.status_code == 403

    def test_other_tenant_returns_404(self, admin_client, tenant, voice_app):
        other = Tenant.objects.create(name="OtherReachTenant")
        cfg = _make_cfg(other, VoiceProvider.TWILIO)
        resp = admin_client.post(f"/voice/v1/api/provider-configs/{cfg.id}/test-webhooks/")
        assert resp.status_code == 404
