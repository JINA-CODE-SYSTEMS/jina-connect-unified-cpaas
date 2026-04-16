"""
Regression test for null-tenant guard in SMSAppViewSet.perform_create.
Ensures that users without a TenantUser association get PermissionDenied (403),
not AttributeError (500).
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.mark.django_db
class TestSMSNullTenantGuard:
    """Test that POST /sms/v1/apps/ with user having no tenant returns 403, not 500."""

    def test_app_create_with_no_tenant_returns_403(self):
        """User with no TenantUser should get 403 PermissionDenied, not 500."""
        # Create a user with no tenant association
        user = User.objects.create_user(
            username="sms_notenant_user",
            email="sms_notenant@test.local",
            mobile="+919100999998",
            password="testpass123",
        )

        # Create API client and authenticate
        client = APIClient()
        client.force_authenticate(user=user)

        # Attempt to create an app — should return 403, not 500
        response = client.post(
            "/sms/v1/apps/",
            {
                "provider": "TWILIO",
                "sender_id": "+1234567890",
                "provider_credentials": {"account_sid": "test", "auth_token": "test"},
            },
            format="json",
        )

        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.data}"
        assert "no associated tenant" in str(response.data).lower()
