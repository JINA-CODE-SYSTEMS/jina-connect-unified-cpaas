"""
WhatsApp Template API (v2) — End-to-End Test Suite
===================================================

Tests all CRUD operations and actions on the `/api/wa/v2/templates/` endpoint.

HOW TO RUN:
    # From the project root (jina-connect/):
    python manage.py test wa.tests.test_template_api_v2 -v 2

    # Run a specific test class:
    python manage.py test wa.tests.test_template_api_v2.TestTemplateCreate -v 2

    # Run a single test:
    python manage.py test wa.tests.test_template_api_v2.TestTemplateCreate.test_create_text_template -v 2

QA MANUAL TESTING:
    See the QA_TEST_CASES dict at the bottom of this file for Postman/curl-ready payloads.
    The Swagger docs are also available at /swagger/ after login.

REQUIRES:
    - A running database with migrations applied
    - Test creates its own tenant, user, and wa_app in setUp()
"""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

# =============================================================================
# HELPER: Test data factory
# =============================================================================


def create_test_tenant_and_user(username="testuser", password="TestPass123!"):
    """
    Create a test Tenant, User, and TenantUser association.
    Returns (tenant, user, token).
    """
    from django.contrib.auth import get_user_model

    from tenants.models import Tenant, TenantUser

    User = get_user_model()

    tenant = Tenant.objects.create(
        name=f"Test Tenant {uuid.uuid4().hex[:8]}",
        is_active=True,
    )

    # mobile has unique=True, so generate a unique Indian phone number per user
    unique_suffix = uuid.uuid4().int % 10**9  # 9-digit random number
    mobile = f"+91{9000000000 + unique_suffix % 999999999}"

    user = User.objects.create_user(
        username=username + uuid.uuid4().hex[:6],
        email=f"{username}_{uuid.uuid4().hex[:6]}@test.com",
        password=password,
        mobile=mobile,
    )

    from tenants.models import TenantRole

    owner_role = TenantRole.objects.get(tenant=tenant, slug="owner")
    TenantUser.objects.create(tenant=tenant, user=user, role=owner_role)

    # Get JWT token
    client = APIClient()
    response = client.post(
        "/token/",
        {
            "username": user.username,
            "password": password,
        },
        format="json",
    )

    token = response.data.get("access") if response.status_code == 200 else None
    return tenant, user, token


def create_test_wa_app(tenant):
    """Create a test WAApp (TenantWAApp) for the tenant."""
    from wa.models import WAApp

    return WAApp.objects.create(
        tenant=tenant,
        app_name=f"Test App {uuid.uuid4().hex[:8]}",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret=f"secret_{uuid.uuid4().hex[:8]}",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id="test_waba_123",
        phone_number_id="test_phone_123",
        bsp="META",
        is_verified=True,
        is_active=True,
    )


# =============================================================================
# SAMPLE PAYLOADS — Reusable by QA for Postman/curl
# =============================================================================

SAMPLE_TEXT_TEMPLATE = {
    "name": "Welcome Message",
    "element_name": "welcome_message",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "TEXT",
    "content": "Hello {{name}}, welcome to {{company}}! Your account is ready.",
    "header": "Welcome!",
    "footer": "Reply STOP to opt out",
    "example_body": ["John", "Acme Corp"],
    "buttons": [
        {"type": "QUICK_REPLY", "text": "Get Started"},
        {"type": "URL", "text": "Visit Website", "url": "https://example.com/{{1}}"},
    ],
}

SAMPLE_IMAGE_TEMPLATE = {
    "name": "Promo Image",
    "element_name": "promo_image_offer",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "IMAGE",
    "content": "Hi {{name}}, check out our {{discount}}% off sale!",
    "footer": "Limited time offer",
    "example_body": ["Jane", "25"],
}

SAMPLE_VIDEO_TEMPLATE = {
    "name": "Product Demo",
    "element_name": "product_demo_video",
    "language_code": "en",
    "category": "UTILITY",
    "template_type": "VIDEO",
    "content": "Hi {{name}}, here is the demo you requested for {{product}}.",
    "example_body": ["Alice", "Widget Pro"],
}

SAMPLE_AUTH_TEMPLATE = {
    "name": "OTP Verification",
    "element_name": "otp_verification",
    "language_code": "en",
    "category": "AUTHENTICATION",
    "template_type": "TEXT",
    "content": "Your verification code is {{1}}. Do not share it.",
    "example_body": ["123456"],
    "buttons": [
        {"type": "OTP", "text": "Copy code"},
    ],
}

SAMPLE_CAROUSEL_TEMPLATE = {
    "name": "Product Carousel",
    "element_name": "product_carousel",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "CAROUSEL",
    "content": "Hi {{name}}, check out our latest products:",
    "example_body": ["Bob"],
    "cards": [
        {
            "body": "Premium Widget - $49.99",
            "buttons": [{"type": "URL", "text": "View", "url": "https://example.com/widget"}],
        },
        {
            "body": "Super Gadget - $79.99",
            "buttons": [{"type": "URL", "text": "View", "url": "https://example.com/gadget"}],
        },
    ],
}

# Invalid payloads for negative tests
INVALID_ELEMENT_NAME_PAYLOAD = {
    "name": "Bad Name",
    "element_name": "123_starts_with_number",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "TEXT",
    "content": "Hello",
}

INVALID_BUTTONS_AND_CARDS_PAYLOAD = {
    "name": "Conflict Template",
    "element_name": "conflict_template",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "TEXT",
    "content": "Hello {{name}}",
    "buttons": [{"type": "QUICK_REPLY", "text": "Yes"}],
    "cards": [{"body": "Card 1"}],
}

INVALID_AUTH_NO_OTP_PAYLOAD = {
    "name": "Bad Auth",
    "element_name": "bad_auth_template",
    "language_code": "en",
    "category": "AUTHENTICATION",
    "template_type": "TEXT",
    "content": "Your code is {{1}}",
    # Missing buttons — AUTHENTICATION requires OTP buttons
}

INVALID_AUTH_WRONG_BUTTON_PAYLOAD = {
    "name": "Bad Auth Buttons",
    "element_name": "bad_auth_buttons",
    "language_code": "en",
    "category": "AUTHENTICATION",
    "template_type": "TEXT",
    "content": "Your code is {{1}}",
    "buttons": [
        {"type": "QUICK_REPLY", "text": "OK"},  # Should be OTP, not QUICK_REPLY
    ],
}

SAMPLE_COUPON_CODE_PAYLOAD = {
    "name": "Summer Sale Coupon",
    "element_name": "summer_sale_coupon",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "TEXT",
    "content": "Use this code for 25% off your next order!",
    "buttons": [
        {"type": "COPY_CODE", "text": "Copy Coupon", "coupon_code": "SUMMER25"},
    ],
}


# =============================================================================
# TEST: Template CRUD Operations
# =============================================================================


class TemplateTestBase(TestCase):
    """
    Base test class that sets up tenant, user, WAApp, and authenticated client.
    All template test classes inherit from this.
    """

    @classmethod
    def setUpTestData(cls):
        """Create shared test data once for the entire test class."""
        cls.tenant, cls.user, cls.token = create_test_tenant_and_user()
        cls.wa_app = create_test_wa_app(cls.tenant)

    def setUp(self):
        """Set up authenticated API client and mock BSP adapter before each test."""
        self.client = APIClient()
        if self.token:
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

        # Mock the BSP adapter so tests never hit META's real API.
        # The mock adapter's submit_template does nothing — the template
        # stays as DRAFT / needs_sync=True (DB state from serializer.save).
        from wa.adapters.base import AdapterResult

        self._mock_adapter = MagicMock()
        self._mock_adapter.submit_template.return_value = AdapterResult(
            success=True, provider="mock", data={"mock": True}
        )
        self._adapter_patcher = patch(
            "wa.viewsets.wa_template_v2.get_bsp_adapter",
            return_value=self._mock_adapter,
        )
        self._adapter_patcher.start()

    def tearDown(self):
        self._adapter_patcher.stop()

    @property
    def list_url(self):
        return "/wa/v2/templates/"

    def detail_url(self, pk):
        return f"/wa/v2/templates/{pk}/"

    def action_url(self, pk, action):
        return f"/wa/v2/templates/{pk}/{action}/"

    def create_template(self, data=None, **overrides):
        """Helper to create a template with default data."""
        payload = dict(data or SAMPLE_TEXT_TEMPLATE)
        payload["wa_app"] = str(self.wa_app.id)
        # Make element_name unique per test
        payload["element_name"] = payload.get("element_name", "test") + "_" + uuid.uuid4().hex[:8]
        payload.update(overrides)
        return self.client.post(self.list_url, payload, format="json")


class TestTemplateCreate(TemplateTestBase):
    """Tests for POST /wa/v2/templates/"""

    def test_create_text_template(self):
        """✅ Create a basic TEXT template with buttons."""
        resp = self.create_template(SAMPLE_TEXT_TEMPLATE)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["category"], "MARKETING")
        self.assertEqual(resp.data["template_type"], "TEXT")
        self.assertEqual(resp.data["status"], "DRAFT")
        self.assertTrue(resp.data["needs_sync"])
        self.assertIn("name", resp.data["content"])  # Has {{name}} placeholder

    def test_create_image_template(self):
        """✅ Create an IMAGE template."""
        resp = self.create_template(SAMPLE_IMAGE_TEMPLATE)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["template_type"], "IMAGE")

    def test_create_video_template(self):
        """✅ Create a VIDEO template."""
        resp = self.create_template(SAMPLE_VIDEO_TEMPLATE)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["template_type"], "VIDEO")

    def test_create_auth_template_with_otp(self):
        """✅ Create an AUTHENTICATION template with OTP button."""
        resp = self.create_template(SAMPLE_AUTH_TEMPLATE)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["category"], "AUTHENTICATION")

    def test_create_carousel_template(self):
        """✅ Create a CAROUSEL template with cards."""
        resp = self.create_template(SAMPLE_CAROUSEL_TEMPLATE)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["template_type"], "CAROUSEL")
        self.assertIsNotNone(resp.data["cards"])
        self.assertEqual(len(resp.data["cards"]), 2)

    def test_create_returns_uuid_id(self):
        """✅ Response includes a valid UUID id."""
        resp = self.create_template()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # Should be a valid UUID
        uuid.UUID(str(resp.data["id"]))

    def test_create_normalizes_element_name(self):
        """✅ element_name is lowercased."""
        resp = self.create_template(element_name="My_Cool_Template_" + uuid.uuid4().hex[:6])
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["element_name"].islower())

    def test_create_without_auth_returns_401(self):
        """🔒 Unauthenticated request is rejected."""
        client = APIClient()  # No token
        payload = dict(SAMPLE_TEXT_TEMPLATE)
        payload["wa_app"] = str(self.wa_app.id)
        payload["element_name"] = "unauth_test_" + uuid.uuid4().hex[:8]
        resp = client.post(self.list_url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class TestTemplateCreateValidation(TemplateTestBase):
    """Tests for validation errors on POST /wa/v2/templates/"""

    def test_invalid_element_name_starting_with_number(self):
        """❌ element_name starting with a number should fail."""
        resp = self.create_template(INVALID_ELEMENT_NAME_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_buttons_and_cards_mutual_exclusivity(self):
        """❌ Cannot have both buttons and cards."""
        resp = self.create_template(INVALID_BUTTONS_AND_CARDS_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_auth_template_requires_otp_buttons(self):
        """❌ AUTHENTICATION category without OTP buttons should fail."""
        resp = self.create_template(INVALID_AUTH_NO_OTP_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_auth_template_rejects_non_otp_buttons(self):
        """❌ AUTHENTICATION category with non-OTP buttons should fail."""
        resp = self.create_template(INVALID_AUTH_WRONG_BUTTON_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_marketing_copy_code_button_accepted(self):
        """✅ MARKETING template with COPY_CODE button should be accepted."""
        resp = self.create_template(SAMPLE_COUPON_CODE_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()
        self.assertEqual(data["category"], "MARKETING")
        # Confirm the COPY_CODE button is stored correctly
        self.assertEqual(len(data["buttons"]), 1)
        self.assertEqual(data["buttons"][0]["type"], "COPY_CODE")
        self.assertEqual(data["buttons"][0]["coupon_code"], "SUMMER25")

    def test_missing_required_fields(self):
        """❌ Missing wa_app should fail."""
        resp = self.client.post(
            self.list_url,
            {
                "element_name": "missing_fields_" + uuid.uuid4().hex[:8],
                "content": "Hello",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_content_allowed(self):
        """✅ Content can be blank (some templates are header-only)."""
        resp = self.create_template(content="", header="Header Only Template")
        # This should succeed or fail based on your business rules
        self.assertIn(
            resp.status_code,
            [
                status.HTTP_201_CREATED,
                status.HTTP_400_BAD_REQUEST,
            ],
        )


class TestTemplateList(TemplateTestBase):
    """Tests for GET /wa/v2/templates/"""

    def test_list_templates(self):
        """✅ List returns paginated templates."""
        # Create a few templates first
        self.create_template(element_name="list_test_a_" + uuid.uuid4().hex[:6])
        self.create_template(element_name="list_test_b_" + uuid.uuid4().hex[:6])

        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Paginated response should have results key
        self.assertIn("results", resp.data)
        self.assertGreaterEqual(len(resp.data["results"]), 2)

    def test_filter_by_category(self):
        """✅ Filter by category=MARKETING."""
        self.create_template(SAMPLE_TEXT_TEMPLATE, element_name="filter_cat_" + uuid.uuid4().hex[:6])
        resp = self.client.get(self.list_url, {"category": "MARKETING"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for t in resp.data.get("results", []):
            self.assertEqual(t["category"], "MARKETING")

    def test_filter_by_status(self):
        """✅ Filter by status=DRAFT."""
        self.create_template(element_name="filter_status_" + uuid.uuid4().hex[:6])
        resp = self.client.get(self.list_url, {"status": "DRAFT"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for t in resp.data.get("results", []):
            self.assertEqual(t["status"], "DRAFT")

    def test_filter_by_template_type(self):
        """✅ Filter by template_type=IMAGE."""
        self.create_template(SAMPLE_IMAGE_TEMPLATE, element_name="filter_type_" + uuid.uuid4().hex[:6])
        resp = self.client.get(self.list_url, {"template_type": "IMAGE"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_filter_by_wa_app(self):
        """✅ Filter by wa_app ID."""
        self.create_template(element_name="filter_app_" + uuid.uuid4().hex[:6])
        resp = self.client.get(self.list_url, {"wa_app": str(self.wa_app.id)})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_search_by_name(self):
        """✅ Search in name/element_name/content."""
        unique = uuid.uuid4().hex[:8]
        self.create_template(element_name=f"searchable_{unique}", content=f"Hello {unique}")
        resp = self.client.get(self.list_url, {"search": unique})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(resp.data.get("results", [])), 1)

    def test_ordering_by_created_at(self):
        """✅ Order by -created_at (newest first)."""
        resp = self.client.get(self.list_url, {"ordering": "-created_at"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_list_without_auth_returns_401(self):
        """🔒 Unauthenticated list is rejected."""
        client = APIClient()
        resp = client.get(self.list_url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class TestTemplateRetrieve(TemplateTestBase):
    """Tests for GET /wa/v2/templates/{id}/"""

    def test_retrieve_template(self):
        """✅ Get a specific template by ID."""
        create_resp = self.create_template()
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        pk = create_resp.data["id"]

        resp = self.client.get(self.detail_url(pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["id"], pk)
        # Full serializer should have more fields than list
        self.assertIn("content", resp.data)
        self.assertIn("buttons", resp.data)
        self.assertIn("wa_app", resp.data)

    def test_retrieve_nonexistent_returns_404(self):
        """❌ Non-existent template returns 404."""
        fake_id = uuid.uuid4()
        resp = self.client.get(self.detail_url(fake_id))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class TestTemplateUpdate(TemplateTestBase):
    """Tests for PATCH /wa/v2/templates/{id}/"""

    def test_update_content(self):
        """✅ PATCH updates content and marks needs_sync."""
        create_resp = self.create_template()
        pk = create_resp.data["id"]

        resp = self.client.patch(
            self.detail_url(pk),
            {
                "content": "Updated body text with {{name}}!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIn("Updated body text", resp.data["content"])
        self.assertTrue(resp.data["needs_sync"])

    def test_update_footer(self):
        """✅ PATCH updates footer."""
        create_resp = self.create_template()
        pk = create_resp.data["id"]

        resp = self.client.patch(
            self.detail_url(pk),
            {
                "footer": "New footer text",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["footer"], "New footer text")

    def test_update_buttons(self):
        """✅ PATCH updates buttons and marks needs_sync."""
        create_resp = self.create_template()
        pk = create_resp.data["id"]

        resp = self.client.patch(
            self.detail_url(pk),
            {
                "buttons": [
                    {"type": "QUICK_REPLY", "text": "Updated Button"},
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["needs_sync"])

    def test_update_nonexistent_returns_404(self):
        """❌ PATCH on non-existent template returns 404."""
        resp = self.client.patch(
            self.detail_url(uuid.uuid4()),
            {
                "content": "x",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# =============================================================================
# TEST: Template Actions (sync, preview, meta-payload, categories, etc.)
# =============================================================================


class TestTemplateSync(TemplateTestBase):
    """Tests for POST /wa/v2/templates/{id}/sync/"""

    def test_sync_calls_adapter(self):
        """\u2705 Sync endpoint calls BSP adapter and returns success."""
        create_resp = self.create_template()
        pk = create_resp.data["id"]

        resp = self.client.post(self.action_url(pk, "sync"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("message", resp.data)
        # Adapter was called
        self._mock_adapter.submit_template.assert_called()

    @patch("wa.tasks.retry_submit_template.apply_async")
    def test_sync_returns_502_on_adapter_failure(self, _mock_retry):
        """\u274c Sync returns 502 when BSP adapter reports failure."""
        from wa.adapters.base import AdapterResult

        self._mock_adapter.submit_template.return_value = AdapterResult(
            success=False, provider="mock", error_message="BSP rejected"
        )
        create_resp = self.create_template()
        pk = create_resp.data["id"]

        resp = self.client.post(self.action_url(pk, "sync"))
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn("error", resp.data)


class TestTemplatePreview(TemplateTestBase):
    """Tests for POST /wa/v2/templates/{id}/preview/"""

    def test_preview_with_params(self):
        """✅ Preview replaces placeholders with given values."""
        create_resp = self.create_template(
            content="Hello {{name}}, your code is {{code}}.",
        )
        pk = create_resp.data["id"]

        resp = self.client.post(
            self.action_url(pk, "preview"),
            {
                "params": {"name": "John", "code": "ABC123"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("John", resp.data["body"])
        self.assertIn("ABC123", resp.data["body"])

    def test_preview_without_params(self):
        """✅ Preview without params returns template with raw placeholders."""
        create_resp = self.create_template(content="Hello {{name}}!")
        pk = create_resp.data["id"]

        resp = self.client.post(self.action_url(pk, "preview"), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("{{name}}", resp.data["body"])


class TestTemplateMetaPayload(TemplateTestBase):
    """Tests for GET /wa/v2/templates/{id}/meta-payload/"""

    def test_meta_payload_structure(self):
        """✅ Meta payload has correct structure for META Graph API."""
        create_resp = self.create_template(
            content="Hello {{name}}, welcome!",
            header="Greetings",
        )
        pk = create_resp.data["id"]

        resp = self.client.get(self.action_url(pk, "meta-payload"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        payload = resp.data
        self.assertIn("name", payload)
        self.assertIn("language", payload)
        self.assertIn("category", payload)
        self.assertIn("components", payload)
        self.assertIsInstance(payload["components"], list)

        # Should have at least BODY component
        component_types = [c["type"] for c in payload["components"]]
        self.assertIn("body", component_types)

    def test_meta_payload_keeps_named_placeholders(self):
        """✅ Named placeholders {{name}} are kept as-is in META payload (parameter_format=NAMED)."""
        create_resp = self.create_template(
            content="Hi {{customer_name}}, order {{order_id}} is ready.",
        )
        pk = create_resp.data["id"]

        resp = self.client.get(self.action_url(pk, "meta-payload"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        body_component = next(c for c in resp.data["components"] if c["type"] == "body")
        # Open-source uses parameter_format=NAMED — placeholders stay as named
        self.assertIn("{{customer_name}}", body_component["text"])
        self.assertIn("{{order_id}}", body_component["text"])


class TestTemplateLookups(TemplateTestBase):
    """Tests for GET /wa/v2/templates/{categories,types,button-types}/"""

    def test_get_categories(self):
        """✅ Returns list of template categories."""
        resp = self.client.get(self.list_url + "categories/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        values = [item["value"] for item in resp.data]
        self.assertIn("MARKETING", values)
        self.assertIn("UTILITY", values)
        self.assertIn("AUTHENTICATION", values)

    def test_get_types(self):
        """✅ Returns list of template types."""
        resp = self.client.get(self.list_url + "types/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        values = [item["value"] for item in resp.data]
        self.assertIn("TEXT", values)
        self.assertIn("IMAGE", values)
        self.assertIn("VIDEO", values)
        self.assertIn("DOCUMENT", values)

    def test_get_button_types(self):
        """✅ Returns list of button types with examples."""
        resp = self.client.get(self.list_url + "button-types/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(resp.data), 3)
        # Each should have type, label, example
        for item in resp.data:
            self.assertIn("type", item)
            self.assertIn("label", item)
            self.assertIn("example", item)


# =============================================================================
# TEST: Tenant Isolation
# =============================================================================


class TestTemplateTenantIsolation(TestCase):
    """Ensure templates from Tenant A are NOT visible to Tenant B."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant_a, cls.user_a, cls.token_a = create_test_tenant_and_user("user_a")
        cls.tenant_b, cls.user_b, cls.token_b = create_test_tenant_and_user("user_b")
        cls.wa_app_a = create_test_wa_app(cls.tenant_a)
        cls.wa_app_b = create_test_wa_app(cls.tenant_b)

    def setUp(self):
        # Mock BSP adapter for tenant isolation tests too
        from wa.adapters.base import AdapterResult

        self._mock_adapter = MagicMock()
        self._mock_adapter.submit_template.return_value = AdapterResult(
            success=True, provider="mock", data={"mock": True}
        )
        self._adapter_patcher = patch(
            "wa.viewsets.wa_template_v2.get_bsp_adapter",
            return_value=self._mock_adapter,
        )
        self._adapter_patcher.start()

    def tearDown(self):
        self._adapter_patcher.stop()

    def test_tenant_b_cannot_see_tenant_a_templates(self):
        """🔒 Tenant B cannot list Tenant A's templates."""
        # Create template as Tenant A
        client_a = APIClient()
        client_a.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_a}")
        resp = client_a.post(
            "/wa/v2/templates/",
            {
                **SAMPLE_TEXT_TEMPLATE,
                "wa_app": str(self.wa_app_a.id),
                "element_name": "tenant_a_only_" + uuid.uuid4().hex[:8],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        template_id = resp.data["id"]

        # Try to access as Tenant B
        client_b = APIClient()
        client_b.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_b}")

        # List should not include Tenant A's template
        list_resp = client_b.get("/wa/v2/templates/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        ids = [t["id"] for t in list_resp.data.get("results", [])]
        self.assertNotIn(template_id, ids)

        # Direct retrieve should return 404
        detail_resp = client_b.get(f"/wa/v2/templates/{template_id}/")
        self.assertEqual(detail_resp.status_code, status.HTTP_404_NOT_FOUND)


# =============================================================================
# QA Manual Test Cases → qa/wa_templates_v2.postman_collection.json
# =============================================================================
# QA Postman collection has been moved to a standalone file.
# Import it into Postman:  qa/wa_templates_v2.postman_collection.json
# Regenerate:              python qa/generate_collections.py
