"""Tests for RCS webhook views: Google Pub/Sub + Meta dispatching."""

import base64
import json

import pytest
from django.test import RequestFactory

from rcs.models import RCSApp, RCSWebhookEvent
from rcs.views import RCSWebhookView
from tenants.models import Tenant


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="Webhook View Tenant")


@pytest.fixture
def rcs_app(tenant):
    return RCSApp.objects.create(
        tenant=tenant,
        provider="GOOGLE_RBM",
        agent_id="webhook-agent@rbm.goog",
    )


@pytest.fixture
def factory():
    return RequestFactory()


@pytest.fixture(autouse=True)
def silence_task(monkeypatch):
    monkeypatch.setattr("rcs.tasks.process_rcs_event_task.delay", lambda *a, **kw: None)


@pytest.mark.django_db
class TestGoogleWebhookView:
    def _make_pubsub_request(self, factory, rcs_app, payload_dict, sig="valid"):
        data_b64 = base64.b64encode(json.dumps(payload_dict).encode()).decode()
        body = json.dumps({"message": {"data": data_b64, "messageId": "ps-1"}, "subscription": "sub-1"}).encode()
        request = factory.post(
            f"/rcs/v1/webhooks/{rcs_app.pk}/",
            data=body,
            content_type="application/json",
            HTTP_X_GOOG_SIGNATURE=sig,
        )
        return request

    def test_inbound_message_creates_webhook_event(self, factory, rcs_app, monkeypatch):
        monkeypatch.setattr(
            "rcs.providers.google_rbm_provider.GoogleRBMProvider.validate_webhook_signature",
            lambda self, request: True,
        )
        payload = {
            "senderPhoneNumber": "+14155551234",
            "messageId": "goog-msg-1",
            "text": "hello",
        }
        request = self._make_pubsub_request(factory, rcs_app, payload)
        view = RCSWebhookView.as_view()
        response = view(request, rcs_app_id=rcs_app.pk)

        assert response.status_code == 200
        assert RCSWebhookEvent.objects.filter(
            rcs_app=rcs_app,
            provider_message_id="goog-msg-1",
            event_type="MESSAGE",
        ).exists()

    def test_delivery_event_creates_webhook_event(self, factory, rcs_app, monkeypatch):
        monkeypatch.setattr(
            "rcs.providers.google_rbm_provider.GoogleRBMProvider.validate_webhook_signature",
            lambda self, request: True,
        )
        payload = {
            "senderPhoneNumber": "+14155551234",
            "messageId": "goog-dlr-1",
            "eventType": "DELIVERED",
        }
        request = self._make_pubsub_request(factory, rcs_app, payload)
        view = RCSWebhookView.as_view()
        view(request, rcs_app_id=rcs_app.pk)

        assert RCSWebhookEvent.objects.filter(
            rcs_app=rcs_app,
            provider_message_id="goog-dlr-1",
            event_type="DELIVERED",
        ).exists()

    def test_invalid_signature_returns_200_without_persisting(self, factory, rcs_app, monkeypatch):
        monkeypatch.setattr(
            "rcs.providers.google_rbm_provider.GoogleRBMProvider.validate_webhook_signature",
            lambda self, request: False,
        )
        payload = {"senderPhoneNumber": "+14155559999", "messageId": "bad-sig-1", "text": "x"}
        request = self._make_pubsub_request(factory, rcs_app, payload, sig="bad")
        view = RCSWebhookView.as_view()
        response = view(request, rcs_app_id=rcs_app.pk)

        assert response.status_code == 200
        assert not RCSWebhookEvent.objects.filter(provider_message_id="bad-sig-1").exists()

    def test_nonexistent_rcs_app_returns_404(self, factory):
        import uuid

        payload = {"senderPhoneNumber": "+1", "messageId": "x", "text": "x"}
        data_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        body = json.dumps({"message": {"data": data_b64, "messageId": "ps-x"}, "subscription": "sub-x"}).encode()
        request = factory.post(
            f"/rcs/v1/webhooks/{uuid.uuid4()}/",
            data=body,
            content_type="application/json",
        )
        view = RCSWebhookView.as_view()
        response = view(request, rcs_app_id=uuid.uuid4())
        assert response.status_code == 200

    def test_duplicate_event_not_recreated(self, factory, rcs_app, monkeypatch):
        monkeypatch.setattr(
            "rcs.providers.google_rbm_provider.GoogleRBMProvider.validate_webhook_signature",
            lambda self, request: True,
        )
        payload = {"senderPhoneNumber": "+14155558888", "messageId": "dup-1", "text": "hi"}
        request = self._make_pubsub_request(factory, rcs_app, payload)
        view = RCSWebhookView.as_view()
        view(request, rcs_app_id=rcs_app.pk)
        view(request, rcs_app_id=rcs_app.pk)

        assert RCSWebhookEvent.objects.filter(rcs_app=rcs_app, provider_message_id="dup-1").count() == 1


@pytest.mark.django_db
class TestMetaWebhookView:
    @pytest.fixture
    def meta_rcs_app(self, tenant):
        return RCSApp.objects.create(
            tenant=tenant,
            provider="META_RCS",
            agent_id="meta-phone-id-1",
        )

    def test_meta_verification_challenge(self, factory, meta_rcs_app):
        request = factory.get(
            f"/rcs/v1/webhooks/{meta_rcs_app.pk}/",
            {
                "hub.mode": "subscribe",
                "hub.challenge": "123",
                "hub.verify_token": meta_rcs_app.webhook_client_token,
            },
        )
        view = RCSWebhookView.as_view()
        response = view(request, rcs_app_id=meta_rcs_app.pk)
        assert response.status_code == 200
        assert b"123" in response.content

    def test_meta_inbound_creates_event(self, factory, meta_rcs_app, monkeypatch):
        monkeypatch.setattr(
            "rcs.providers.meta_rcs_provider.MetaRCSProvider.validate_webhook_signature",
            lambda self, request: True,
        )
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "meta-msg-1",
                                        "from": "+14155557777",
                                        "type": "text",
                                        "text": {"body": "hi from meta"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode()
        request = factory.post(
            f"/rcs/v1/webhooks/{meta_rcs_app.pk}/",
            data=body,
            content_type="application/json",
        )
        view = RCSWebhookView.as_view()
        view(request, rcs_app_id=meta_rcs_app.pk)

        assert RCSWebhookEvent.objects.filter(
            rcs_app=meta_rcs_app,
            provider_message_id="meta-msg-1",
            event_type="MESSAGE",
        ).exists()
