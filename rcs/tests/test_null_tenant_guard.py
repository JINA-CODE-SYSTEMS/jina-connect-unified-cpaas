"""
Regression test for null-tenant guard in RCSAppViewSet.perform_create.
Ensures that users without a TenantUser association get PermissionDenied (403),
not AttributeError (500).
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.mark.django_db
class TestRCSNullTenantGuard:
    """Test that POST /rcs/v1/apps/ with user having no tenant returns 403, not 500."""

    def test_app_create_with_no_tenant_returns_403(self):
        """User with no TenantUser should get 403 PermissionDenied, not 500."""
        # Create a user with no tenant association
        user = User.objects.create_user(
            username="rcs_notenant_user",
            email="rcs_notenant@test.local",
            mobile="+919100999997",
            password="testpass123",
        )

        # Create API client and authenticate
        client = APIClient()
        client.force_authenticate(user=user)

        # Attempt to create an app — should return 403, not 500
        response = client.post(
            "/rcs/v1/apps/",
            {
                "provider": "GOOGLE_RBM",
                "agent_id": "test-agent@business.goog",
                "agent_name": "Test Agent",
                "provider_credentials": {"api_key": "test"},
            },
            format="json",
        )

        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.data}"
        assert "no associated tenant" in str(response.data).lower()
