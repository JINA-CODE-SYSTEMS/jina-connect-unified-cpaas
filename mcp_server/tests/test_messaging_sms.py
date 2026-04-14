import importlib
import sys
import types

import pytest
from sms.models import SMSApp
from tenants.models import Tenant


def _load_messaging_module():
    class _FakeMCP:
        @staticmethod
        def tool():
            def _decorator(fn):
                return fn

            return _decorator

    fake_server = types.ModuleType("mcp_server.server")
    fake_server.mcp = _FakeMCP()
    sys.modules["mcp_server.server"] = fake_server

    import mcp_server.tools.messaging as messaging_module

    return importlib.reload(messaging_module)


@pytest.mark.django_db
class TestMCPMessagingSMS:
    def test_send_message_routes_sms_channel(self, monkeypatch):
        messaging = _load_messaging_module()
        monkeypatch.setattr("mcp_server.tools.messaging._send_sms_message", lambda api_key, phone, text: {"ok": True})

        result = messaging.send_message(api_key="k", phone="+14155551111", text="hello", channel="SMS")

        assert result == {"ok": True}

    def test_send_message_rejects_unsupported_channel(self):
        messaging = _load_messaging_module()
        result = messaging.send_message(api_key="k", phone="+14155551111", text="hello", channel="EMAIL")

        assert "Unsupported channel" in result["error"]

    def test_send_sms_message_returns_error_when_app_missing(self, monkeypatch):
        messaging = _load_messaging_module()
        tenant = Tenant.objects.create(name="MCP SMS no app")
        monkeypatch.setattr("mcp_server.tools.messaging.resolve_tenant", lambda api_key: (tenant, None))

        result = messaging._send_sms_message(api_key="k", phone="+14155551111", text="hello")

        assert "No active SMS app" in result["error"]

    def test_send_sms_message_success(self, monkeypatch):
        messaging = _load_messaging_module()
        tenant = Tenant.objects.create(name="MCP SMS with app")
        app = SMSApp.objects.create(
            tenant=tenant,
            provider="TWILIO",
            sender_id="+14155550000",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        )

        monkeypatch.setattr("mcp_server.tools.messaging.resolve_tenant", lambda api_key: (tenant, None))

        class _FakeSender:
            def __init__(self, sms_app):
                assert sms_app.id == app.id

            def send_text(self, chat_id, text):
                assert chat_id == "+14155551111"
                assert text == "hello"
                return {"success": True, "message_id": "SM-MCP-1"}

        monkeypatch.setattr("sms.services.message_sender.SMSMessageSender", _FakeSender)

        result = messaging._send_sms_message(api_key="k", phone="+14155551111", text="hello")

        assert result["channel"] == "SMS"
        assert result["status"] == "SENT"
        assert result["message_id"] == "SM-MCP-1"

    def test_send_sms_message_rejects_empty_text(self, monkeypatch):
        messaging = _load_messaging_module()
        tenant = Tenant.objects.create(name="MCP SMS empty text")
        monkeypatch.setattr("mcp_server.tools.messaging.resolve_tenant", lambda api_key: (tenant, None))

        result = messaging._send_sms_message(api_key="k", phone="+14155551111", text="")

        assert "cannot be empty" in result["error"]

    def test_send_sms_message_rejects_whitespace_text(self, monkeypatch):
        messaging = _load_messaging_module()
        tenant = Tenant.objects.create(name="MCP SMS whitespace text")
        monkeypatch.setattr("mcp_server.tools.messaging.resolve_tenant", lambda api_key: (tenant, None))

        result = messaging._send_sms_message(api_key="k", phone="+14155551111", text="   ")

        assert "cannot be empty" in result["error"]

    def test_send_sms_message_rejects_invalid_phone(self, monkeypatch):
        messaging = _load_messaging_module()
        tenant = Tenant.objects.create(name="MCP SMS bad phone")
        monkeypatch.setattr("mcp_server.tools.messaging.resolve_tenant", lambda api_key: (tenant, None))

        result = messaging._send_sms_message(api_key="k", phone="abc123", text="hello")

        assert "Invalid SMS phone number" in result["error"]
