from types import SimpleNamespace

import pytest

from broadcast.tasks import handle_sms_message
from sms.models import SMSApp
from tenants.models import Tenant


@pytest.mark.django_db
class TestBroadcastSMSHandler:
    def test_returns_error_when_sms_app_missing(self):
        tenant = Tenant.objects.create(name="No SMS app tenant")

        message = SimpleNamespace(
            contact=SimpleNamespace(phone="+14155551111"),
            broadcast=SimpleNamespace(tenant=tenant, tenant_id=tenant.id, placeholder_data={"text": "hello"}),
            rendered_content="hello",
        )

        result = handle_sms_message(message)

        assert result["success"] is False
        assert "No active SMS app" in result["error"]

    def test_uses_sender_and_returns_success(self, monkeypatch):
        tenant = Tenant.objects.create(name="SMS app tenant")
        SMSApp.objects.create(
            tenant=tenant,
            provider="TWILIO",
            sender_id="+14155550000",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        )

        class _FakeSender:
            def __init__(self, app):
                self.app = app

            def send_text(self, chat_id, text, contact=None, broadcast_message=None, create_inbox_entry=False):
                assert chat_id == "+14155551111"
                assert text == "hello"
                return {"success": True, "message_id": "SM123", "response": {"ok": True}}

        monkeypatch.setattr("sms.services.message_sender.SMSMessageSender", _FakeSender)

        message = SimpleNamespace(
            contact=SimpleNamespace(phone="+14155551111"),
            broadcast=SimpleNamespace(tenant=tenant, tenant_id=tenant.id, placeholder_data={"text": "hello"}),
            rendered_content="hello",
        )

        result = handle_sms_message(message)

        assert result["success"] is True
        assert result["message_id"] == "SM123"
