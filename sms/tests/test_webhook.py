import json

import pytest
from django.test import RequestFactory

from sms.models import SMSApp, SMSWebhookEvent
from sms.views import SMSDLRWebhookView, SMSInboundWebhookView
from tenants.models import Tenant


@pytest.mark.django_db
class TestSMSWebhookView:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(name="SMS Webhook Tenant")
        self.sms_app = SMSApp.objects.create(
            tenant=self.tenant,
            provider="TWILIO",
            sender_id="+14155550000",
            provider_credentials={"account_sid": "AC123", "auth_token": "tok"},
        )

        # Avoid broker dependency in tests.
        monkeypatch.setattr("sms.tasks.process_sms_event_task.delay", lambda *args, **kwargs: None)

        self.inbound_view = SMSInboundWebhookView.as_view()
        self.dlr_view = SMSDLRWebhookView.as_view()

    def test_inbound_webhook_creates_event(self, monkeypatch):
        monkeypatch.setattr(
            "sms.providers.twilio_provider.TwilioSMSProvider.validate_webhook_signature",
            lambda self, request: True,
        )

        payload = {
            "MessageSid": "SM-IN-1",
            "From": "+14155551111",
            "To": "+14155550000",
            "Body": "hello",
        }
        request = self.factory.post(
            f"/sms/v1/webhooks/{self.sms_app.pk}/inbound/",
            data=payload,
        )

        response = self.inbound_view(request, sms_app_id=self.sms_app.pk)

        assert response.status_code == 200
        assert SMSWebhookEvent.objects.filter(
            sms_app=self.sms_app,
            provider_message_id="SM-IN-1",
            event_type="INBOUND",
        ).exists()

    def test_dlr_webhook_creates_event(self, monkeypatch):
        monkeypatch.setattr(
            "sms.providers.twilio_provider.TwilioSMSProvider.validate_webhook_signature",
            lambda self, request: True,
        )

        payload = {
            "MessageSid": "SM-DLR-1",
            "MessageStatus": "delivered",
            "To": "+14155551111",
            "From": "+14155550000",
        }
        request = self.factory.post(
            f"/sms/v1/webhooks/{self.sms_app.pk}/dlr/",
            data=payload,
        )

        response = self.dlr_view(request, sms_app_id=self.sms_app.pk)

        assert response.status_code == 200
        assert SMSWebhookEvent.objects.filter(
            sms_app=self.sms_app,
            provider_message_id="SM-DLR-1",
            event_type="DLR",
        ).exists()

    def test_invalid_signature_returns_ok_without_persist(self, monkeypatch):
        monkeypatch.setattr(
            "sms.providers.twilio_provider.TwilioSMSProvider.validate_webhook_signature",
            lambda self, request: False,
        )

        request = self.factory.post(
            f"/sms/v1/webhooks/{self.sms_app.pk}/inbound/",
            data={"MessageSid": "SM-BAD-1", "Body": "x"},
        )

        response = self.inbound_view(request, sms_app_id=self.sms_app.pk)

        assert response.status_code == 200
        assert not SMSWebhookEvent.objects.filter(provider_message_id="SM-BAD-1").exists()

    def test_json_payload_fallback_message_id_is_stable(self, monkeypatch):
        monkeypatch.setattr(
            "sms.providers.msg91_provider.MSG91SMSProvider.validate_webhook_signature",
            lambda self, request: True,
        )

        # Use MSG91 app with no msg_id in payload to trigger fallback ID.
        self.sms_app.provider = "MSG91"
        self.sms_app.save(update_fields=["provider"])

        payload = {"mobile": "+14155552222", "message": "ping", "sender": "JINA"}
        request = self.factory.post(
            f"/sms/v1/webhooks/{self.sms_app.pk}/inbound/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        response = self.inbound_view(request, sms_app_id=self.sms_app.pk)

        assert response.status_code == 200
        assert SMSWebhookEvent.objects.filter(sms_app=self.sms_app, event_type="INBOUND").count() == 1
