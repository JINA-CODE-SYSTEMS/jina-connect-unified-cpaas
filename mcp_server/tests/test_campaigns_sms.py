import importlib
import sys
import types

import pytest

from contacts.models import TenantContact
from tenants.models import Tenant


def _load_campaigns_module():
    class _FakeMCP:
        @staticmethod
        def tool():
            def _decorator(fn):
                return fn

            return _decorator

    fake_server = types.ModuleType("mcp_server.server")
    fake_server.mcp = _FakeMCP()
    sys.modules["mcp_server.server"] = fake_server

    import mcp_server.tools.campaigns as campaigns_module

    return importlib.reload(campaigns_module)


@pytest.mark.django_db
class TestMCPCampaignsSMS:
    def test_create_broadcast_rejects_unsupported_channel(self, monkeypatch):
        campaigns = _load_campaigns_module()
        tenant = Tenant.objects.create(name="MCP campaign tenant")
        monkeypatch.setattr("mcp_server.tools.campaigns.resolve_tenant", lambda api_key: (tenant, None))

        result = campaigns.create_broadcast(
            api_key="k",
            name="Test",
            template_name="hello",
            phone_numbers=["+14155551111"],
            channel="EMAIL",
        )

        assert "Unsupported channel" in result["error"]

    def test_create_broadcast_sms_success(self, monkeypatch):
        campaigns = _load_campaigns_module()
        tenant = Tenant.objects.create(name="MCP campaign tenant 2")
        monkeypatch.setattr("mcp_server.tools.campaigns.resolve_tenant", lambda api_key: (tenant, None))

        result = campaigns.create_broadcast(
            api_key="k",
            name="SMS Broadcast",
            template_name="hello sms",
            phone_numbers=["+14155551111", "+14155552222"],
            channel="SMS",
        )

        assert result["channel"] == "SMS"
        assert result["recipient_count"] == 2

        contacts = TenantContact.objects.filter(tenant=tenant)
        assert contacts.count() == 2
        assert all(c.source == "SMS" for c in contacts)

    def test_create_broadcast_sms_max_batch_guard(self, monkeypatch):
        campaigns = _load_campaigns_module()
        tenant = Tenant.objects.create(name="MCP campaign tenant 3")
        monkeypatch.setattr("mcp_server.tools.campaigns.resolve_tenant", lambda api_key: (tenant, None))

        phone_numbers = [f"+1415{i:07d}" for i in range(10001)]
        result = campaigns.create_broadcast(
            api_key="k",
            name="Too Big",
            template_name="hello sms",
            phone_numbers=phone_numbers,
            channel="SMS",
        )

        assert "Batch too large" in result["error"]

    def test_create_broadcast_sms_empty_recipients(self, monkeypatch):
        campaigns = _load_campaigns_module()
        tenant = Tenant.objects.create(name="MCP campaign tenant 4")
        monkeypatch.setattr("mcp_server.tools.campaigns.resolve_tenant", lambda api_key: (tenant, None))

        result = campaigns.create_broadcast(
            api_key="k",
            name="No Recipients",
            template_name="hello sms",
            phone_numbers=[],
            channel="SMS",
        )

        assert "No valid phone numbers provided" in result["error"]

    def test_create_broadcast_sms_invalid_phone_format(self, monkeypatch):
        campaigns = _load_campaigns_module()
        tenant = Tenant.objects.create(name="MCP campaign tenant 5")
        monkeypatch.setattr("mcp_server.tools.campaigns.resolve_tenant", lambda api_key: (tenant, None))

        result = campaigns.create_broadcast(
            api_key="k",
            name="Bad Phone",
            template_name="hello sms",
            phone_numbers=["abc123"],
            channel="SMS",
        )

        assert "Invalid SMS phone number" in result["error"]
