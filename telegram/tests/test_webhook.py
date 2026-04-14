"""
Tests for TelegramWebhookView — secret validation, idempotency, event classification.
"""

import json
import uuid

import pytest
from django.test import RequestFactory

from telegram.models import TelegramBotApp, TelegramWebhookEvent
from telegram.views import TelegramWebhookView
from tenants.models import Tenant


@pytest.mark.django_db
class TestTelegramWebhookView:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(name="Webhook Test Tenant")
        self.bot_app = TelegramBotApp.objects.create(
            tenant=self.tenant,
            bot_token="111:AAA-testtoken",
            bot_username="webhook_test_bot",
            bot_user_id=111,
        )
        self.view = TelegramWebhookView.as_view()
        self.url = f"/telegram/v1/webhooks/{self.bot_app.pk}/"

    def _post(self, payload, secret=None):
        request = self.factory.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        if secret is not None:
            request.META["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = secret
        return self.view(request, bot_app_id=self.bot_app.pk)

    def test_get_health_check(self):
        request = self.factory.get(self.url)
        response = self.view(request, bot_app_id=self.bot_app.pk)
        assert response.status_code == 200
        assert json.loads(response.content)["ok"] is True

    def test_valid_message_creates_event(self):
        payload = {
            "update_id": 100001,
            "message": {"message_id": 1, "chat": {"id": 12345}, "text": "hello"},
        }
        response = self._post(payload, secret=self.bot_app.webhook_secret)
        assert response.status_code == 200
        assert TelegramWebhookEvent.objects.filter(bot_app=self.bot_app, update_id=100001).exists()

    def test_event_type_classified_as_message(self):
        payload = {
            "update_id": 100002,
            "message": {"message_id": 2, "chat": {"id": 12345}, "text": "hi"},
        }
        self._post(payload, secret=self.bot_app.webhook_secret)
        event = TelegramWebhookEvent.objects.get(update_id=100002)
        assert event.event_type == "MESSAGE"

    def test_callback_query_classified(self):
        payload = {
            "update_id": 100003,
            "callback_query": {
                "id": "cb_123",
                "data": "v1:select:node1:abc",
                "message": {"chat": {"id": 12345}},
            },
        }
        self._post(payload, secret=self.bot_app.webhook_secret)
        event = TelegramWebhookEvent.objects.get(update_id=100003)
        assert event.event_type == "CALLBACK_QUERY"

    def test_invalid_secret_does_not_persist_event(self):
        payload = {"update_id": 100004, "message": {"text": "bad"}}
        response = self._post(payload, secret="wrong-secret")
        assert response.status_code == 200
        assert not TelegramWebhookEvent.objects.filter(update_id=100004).exists()

    def test_missing_secret_does_not_persist_event(self):
        payload = {"update_id": 100005, "message": {"text": "no secret"}}
        response = self._post(payload, secret=None)
        assert response.status_code == 200
        assert not TelegramWebhookEvent.objects.filter(update_id=100005).exists()

    def test_duplicate_update_id_is_idempotent(self):
        payload = {
            "update_id": 100006,
            "message": {"message_id": 6, "chat": {"id": 12345}, "text": "first"},
        }
        self._post(payload, secret=self.bot_app.webhook_secret)
        self._post(payload, secret=self.bot_app.webhook_secret)
        assert TelegramWebhookEvent.objects.filter(bot_app=self.bot_app, update_id=100006).count() == 1

    def test_unknown_bot_app_returns_200(self):
        fake_id = uuid.uuid4()
        request = self.factory.post(
            f"/telegram/v1/webhooks/{fake_id}/",
            data=json.dumps({"update_id": 999}),
            content_type="application/json",
        )
        request.META["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = "whatever"
        response = self.view(request, bot_app_id=fake_id)
        assert response.status_code == 200

    def test_invalid_json_returns_200(self):
        request = self.factory.post(
            self.url,
            data="not-json",
            content_type="application/json",
        )
        request.META["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = self.bot_app.webhook_secret
        response = self.view(request, bot_app_id=self.bot_app.pk)
        assert response.status_code == 200

    def test_missing_update_id_returns_200(self):
        payload = {"message": {"text": "no update_id"}}
        response = self._post(payload, secret=self.bot_app.webhook_secret)
        assert response.status_code == 200
        assert TelegramWebhookEvent.objects.count() == 0
