"""MCP voice-tool tests (#173)."""

from __future__ import annotations

import importlib
import json
import sys
import types
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from contacts.models import TenantContact
from tenants.models import Tenant, TenantVoiceApp
from voice.constants import CallDirection, CallStatus, VoiceProvider
from voice.models import VoiceCall, VoiceProviderConfig, VoiceRecording


def _load_voice_module():
    class _FakeMCP:
        @staticmethod
        def tool():
            def _decorator(fn):
                return fn

            return _decorator

    fake_server = types.ModuleType("mcp_server.server")
    fake_server.mcp = _FakeMCP()
    sys.modules["mcp_server.server"] = fake_server

    import mcp_server.tools.voice as voice_module

    return importlib.reload(voice_module)


def _make_tenant_with_voice(name, *, voice_enabled=True, default_outbound=None):
    tenant = Tenant.objects.create(name=name)
    if voice_enabled is not None:
        TenantVoiceApp.objects.create(
            tenant=tenant,
            is_enabled=voice_enabled,
            default_outbound_config=default_outbound,
        )
    return tenant


def _make_config(tenant, **extra):
    return VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="MCP Twilio",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
        from_numbers=["+14155550100"],
        **extra,
    )


def _make_call(tenant, cfg, **extra):
    return VoiceCall.objects.create(
        tenant=tenant,
        name="mcp-call",
        provider_config=cfg,
        provider_call_id=extra.pop("provider_call_id", "CA_mcp"),
        direction=CallDirection.OUTBOUND,
        from_number="+14155550100",
        to_number=extra.pop("to_number", "+14155550199"),
        status=extra.pop("status", CallStatus.COMPLETED),
        duration_seconds=extra.pop("duration", 42),
        **extra,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tenant scoping / authz
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestVoiceAuthz:
    def test_tenant_without_voice_app_gets_error(self, monkeypatch):
        voice = _load_voice_module()
        tenant = Tenant.objects.create(name="No voice tenant")
        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_get_call_status(api_key="k", call_id="00000000-0000-0000-0000-000000000000")
        assert "not provisioned" in result["error"].lower()

    def test_tenant_with_voice_disabled_gets_error(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("disabled tenant", voice_enabled=False)
        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_get_call_status(api_key="k", call_id="00000000-0000-0000-0000-000000000000")
        assert "disabled" in result["error"].lower()

    def test_invalid_api_key_surfaces_resolve_error(self, monkeypatch):
        voice = _load_voice_module()

        def _bad(api_key):
            raise ValueError("Invalid API key. Check your Jina Connect access key.")

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", _bad)

        result = voice.voice_get_call_status(api_key="k", call_id="x")
        assert "Invalid API key" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# voice_initiate_call
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestVoiceInitiateCall:
    def test_creates_call_and_queues_task(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("init tenant")
        cfg = _make_config(tenant)
        TenantVoiceApp.objects.filter(tenant=tenant).update(default_outbound_config=cfg)

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        delay_calls = []
        monkeypatch.setattr(
            "voice.tasks.initiate_call.delay", lambda call_id: delay_calls.append(call_id) or MagicMock()
        )

        result = voice.voice_initiate_call(api_key="k", to_number="+14155550199", tts_text="Hi there")

        assert "call_id" in result
        assert result["status"] == CallStatus.QUEUED
        assert delay_calls == [result["call_id"]]
        call = VoiceCall.objects.get(pk=result["call_id"])
        assert call.metadata.get("static_play") == {"tts_text": "Hi there"}

    def test_rejects_both_flow_and_text(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("init both tenant")
        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_initiate_call(api_key="k", to_number="+14155550199", flow_id="x", tts_text="y")
        assert "exactly one" in result["error"].lower()

    def test_rejects_neither_flow_nor_text(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("init neither tenant")
        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_initiate_call(api_key="k", to_number="+14155550199")
        assert "exactly one" in result["error"].lower()

    def test_no_provider_config_errors(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("init nocfg tenant")
        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_initiate_call(api_key="k", to_number="+14155550199", tts_text="hi")
        assert "No active VoiceProviderConfig" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# voice_get_call_status / list / hangup / recording / transcription
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestVoiceLookups:
    def test_get_call_status_returns_events(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("look tenant")
        cfg = _make_config(tenant)
        call = _make_call(tenant, cfg, provider_call_id="CA_look")

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_get_call_status(api_key="k", call_id=str(call.id))
        assert result["call_id"] == str(call.id)
        assert result["status"] == CallStatus.COMPLETED
        assert "events" in result

    def test_get_call_status_other_tenant_404s(self, monkeypatch):
        voice = _load_voice_module()
        tenant_a = _make_tenant_with_voice("look a")
        tenant_b = _make_tenant_with_voice("look b")
        cfg_b = _make_config(tenant_b)
        call_b = _make_call(tenant_b, cfg_b, provider_call_id="CA_look_b")

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant_a, None))

        result = voice.voice_get_call_status(api_key="k", call_id=str(call_b.id))
        assert "not found" in result["error"]

    def test_list_calls_filters_and_paginates(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("list tenant")
        cfg = _make_config(tenant)
        now = timezone.now()
        for i in range(5):
            _make_call(tenant, cfg, provider_call_id=f"CA_L_{i}", started_at=now)

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_list_calls(api_key="k", limit=2, offset=0)
        assert result["count"] == 5
        assert len(result["calls"]) == 2

    def test_list_calls_status_filter(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("list status tenant")
        cfg = _make_config(tenant)
        _make_call(tenant, cfg, provider_call_id="CA_done", status=CallStatus.COMPLETED)
        _make_call(tenant, cfg, provider_call_id="CA_fail", status=CallStatus.FAILED)

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_list_calls(api_key="k", status=CallStatus.FAILED)
        assert result["count"] == 1
        assert result["calls"][0]["provider_call_id"] == "CA_fail"

    def test_get_recording_returns_signed_url(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("rec tenant")
        cfg = _make_config(tenant)
        call = _make_call(tenant, cfg, provider_call_id="CA_rec")
        VoiceRecording.objects.create(
            call=call,
            name="rec",
            provider_recording_id="RE_1",
            storage_url="t/c/rec.mp3",
            duration_seconds=42,
            size_bytes=100,
            format="mp3",
        )

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))
        monkeypatch.setattr(
            "voice.recordings.storage.signed_url",
            lambda key, expires_seconds=None: f"https://signed/{key}?ttl={expires_seconds}",
        )

        result = voice.voice_get_recording(api_key="k", call_id=str(call.id), expires_seconds=900)
        assert result["recording_url"].startswith("https://signed/t/c/rec.mp3")
        assert "ttl=900" in result["recording_url"]

    def test_get_recording_returns_null_when_missing(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("rec none tenant")
        cfg = _make_config(tenant)
        call = _make_call(tenant, cfg, provider_call_id="CA_rec_none")

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_get_recording(api_key="k", call_id=str(call.id))
        assert result["recording_url"] is None

    def test_get_transcription_returns_text(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("trans tenant")
        cfg = _make_config(tenant)
        call = _make_call(tenant, cfg, provider_call_id="CA_tr")
        VoiceRecording.objects.create(
            call=call,
            name="rec",
            provider_recording_id="RE_tr",
            storage_url="t/c/rec.mp3",
            duration_seconds=42,
            size_bytes=100,
            format="mp3",
            transcription="hello world",
            transcription_provider="deepgram",
            transcription_confidence=0.95,
        )

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_get_transcription(api_key="k", call_id=str(call.id))
        assert result["text"] == "hello world"
        assert result["provider"] == "deepgram"

    def test_get_transcription_returns_null_when_missing(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("trans none tenant")
        cfg = _make_config(tenant)
        call = _make_call(tenant, cfg, provider_call_id="CA_tr_none")

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_get_transcription(api_key="k", call_id=str(call.id))
        assert result["transcription"] is None


# ─────────────────────────────────────────────────────────────────────────────
# voice_hangup_call
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestVoiceHangup:
    def test_invokes_adapter_hangup(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("hu tenant")
        cfg = _make_config(tenant)
        call = _make_call(tenant, cfg, provider_call_id="CA_hu", status=CallStatus.IN_PROGRESS)

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        adapter_instance = MagicMock()
        adapter_cls = MagicMock(return_value=adapter_instance)
        monkeypatch.setattr("voice.adapters.registry.get_voice_adapter_cls", lambda provider: adapter_cls)

        result = voice.voice_hangup_call(api_key="k", call_id=str(call.id))
        assert result["hung_up"] is True
        adapter_instance.hangup.assert_called_once_with("CA_hu")

    def test_no_op_when_already_terminal(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("hu term tenant")
        cfg = _make_config(tenant)
        call = _make_call(tenant, cfg, provider_call_id="CA_hu_term", status=CallStatus.COMPLETED)

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_hangup_call(api_key="k", call_id=str(call.id))
        assert result["hung_up"] is False
        assert result["status"] == CallStatus.COMPLETED


# ─────────────────────────────────────────────────────────────────────────────
# voice_trigger_broadcast
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestVoiceTriggerBroadcast:
    def test_dispatches_voice_broadcast(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("br tenant")
        contact = TenantContact.objects.create(tenant=tenant, phone="+14155550199")

        from broadcast.models import Broadcast, BroadcastMessage, BroadcastPlatformChoices

        broadcast = Broadcast.objects.create(tenant=tenant, name="br", platform=BroadcastPlatformChoices.VOICE)
        BroadcastMessage.objects.create(broadcast=broadcast, contact=contact, name="m")

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        task = MagicMock()
        task.id = "task-1"
        monkeypatch.setattr("broadcast.tasks.process_broadcast_messages_batch.delay", lambda ids: task)

        result = voice.voice_trigger_broadcast(api_key="k", broadcast_id=str(broadcast.id))
        assert result["recipient_count"] == 1
        assert result["task_id"] == "task-1"

    def test_rejects_non_voice_broadcast(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("br wa tenant")

        from broadcast.models import Broadcast, BroadcastPlatformChoices

        broadcast = Broadcast.objects.create(tenant=tenant, name="br_wa", platform=BroadcastPlatformChoices.WHATSAPP)

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_trigger_broadcast(api_key="k", broadcast_id=str(broadcast.id))
        assert "not a VOICE broadcast" in result["error"]

    def test_empty_broadcast_errors(self, monkeypatch):
        voice = _load_voice_module()
        tenant = _make_tenant_with_voice("br empty tenant")

        from broadcast.models import Broadcast, BroadcastPlatformChoices

        broadcast = Broadcast.objects.create(tenant=tenant, name="br_empty", platform=BroadcastPlatformChoices.VOICE)

        monkeypatch.setattr("mcp_server.tools.voice.resolve_tenant", lambda api_key: (tenant, None))

        result = voice.voice_trigger_broadcast(api_key="k", broadcast_id=str(broadcast.id))
        assert "no recipients" in result["error"].lower()
