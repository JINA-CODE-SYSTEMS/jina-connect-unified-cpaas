"""Tests for channel-scoped template viewsets (#97, #21 MRO fix)."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from wa.models import TemplateStatus, WATemplate


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name="Template Tenant")


@pytest.fixture()
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="tpl_test",
        email="tpl@test.com",
        mobile="+919400000001",
        password="testpass123",
    )


@pytest.fixture()
def role(tenant):
    from tenants.models import TenantRole

    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    return role


@pytest.fixture()
def tenant_user(tenant, user, role):
    from tenants.models import TenantUser

    return TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)


@pytest.fixture()
def api_client(tenant_user):
    client = APIClient()
    client.force_authenticate(user=tenant_user.user)
    return client


TEMPLATE_PAYLOAD = {
    "name": "Test Template",
    "element_name": "test_template",
    "content": "Hello {{name}}",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "TEXT",
}


@pytest.mark.django_db
class TestSMSTemplateViewSet:
    def test_create_sets_platform_and_tenant(self, api_client, tenant_user):
        """POST /sms/v1/templates/ creates an approved SMS template for the tenant (#21)."""
        resp = api_client.post("/sms/v1/templates/", TEMPLATE_PAYLOAD, format="json")

        assert resp.status_code == 201
        tpl = WATemplate.objects.get(pk=resp.data["id"])
        assert tpl.platform == "SMS"
        assert tpl.tenant == tenant_user.tenant
        assert tpl.status == TemplateStatus.APPROVED
        assert tpl.needs_sync is False

    def test_list_filters_by_platform(self, api_client, tenant_user):
        """GET /sms/v1/templates/ only returns SMS templates."""
        WATemplate.objects.create(name="sms_tpl", element_name="sms_tpl", platform="SMS", tenant=tenant_user.tenant)
        WATemplate.objects.create(name="tg_tpl", element_name="tg_tpl", platform="TELEGRAM", tenant=tenant_user.tenant)

        resp = api_client.get("/sms/v1/templates/")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.data["results"]]
        assert "sms_tpl" in names
        assert "tg_tpl" not in names


@pytest.mark.django_db
class TestTelegramTemplateViewSet:
    def test_create_sets_platform_and_tenant(self, api_client, tenant_user):
        """POST /telegram/v1/templates/ creates an approved TELEGRAM template."""
        resp = api_client.post("/telegram/v1/templates/", TEMPLATE_PAYLOAD, format="json")

        assert resp.status_code == 201
        tpl = WATemplate.objects.get(pk=resp.data["id"])
        assert tpl.platform == "TELEGRAM"
        assert tpl.tenant == tenant_user.tenant
        assert tpl.status == TemplateStatus.APPROVED
        assert tpl.needs_sync is False

    def test_list_filters_by_platform(self, api_client, tenant_user):
        """GET /telegram/v1/templates/ only returns TELEGRAM templates."""
        WATemplate.objects.create(name="tg_tpl", element_name="tg_tpl", platform="TELEGRAM", tenant=tenant_user.tenant)
        WATemplate.objects.create(name="sms_tpl2", element_name="sms_tpl2", platform="SMS", tenant=tenant_user.tenant)

        resp = api_client.get("/telegram/v1/templates/")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.data["results"]]
        assert "tg_tpl" in names
        assert "sms_tpl2" not in names


@pytest.mark.django_db
class TestRCSTemplateViewSet:
    def test_create_sets_platform_and_tenant(self, api_client, tenant_user):
        """POST /rcs/v1/templates/ creates an approved RCS template."""
        resp = api_client.post("/rcs/v1/templates/", TEMPLATE_PAYLOAD, format="json")

        assert resp.status_code == 201
        tpl = WATemplate.objects.get(pk=resp.data["id"])
        assert tpl.platform == "RCS"
        assert tpl.tenant == tenant_user.tenant
        assert tpl.status == TemplateStatus.APPROVED
        assert tpl.needs_sync is False


@pytest.mark.django_db
class TestChannelTemplateIsolation:
    def test_cross_tenant_template_not_visible(self, user, tenant_user):
        """Templates from tenant A are not visible to tenant B."""
        from django.contrib.auth import get_user_model

        from tenants.models import Tenant, TenantRole, TenantUser

        # Create a template owned by tenant_user's tenant
        WATemplate.objects.create(
            name="secret_tpl", element_name="secret_tpl", platform="SMS", tenant=tenant_user.tenant
        )

        # Create a second tenant + user
        other_tenant = Tenant.objects.create(name="Other")
        other_role = TenantRole.objects.get(tenant=other_tenant, slug="owner")
        other_user = get_user_model().objects.create_user(
            username="other_tpl", email="other_tpl@test.com", mobile="+919400000099", password="testpass123"
        )
        TenantUser.objects.create(tenant=other_tenant, user=other_user, role=other_role, is_active=True)

        client = APIClient()
        client.force_authenticate(user=other_user)

        resp = client.get("/sms/v1/templates/")
        assert resp.status_code == 200
        assert len(resp.data["results"]) == 0
