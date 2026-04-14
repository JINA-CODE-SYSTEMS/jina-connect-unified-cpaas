import pytest

from contacts.models import TenantContact
from sms.models import SMSApp, SMSOutboundMessage
from sms.providers.base import SMSSendResult
from sms.services.message_sender import SMSMessageSender
from tenants.models import Tenant


class _FakeProvider:
    def __init__(self, success=True):
        self.success = success

    def send_sms(self, to, body, sender_id=None, dlt_template_id=None, **kwargs):
        if self.success:
            return SMSSendResult(
                success=True,
                provider="fake",
                message_id="SM123",
                segment_count=1,
                raw_response={"ok": True},
            )
        return SMSSendResult(success=False, provider="fake", error_message="boom")


@pytest.mark.django_db
class TestSMSMessageSender:
    def setup_method(self):
        self.tenant = Tenant.objects.create(name="SMS Sender Tenant")
        self.contact = TenantContact.objects.create(
            tenant=self.tenant,
            phone="+14155550123",
            first_name="John",
        )
        self.sms_app = SMSApp.objects.create(
            tenant=self.tenant,
            provider="TWILIO",
            sender_id="+14155550000",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        )

    def test_send_text_success_creates_outbound(self, monkeypatch):
        monkeypatch.setattr(
            "sms.services.message_sender.get_sms_provider",
            lambda app: _FakeProvider(success=True),
        )
        sender = SMSMessageSender(self.sms_app)

        result = sender.send_text(
            chat_id=str(self.contact.phone),
            text="hello",
            contact=self.contact,
            create_inbox_entry=False,
        )

        assert result["success"] is True
        assert result["message_id"] == "SM123"

        outbound = SMSOutboundMessage.objects.get(provider_message_id="SM123")
        assert outbound.status == "SENT"
        assert outbound.contact_id == self.contact.id

    def test_send_text_failure_creates_failed_outbound(self, monkeypatch):
        monkeypatch.setattr(
            "sms.services.message_sender.get_sms_provider",
            lambda app: _FakeProvider(success=False),
        )
        sender = SMSMessageSender(self.sms_app)

        result = sender.send_text(
            chat_id=str(self.contact.phone),
            text="hello",
            contact=self.contact,
            create_inbox_entry=False,
        )

        assert result["success"] is False

        outbound = SMSOutboundMessage.objects.filter(contact=self.contact).latest("created_at")
        assert outbound.status == "FAILED"
        assert "boom" in outbound.error_message
