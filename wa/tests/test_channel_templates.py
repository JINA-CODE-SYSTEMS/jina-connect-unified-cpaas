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
class TestTelegramMediaTemplates:
    """Verify the Telegram media-template contract (Issue #143).

    Telegram templates use URL-based media via ``example_media_url``.
    File upload (tenant_media / media_handle) is not supported.
    """

    MEDIA_URL = "https://example.com/sample.jpg"

    def test_image_template_with_media_url_accepted(self, api_client):
        """POST IMAGE template with example_media_url → 201, url persisted."""
        payload = {
            "name": "tg_img_tpl",
            "element_name": "tg_img_tpl",
            "content": "See the image",
            "language_code": "en",
            "category": "MARKETING",
            "template_type": "IMAGE",
            "example_media_url": self.MEDIA_URL,
        }
        resp = api_client.post("/telegram/v1/templates/", payload, format="json")
        assert resp.status_code == 201
        assert resp.data["example_media_url"] == self.MEDIA_URL
        assert resp.data["template_type"] == "IMAGE"

    def test_image_template_without_media_url_rejected(self, api_client):
        """POST IMAGE template without example_media_url → 400 with explicit error."""
        payload = {
            "name": "tg_img_nurl",
            "element_name": "tg_img_nurl",
            "content": "No URL provided",
            "language_code": "en",
            "category": "MARKETING",
            "template_type": "IMAGE",
        }
        resp = api_client.post("/telegram/v1/templates/", payload, format="json")
        assert resp.status_code == 400
        assert "example_media_url" in resp.data

    def test_video_template_without_media_url_rejected(self, api_client):
        """POST VIDEO template without example_media_url → 400."""
        payload = {
            "name": "tg_vid_nurl",
            "element_name": "tg_vid_nurl",
            "content": "No URL provided",
            "language_code": "en",
            "category": "MARKETING",
            "template_type": "VIDEO",
        }
        resp = api_client.post("/telegram/v1/templates/", payload, format="json")
        assert resp.status_code == 400
        assert "example_media_url" in resp.data

    def test_document_template_with_media_url_accepted(self, api_client):
        """POST DOCUMENT template with example_media_url → 201."""
        payload = {
            "name": "tg_doc_tpl",
            "element_name": "tg_doc_tpl",
            "content": "See the document",
            "language_code": "en",
            "category": "UTILITY",
            "template_type": "DOCUMENT",
            "example_media_url": "https://example.com/sample.pdf",
        }
        resp = api_client.post("/telegram/v1/templates/", payload, format="json")
        assert resp.status_code == 201
        assert resp.data["example_media_url"] == "https://example.com/sample.pdf"

    def test_types_returns_only_telegram_applicable(self, api_client):
        """GET /telegram/v1/templates/types/ returns only TEXT, IMAGE, VIDEO, DOCUMENT."""
        resp = api_client.get("/telegram/v1/templates/types/")
        assert resp.status_code == 200
        values = {entry["value"] for entry in resp.data}
        assert values == {"TEXT", "IMAGE", "VIDEO", "DOCUMENT"}
        # WA-specific types must not be present
        assert "CAROUSEL" not in values
        assert "CATALOG" not in values
        assert "PRODUCT" not in values
        assert "ORDER_DETAILS" not in values

    def test_text_template_creates_without_media_url(self, api_client):
        """TEXT templates need no media URL."""
        payload = {
            "name": "tg_text_ok",
            "element_name": "tg_text_ok",
            "content": "Hello world",
            "language_code": "en",
            "category": "UTILITY",
            "template_type": "TEXT",
        }
        resp = api_client.post("/telegram/v1/templates/", payload, format="json")
        assert resp.status_code == 201


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
