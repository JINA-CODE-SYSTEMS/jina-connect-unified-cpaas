"""Tests for #129: RCS ad-hoc send endpoint."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from rcs.models import RCSApp
from tenants.models import Tenant, TenantRole, TenantUser

User = get_user_model()


@pytest.fixture
def user_in_tenant(db):
    user = User.objects.create_user(
        username="rcs_send_user",
        email="rcs_send@test.local",
        mobile="+919100999996",
        password="testpass123",
    )
    tenant = Tenant.objects.create(name="RCS Send Tenant")
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)
    return user, tenant


@pytest.mark.django_db
class TestRCSSendAction:
    """POST /rcs/v1/messages/send/ — ticket #129."""

    def test_send_requires_auth(self):
        response = APIClient().post("/rcs/v1/messages/send/", {"chat_id": "+1", "text": "hi"}, format="json")
        assert response.status_code == 401

    def test_send_requires_text_or_media(self, user_in_tenant):
        user, _tenant = user_in_tenant
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post("/rcs/v1/messages/send/", {"chat_id": "+14155550100"}, format="json")
        assert response.status_code == 400
        assert "text" in str(response.data).lower() or "media_url" in str(response.data).lower()

    def test_send_returns_400_when_no_active_rcs_app(self, user_in_tenant):
        user, _tenant = user_in_tenant
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            "/rcs/v1/messages/send/",
            {"chat_id": "+14155550100", "text": "hello"},
            format="json",
        )
        assert response.status_code == 400
        assert "no active rcs app" in str(response.data).lower()

    def test_send_text_invokes_sender(self, user_in_tenant):
        user, tenant = user_in_tenant
        RCSApp.objects.create(
            tenant=tenant,
            provider="GOOGLE_RBM",
            agent_id="agent@rbm.goog",
            is_active=True,
            daily_limit=1000,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        with patch("rcs.services.message_sender.RCSMessageSender") as mock_sender_cls:
            mock_sender_cls.return_value.send_text.return_value = {
                "success": True,
                "message_id": "msg-1",
                "outbound_id": "1",
                "channel": "RCS",
                "error": None,
            }

            response = client.post(
                "/rcs/v1/messages/send/",
                {"chat_id": "+14155550100", "text": "hello"},
                format="json",
            )

        assert response.status_code == 200
        assert response.data["success"] is True
        mock_sender_cls.return_value.send_text.assert_called_once()
        # Media path NOT taken
        mock_sender_cls.return_value.send_media.assert_not_called()

    def test_send_media_invokes_sender(self, user_in_tenant):
        user, tenant = user_in_tenant
        RCSApp.objects.create(
            tenant=tenant,
            provider="GOOGLE_RBM",
            agent_id="agent2@rbm.goog",
            is_active=True,
            daily_limit=1000,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        with patch("rcs.services.message_sender.RCSMessageSender") as mock_sender_cls:
            mock_sender_cls.return_value.send_media.return_value = {
                "success": True,
                "message_id": "msg-2",
                "outbound_id": "2",
                "channel": "RCS",
                "error": None,
            }

            response = client.post(
                "/rcs/v1/messages/send/",
                {
                    "chat_id": "+14155550100",
                    "media_url": "https://example.com/img.jpg",
                    "media_type": "image",
                    "text": "caption",
                },
                format="json",
            )

        assert response.status_code == 200
        mock_sender_cls.return_value.send_media.assert_called_once()
        mock_sender_cls.return_value.send_text.assert_not_called()

    def test_send_rejects_invalid_e164_chat_id(self, user_in_tenant):
        """#129 review item 6: chat_id must be a valid E.164 phone number."""
        user, _tenant = user_in_tenant
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            "/rcs/v1/messages/send/",
            {"chat_id": "not-a-phone", "text": "hi"},
            format="json",
        )
        assert response.status_code == 400
        assert "chat_id" in response.data

    def test_send_rejects_contact_id_not_in_tenant(self, user_in_tenant):
        """#129 review item 7: contact_id not belonging to tenant returns 400."""
        user, tenant = user_in_tenant
        RCSApp.objects.create(
            tenant=tenant,
            provider="GOOGLE_RBM",
            agent_id="agent3@rbm.goog",
            is_active=True,
            daily_limit=1000,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            "/rcs/v1/messages/send/",
            {"chat_id": "+14155550100", "text": "hi", "contact_id": 999999},
            format="json",
        )
        assert response.status_code == 400
        assert "not found" in str(response.data).lower()
