"""
Tests for contact upsert logic in Telegram event processing.
"""
import pytest

from contacts.models import TenantContact
from telegram.models import TelegramBotApp, TelegramWebhookEvent
from telegram.tasks import _handle_message
from tenants.models import Tenant


@pytest.mark.django_db
class TestContactUpsert:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant = Tenant.objects.create(name="Upsert Test Tenant")
        self.bot_app = TelegramBotApp.objects.create(
            tenant=self.tenant,
            bot_token="222:BBB-upserttest",
            bot_username="upsert_bot",
            bot_user_id=222,
        )

    def _make_event(self, chat_id, text="hello", first_name="John", last_name="Doe", username="johndoe"):
        return TelegramWebhookEvent.objects.create(
            tenant=self.tenant,
            bot_app=self.bot_app,
            update_id=chat_id,  # reuse chat_id as update_id for simplicity
            event_type="MESSAGE",
            payload={
                "message": {
                    "message_id": 1,
                    "from": {
                        "id": chat_id,
                        "first_name": first_name,
                        "last_name": last_name,
                        "username": username,
                    },
                    "chat": {"id": chat_id, "type": "private"},
                    "text": text,
                },
            },
        )

    def test_creates_new_contact(self):
        event = self._make_event(chat_id=11111)
        _handle_message(event)

        contact = TenantContact.objects.get(tenant=self.tenant, telegram_chat_id=11111)
        assert contact.first_name == "John"
        assert contact.last_name == "Doe"
        assert contact.telegram_username == "johndoe"

    def test_updates_existing_contact(self):
        TenantContact.objects.create(
            tenant=self.tenant,
            phone="+919000000001",
            telegram_chat_id=22222,
            first_name="Old",
            last_name="Name",
        )
        event = self._make_event(chat_id=22222, first_name="New", last_name="Updated", username="newuser")
        _handle_message(event)

        contact = TenantContact.objects.get(tenant=self.tenant, telegram_chat_id=22222)
        assert contact.first_name == "New"
        assert contact.last_name == "Updated"

    def test_missing_fields_default_to_empty(self):
        event = self._make_event(chat_id=33333, first_name="", last_name="", username=None)
        _handle_message(event)

        contact = TenantContact.objects.get(tenant=self.tenant, telegram_chat_id=33333)
        assert contact.first_name == ""
        assert contact.last_name == ""

    def test_no_chat_id_skips_processing(self):
        event = TelegramWebhookEvent.objects.create(
            tenant=self.tenant,
            bot_app=self.bot_app,
            update_id=44444,
            event_type="MESSAGE",
            payload={
                "message": {
                    "message_id": 1,
                    "from": {"id": 44444},
                    "chat": {},  # no "id" key
                    "text": "no chat id",
                },
            },
        )
        _handle_message(event)
        assert not TenantContact.objects.filter(telegram_chat_id=44444).exists()
