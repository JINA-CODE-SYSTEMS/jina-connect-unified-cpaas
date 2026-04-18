"""Tests for channel template create with missing tenant (#126)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def user_no_tenant(db):
    """Authenticated user with no TenantUser record."""
    user = User.objects.create_user(
        username="orphan_tpl", email="orphan_tpl@test.local", mobile="+919100999991", password="testpass123"
    )
    return user


@pytest.mark.django_db
class TestChannelTemplateCreateNoTenant:
    def test_create_returns_400_when_no_tenant(self, user_no_tenant):
        """#126: _ChannelTemplateMixin.create() raises ValidationError when tenant_user is None."""
        client = APIClient()
        client.force_authenticate(user=user_no_tenant)

        # Use the Telegram template endpoint (any channel template would work)
        response = client.post(
            "/telegram/v1/templates/",
            {"element_name": "test_template", "category": "UTILITY", "language": "en"},
            format="json",
        )
        assert response.status_code == 400
        assert "tenant" in str(response.data).lower()
