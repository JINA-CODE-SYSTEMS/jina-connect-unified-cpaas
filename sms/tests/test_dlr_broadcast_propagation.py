from types import SimpleNamespace

import pytest

from broadcast.models import Broadcast, BroadcastMessage, BroadcastPlatformChoices, MessageStatusChoices
from contacts.models import TenantContact
from sms.models import SMSApp, SMSOutboundMessage, SMSWebhookEvent
from sms.tasks import _handle_dlr
from tenants.models import Tenant


@pytest.mark.django_db
class TestDLRBroadcastPropagation:
    def test_dlr_delivered_updates_broadcast_message_status(self, monkeypatch):
        tenant = Tenant.objects.create(name="DLR tenant delivered")
        contact = TenantContact.objects.create(tenant=tenant, phone="+14155551111", first_name="John")
        sms_app = SMSApp.objects.create(
            tenant=tenant,
            provider="TWILIO",
            sender_id="+14155550000",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        )

        broadcast = Broadcast.objects.create(name="SMS broadcast", tenant=tenant, platform=BroadcastPlatformChoices.SMS)
        bm = BroadcastMessage.objects.create(broadcast=broadcast, contact=contact, status=MessageStatusChoices.SENT)

        SMSOutboundMessage.objects.create(
            tenant=tenant,
            sms_app=sms_app,
            contact=contact,
            to_number=str(contact.phone),
            from_number=sms_app.sender_id,
            message_text="hello",
            provider_message_id="SM-DLR-1",
            status="SENT",
            broadcast_message=bm,
        )

        event = SMSWebhookEvent.objects.create(
            tenant=tenant,
            sms_app=sms_app,
            provider=sms_app.provider,
            event_type="DLR",
            provider_message_id="SM-DLR-1",
            payload={"MessageSid": "SM-DLR-1", "MessageStatus": "delivered"},
        )

        monkeypatch.setattr(
            "sms.tasks.get_sms_provider",
            lambda app: SimpleNamespace(
                parse_dlr_webhook=lambda payload: SimpleNamespace(
                    provider_message_id="SM-DLR-1",
                    status="DELIVERED",
                    error_code=None,
                    error_message=None,
                )
            ),
        )

        _handle_dlr(event)

        bm.refresh_from_db()
        assert bm.status == MessageStatusChoices.DELIVERED

    def test_dlr_failed_updates_broadcast_message_status_and_response(self, monkeypatch):
        tenant = Tenant.objects.create(name="DLR tenant failed")
        contact = TenantContact.objects.create(tenant=tenant, phone="+14155552222", first_name="Jane")
        sms_app = SMSApp.objects.create(
            tenant=tenant,
            provider="TWILIO",
            sender_id="+14155550000",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        )

        broadcast = Broadcast.objects.create(
            name="SMS broadcast 2", tenant=tenant, platform=BroadcastPlatformChoices.SMS
        )
        bm = BroadcastMessage.objects.create(broadcast=broadcast, contact=contact, status=MessageStatusChoices.SENT)

        SMSOutboundMessage.objects.create(
            tenant=tenant,
            sms_app=sms_app,
            contact=contact,
            to_number=str(contact.phone),
            from_number=sms_app.sender_id,
            message_text="hello",
            provider_message_id="SM-DLR-2",
            status="SENT",
            broadcast_message=bm,
        )

        event = SMSWebhookEvent.objects.create(
            tenant=tenant,
            sms_app=sms_app,
            provider=sms_app.provider,
            event_type="DLR",
            provider_message_id="SM-DLR-2",
            payload={"MessageSid": "SM-DLR-2", "MessageStatus": "failed"},
        )

        monkeypatch.setattr(
            "sms.tasks.get_sms_provider",
            lambda app: SimpleNamespace(
                parse_dlr_webhook=lambda payload: SimpleNamespace(
                    provider_message_id="SM-DLR-2",
                    status="FAILED",
                    error_code="30003",
                    error_message="Unreachable handset",
                )
            ),
        )

        _handle_dlr(event)

        bm.refresh_from_db()
        assert bm.status == MessageStatusChoices.FAILED
        assert "Unreachable handset" in (bm.response or "")
