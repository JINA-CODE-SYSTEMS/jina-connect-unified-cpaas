"""
Tests for TelegramBotAppViewSet.perform_create (invalid-token rejection)
and TelegramBroadcastViewSet.perform_create (empty placeholder_data guard).
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from broadcast.models import Broadcast, BroadcastPlatformChoices
from contacts.models import TenantContact
from telegram.models import TelegramBotApp
from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="viewset_user",
        email="vs@example.com",
        mobile="+919800000001",
        password="pass",
    )


@pytest.fixture
def tenant(db, user):
    return Tenant.objects.create(name="VS Tenant", created_by=user, updated_by=user)


@pytest.fixture
def role(tenant):
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    return role


@pytest.fixture
def tenant_user(db, tenant, user, role):
    return TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)


@pytest.fixture
def auth_client(user, tenant_user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ── TelegramBotAppViewSet ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTelegramBotAppCreate:
    BOT_URL = "/telegram/v1/bots/"

    @patch("telegram.services.bot_client.TelegramBotClient.get_me")
    def test_valid_token_creates_bot_with_backfilled_identity(self, mock_get_me, auth_client, tenant):
        mock_get_me.return_value = {"id": 9999, "username": "my_test_bot"}

        resp = auth_client.post(
            self.BOT_URL,
            {"bot_token": "9999:VALID-TOKEN-abc"},
            format="json",
        )
        assert resp.status_code == 201
        bot = TelegramBotApp.objects.get(tenant=tenant)
        assert bot.bot_user_id == 9999
        assert bot.bot_username == "my_test_bot"

    @patch("telegram.services.bot_client.TelegramBotClient.get_me")
    def test_invalid_token_returns_400_and_does_not_save(self, mock_get_me, auth_client, tenant):
        from telegram.services.bot_client import TelegramAPIError

        mock_get_me.side_effect = TelegramAPIError(401, "Unauthorized")

        resp = auth_client.post(
            self.BOT_URL,
            {"bot_token": "bad:TOKEN"},
            format="json",
        )
        assert resp.status_code == 400
        assert "bot_token" in resp.data
        # Ensure the partial record was deleted
        assert TelegramBotApp.objects.filter(tenant=tenant).count() == 0

    @patch("telegram.services.bot_client.TelegramBotClient.get_me")
    def test_error_message_does_not_leak_telegram_internals(self, mock_get_me, auth_client):
        from telegram.services.bot_client import TelegramAPIError

        mock_get_me.side_effect = TelegramAPIError(401, "Unauthorized: very_secret_detail")

        resp = auth_client.post(
            self.BOT_URL,
            {"bot_token": "bad:TOKEN"},
            format="json",
        )
        assert resp.status_code == 400
        # The raw Telegram error string must NOT be in the response
        assert "very_secret_detail" not in str(resp.data)
        assert "Unauthorized" not in str(resp.data)


# ── TelegramBroadcastViewSet ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestTelegramBroadcastCreate:
    BROADCAST_URL = "/telegram/v1/broadcast/"

    @pytest.fixture(autouse=True)
    def bot_app(self, tenant):
        return TelegramBotApp.objects.create(
            tenant=tenant,
            bot_token="1234:BOK",
            bot_username="vs_bot",
            bot_user_id=1234,
        )

    @pytest.fixture
    def contact(self, tenant, user):
        return TenantContact.objects.create(
            tenant=tenant,
            phone="+919800000002",
            first_name="Ravi",
            telegram_chat_id=55555,
            created_by=user,
            updated_by=user,
        )

    def test_queued_with_message_is_accepted(self, auth_client, contact):
        resp = auth_client.post(
            self.BROADCAST_URL,
            {
                "name": "Good Broadcast",
                "recipients": [str(contact.pk)],
                "placeholder_data": {"message": "Hello {{first_name}}!"},
                "status": "QUEUED",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert Broadcast.objects.filter(platform=BroadcastPlatformChoices.TELEGRAM).exists()

    def test_queued_without_message_returns_400(self, auth_client, contact):
        resp = auth_client.post(
            self.BROADCAST_URL,
            {
                "name": "Empty Broadcast",
                "recipients": [str(contact.pk)],
                "placeholder_data": {},
                "status": "QUEUED",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "placeholder_data" in resp.data

    def test_queued_with_text_alias_is_accepted(self, auth_client, contact):
        resp = auth_client.post(
            self.BROADCAST_URL,
            {
                "name": "Text Alias Broadcast",
                "recipients": [str(contact.pk)],
                "placeholder_data": {"text": "Hi there!"},
                "status": "QUEUED",
            },
            format="json",
        )
        assert resp.status_code == 201

    def test_draft_without_message_is_accepted(self, auth_client, contact):
        """DRAFT broadcasts are exempt from content validation."""
        resp = auth_client.post(
            self.BROADCAST_URL,
            {
                "name": "Draft No Content",
                "recipients": [str(contact.pk)],
                "placeholder_data": {},
                "status": "DRAFT",
            },
            format="json",
        )
        assert resp.status_code == 201
