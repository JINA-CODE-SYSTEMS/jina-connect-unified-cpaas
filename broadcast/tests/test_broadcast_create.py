"""Tests for broadcast viewset base-class create restriction (#124)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(
        username="bcast_test", email="bcast@test.local", mobile="+919100999990", password="testpass123"
    )
    tenant = Tenant.objects.create(name="Broadcast Test Tenant")
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
class TestBroadcastCreateDisabled:
    def test_post_broadcast_returns_405(self, auth_client):
        """#124: POST /broadcast/ on base viewset returns 405."""
        response = auth_client.post("/broadcast/", {"name": "test"}, format="json")
        assert response.status_code == 405

    def test_post_mobile_broadcast_returns_405(self, auth_client):
        """POST /mobile/broadcast/ (MobileBroadcastViewSet) is also blocked — channel-specific endpoints must be used."""
        response = auth_client.post("/mobile/broadcast/", {"name": "test"}, format="json")
        assert response.status_code == 405

    def test_subclass_create_is_not_blocked_by_base_405(self, db):
        """
        Regression: BroadcastViewSet.create() must only short-circuit with 405
        for the base class itself. Channel-specific subclasses (e.g.
        WABroadcastViewSet) call super().create() and were previously blocked,
        making it impossible to create any broadcast from any channel.
        """
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory

        from broadcast.viewsets.broadcast import BroadcastViewSet
        from tenants.models import Tenant, TenantRole, TenantUser

        User = get_user_model()

        # Create minimal user/tenant for request.user
        tenant = Tenant.objects.create(name="Dummy Tenant")
        user = User.objects.create_user(
            username="dummy_user", email="dummy@test.local", mobile="+919999999999", password="pass"
        )
        role, _ = TenantRole.objects.get_or_create(
            tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100}
        )
        TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)

        class DummyChannelBroadcastViewSet(BroadcastViewSet):
            pass

        factory = APIRequestFactory()
        request = factory.post("/dummy/", {}, format="json")
        # Force authenticate to avoid AnonymousUser issues
        request.user = user
        view = DummyChannelBroadcastViewSet()
        view.action_map = {"post": "create"}
        view.request = view.initialize_request(request)
        view.request.user = user  # Ensure user is set after initialization
        view.format_kwarg = None
        view.action = "create"
        view.kwargs = {}

        # The base-class guard must NOT trigger for subclasses.
        # We only assert the 405 short-circuit isn't returned; the real
        # CreateModelMixin path is exercised in channel-specific test suites
        # (e.g. wa/tests/) where full fixtures (WAApp, template, contacts)
        # are available.
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            # Falling through to the real create path raises ValidationError from
            # the serializer because the payload is empty. This proves the 405
            # guard was bypassed and we reached the serializer layer.
            view.create(view.request)
