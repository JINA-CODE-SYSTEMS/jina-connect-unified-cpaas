"""
Regression test for null-tenant guard in TelegramBotAppViewSet.perform_create.
Ensures that users without a TenantUser association get PermissionDenied (403),
not AttributeError (500).
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.mark.django_db
class TestTelegramNullTenantGuard:
    """Test that POST /telegram/v1/bots/ with user having no tenant returns 403, not 500."""

    @patch("telegram.services.bot_client.TelegramBotClient.get_me")
    def test_bot_create_with_no_tenant_returns_403(self, mock_get_me):
        """User with no TenantUser should get 403 PermissionDenied, not 500."""
        # Mock successful bot token validation
        mock_get_me.return_value = {"id": 123456, "username": "testbot", "first_name": "Test Bot"}

        # Create a user with no tenant association
        user = User.objects.create_user(
            username="notenant_user",
            email="notenant@test.local",
            mobile="+919100999999",
            password="testpass123",
        )

        # Create API client and authenticate
        client = APIClient()
        client.force_authenticate(user=user)

        # Attempt to create a bot — should return 403, not 500
        response = client.post(
            "/telegram/v1/bots/",
            {"bot_token": "123456:ABC-DEF1234567890"},
            format="json",
        )

        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.data}"
        assert "no associated tenant" in str(response.data).lower()
