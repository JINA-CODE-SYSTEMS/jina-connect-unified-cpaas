"""
Integration test: webhook → event → Celery task → inbox message created.
"""
import json
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from contacts.models import TenantContact
from team_inbox.models import Messages
from telegram.models import TelegramBotApp, TelegramWebhookEvent
from telegram.tasks import process_tg_event_task
from telegram.views import TelegramWebhookView
from tenants.models import Tenant


@pytest.mark.django_db(transaction=True)
class TestInboxIntegration:
    """End-to-end: POST webhook → event persisted → task runs → inbox message exists."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(name="Inbox Integration Tenant")
        self.bot_app = TelegramBotApp.objects.create(
            tenant=self.tenant,
            bot_token="333:CCC-inboxtest",
            bot_username="inbox_bot",
            bot_user_id=333,
        )
        self.view = TelegramWebhookView.as_view()

    def _post_webhook(self, payload):
        request = self.factory.post(
            f"/telegram/v1/webhooks/{self.bot_app.pk}/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.META["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = self.bot_app.webhook_secret
        return self.view(request, bot_app_id=self.bot_app.pk)

    def test_text_message_creates_inbox_entry(self):
        payload = {
            "update_id": 200001,
            "message": {
                "message_id": 10,
                "from": {"id": 55555, "first_name": "Alice", "last_name": "W"},
                "chat": {"id": 55555, "type": "private"},
                "text": "Hello from Telegram!",
            },
        }
        self._post_webhook(payload)
        event = TelegramWebhookEvent.objects.get(update_id=200001)

        # Run the Celery task synchronously
        process_tg_event_task(str(event.pk))

        # Contact should be created
        contact = TenantContact.objects.get(tenant=self.tenant, telegram_chat_id=55555)
        assert contact.first_name == "Alice"

        # Inbox message should exist
        inbox_msg = Messages.objects.filter(
            tenant=self.tenant,
            contact=contact,
            platform="TELEGRAM",
        ).first()
        assert inbox_msg is not None
        assert inbox_msg.content["type"] == "text"
        assert inbox_msg.content["body"]["text"] == "Hello from Telegram!"

    def test_photo_message_creates_inbox_entry(self):
        payload = {
            "update_id": 200002,
            "message": {
                "message_id": 11,
                "from": {"id": 66666, "first_name": "Bob"},
                "chat": {"id": 66666, "type": "private"},
                "photo": [
                    {"file_id": "small_id", "width": 100, "height": 100},
                    {"file_id": "large_id", "width": 800, "height": 600},
                ],
                "caption": "Check this out",
            },
        }
        self._post_webhook(payload)
        event = TelegramWebhookEvent.objects.get(update_id=200002)
        process_tg_event_task(str(event.pk))

        inbox_msg = Messages.objects.filter(
            tenant=self.tenant,
            platform="TELEGRAM",
            contact__telegram_chat_id=66666,
        ).first()
        assert inbox_msg is not None
        assert inbox_msg.content["type"] == "image"
        assert inbox_msg.content["media"]["file_id"] == "large_id"

    def test_duplicate_update_id_is_ignored(self):
        payload = {
            "update_id": 200003,
            "message": {
                "message_id": 12,
                "from": {"id": 77777, "first_name": "Charlie"},
                "chat": {"id": 77777, "type": "private"},
                "text": "First",
            },
        }
        # Post twice
        self._post_webhook(payload)
        self._post_webhook(payload)

        # Only one event should exist
        assert TelegramWebhookEvent.objects.filter(update_id=200003).count() == 1
