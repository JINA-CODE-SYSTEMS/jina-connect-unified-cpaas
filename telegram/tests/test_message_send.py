"""Tests for Telegram message sending with contact_id lookup."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from contacts.models import TenantContact
from telegram.models import TelegramBotApp
from tenants.models import Tenant, Role, TenantUser

User = get_user_model()


@pytest.mark.django_db
class TestTelegramMessageSendWithContactId:
    """Test Telegram message sending endpoint with contact_id parameter."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test data."""
        # Create tenant
        self.tenant = Tenant.objects.create(code="test-tenant", name="Test Tenant")
        
        # Create role
        self.role = Role.objects.create(
            tenant=self.tenant,
            name="Admin",
            description="Admin role",
        )
        self.role.permissions.create(permission="inbox.reply")
        
        # Create user
        self.user = User.objects.create_user(
            username="+911234567890",
            password="testpass123",
        )
        
        # Create tenant user
        TenantUser.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=self.role,
        )
        
        # Create Telegram bot
        self.bot_app = TelegramBotApp.objects.create(
            tenant=self.tenant,
            name="Test Bot",
            bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
            bot_username="testbot",
            is_active=True,
        )
        
        # Create contact with telegram_chat_id
        self.contact = TenantContact.objects.create(
            tenant=self.tenant,
            first_name="Test",
            last_name="User",
            phone="+919876543210",
            telegram_chat_id="987654321",
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_send_message_with_contact_id_only(self, mocker):
        """Test sending message with only contact_id (no chat_id)."""
        # Mock the Telegram API call
        mock_sender = mocker.patch("telegram.services.message_sender.TelegramMessageSender.send_text")
        mock_sender.return_value = {"success": True, "message_id": 123}
        
        response = self.client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": self.contact.id,
                "text": "Hello from test",
            },
            format="json",
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["success"] is True
        
        # Verify send_text was called with correct chat_id from contact
        mock_sender.assert_called_once()
        call_kwargs = mock_sender.call_args.kwargs
        assert call_kwargs["chat_id"] == "987654321"
        assert call_kwargs["text"] == "Hello from test"
        assert call_kwargs["contact"] == self.contact

    def test_send_message_with_chat_id_only(self, mocker):
        """Test sending message with only chat_id (original behavior)."""
        mock_sender = mocker.patch("telegram.services.message_sender.TelegramMessageSender.send_text")
        mock_sender.return_value = {"success": True, "message_id": 123}
        
        response = self.client.post(
            "/telegram/v1/messages/send/",
            {
                "chat_id": "111222333",
                "text": "Hello from test",
            },
            format="json",
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["success"] is True
        
        # Verify send_text was called with provided chat_id
        mock_sender.assert_called_once()
        call_kwargs = mock_sender.call_args.kwargs
        assert call_kwargs["chat_id"] == "111222333"
        assert call_kwargs["text"] == "Hello from test"

    def test_send_message_with_both_chat_id_and_contact_id(self, mocker):
        """Test that chat_id takes precedence when both are provided."""
        mock_sender = mocker.patch("telegram.services.message_sender.TelegramMessageSender.send_text")
        mock_sender.return_value = {"success": True, "message_id": 123}
        
        response = self.client.post(
            "/telegram/v1/messages/send/",
            {
                "chat_id": "explicit_chat_id",
                "contact_id": self.contact.id,
                "text": "Hello from test",
            },
            format="json",
        )
        
        assert response.status_code == status.HTTP_200_OK
        
        # Verify explicit chat_id was used (not looked up from contact)
        call_kwargs = mock_sender.call_args.kwargs
        assert call_kwargs["chat_id"] == "explicit_chat_id"

    def test_send_message_without_chat_id_or_contact_id(self):
        """Test validation error when neither chat_id nor contact_id provided."""
        response = self.client.post(
            "/telegram/v1/messages/send/",
            {
                "text": "Hello from test",
            },
            format="json",
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "chat_id" in str(response.data) or "contact_id" in str(response.data)

    def test_send_message_with_invalid_contact_id(self):
        """Test error when contact_id does not exist."""
        response = self.client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": 99999,
                "text": "Hello from test",
            },
            format="json",
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not found" in response.json()["error"].lower()

    def test_send_message_with_contact_without_telegram_chat_id(self):
        """Test error when contact doesn't have telegram_chat_id."""
        # Create contact without telegram_chat_id
        contact_no_telegram = TenantContact.objects.create(
            tenant=self.tenant,
            first_name="No",
            last_name="Telegram",
            phone="+919999999999",
            telegram_chat_id=None,
        )
        
        response = self.client.post(
            "/telegram/v1/messages/send/",
            {
                "contact_id": contact_no_telegram.id,
                "text": "Hello from test",
            },
            format="json",
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "telegram_chat_id" in response.json()["error"].lower()
