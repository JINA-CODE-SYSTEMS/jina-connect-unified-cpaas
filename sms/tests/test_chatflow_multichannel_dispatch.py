import pytest

from chat_flow.services.graph_executor import send_session_message
from contacts.models import TenantContact
from sms.models import SMSApp
from telegram.models import TelegramBotApp
from tenants.models import Tenant


@pytest.mark.django_db
class TestChatflowMultiChannelDispatch:
    def test_sms_branch_dispatch(self, monkeypatch):
        tenant = Tenant.objects.create(name="Chatflow SMS tenant")
        contact = TenantContact.objects.create(tenant=tenant, phone="+14155551111", first_name="A")
        SMSApp.objects.create(
            tenant=tenant,
            provider="TWILIO",
            sender_id="+14155550000",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        )

        class _FakeSender:
            def __init__(self, app):
                self.app = app

            def send_text(self, chat_id, text, contact=None):
                return {"success": True, "message_id": "SM-CF-1"}

            def send_keyboard(self, chat_id, text, keyboard, contact=None):
                return {"success": True, "message_id": "SM-CF-KB-1"}

        monkeypatch.setattr("sms.services.message_sender.SMSMessageSender", _FakeSender)

        result = send_session_message(
            contact_id=contact.id,
            node_data={"message_type": "text", "message_content": "hello"},
            context={"platform": "SMS"},
        )

        assert result["success"] is True
        assert result["outgoing_message_id"] == "SM-CF-1"

    def test_telegram_branch_dispatch(self, monkeypatch):
        tenant = Tenant.objects.create(name="Chatflow TG tenant")
        contact = TenantContact.objects.create(
            tenant=tenant,
            phone="+14155551112",
            first_name="B",
            telegram_chat_id=123456,
        )
        TelegramBotApp.objects.create(
            tenant=tenant,
            bot_token="111:AAA-testtoken",
            bot_username="cf_test_bot",
            bot_user_id=111,
        )

        class _FakeSender:
            def __init__(self, app):
                self.app = app

            def send_text(self, chat_id, text, contact=None):
                return {"success": True, "message_id": "TG-CF-1"}

            def send_keyboard(self, chat_id, text, keyboard, contact=None):
                return {"success": True, "message_id": "TG-CF-KB-1"}

        monkeypatch.setattr("telegram.services.message_sender.TelegramMessageSender", _FakeSender)

        result = send_session_message(
            contact_id=contact.id,
            node_data={"message_type": "text", "message_content": "hello"},
            context={"platform": "TELEGRAM"},
        )

        assert result["success"] is True
        assert result["outgoing_message_id"] == "TG-CF-1"
