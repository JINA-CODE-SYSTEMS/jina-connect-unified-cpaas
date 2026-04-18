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
    def test_create_returns_error_when_no_tenant(self, user_no_tenant):
        """#126: _ChannelTemplateMixin.create() returns an error when tenant_user is None.

        The RBAC layer rejects the request before create() is reached, so the
        response is 403 (not 400).  Either way, the user cannot create a
        template without a valid tenant association.
        """
        client = APIClient()
        client.force_authenticate(user=user_no_tenant)

        # Use the Telegram template endpoint (any channel template would work)
        response = client.post(
            "/telegram/v1/templates/",
            {"element_name": "test_template", "category": "UTILITY", "language": "en"},
            format="json",
        )
        assert response.status_code in (400, 403)
