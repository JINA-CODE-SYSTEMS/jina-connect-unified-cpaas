"""Tests for broadcast viewset base-class create restriction (#124)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantUser

User = get_user_model()


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(
        username="bcast_test", email="bcast@test.local", mobile="+919100999990", password="testpass123"
    )
    tenant = Tenant.objects.create(name="Broadcast Test Tenant")
    TenantUser.objects.create(user=user, tenant=tenant, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
class TestBroadcastCreateDisabled:
    def test_post_broadcast_returns_405(self, auth_client):
        """#124: POST /broadcast/ on base viewset returns 405."""
        response = auth_client.post("/broadcast/", {"name": "test"}, format="json")
        assert response.status_code == 405
