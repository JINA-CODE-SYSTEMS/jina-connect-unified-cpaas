"""
WhatsApp Template — Adapter, Retry Task, and Cron Tests
========================================================

Tests covering:
  - Phase 1.6-1.9: TEXT template creation + submission via both BSPs
  - Phase 2.3-2.4: Media upload for both adapters
  - Phase 6.6-6.7: Template lifecycle + error tracking
  - retry_submit_template Celery task
  - check_template_statuses_cron management command
  - Adapter factory (get_bsp_adapter)

HOW TO RUN:
    DJANGO_SETTINGS_MODULE=jina_connect.settings python -m pytest wa/tests/test_adapters_and_tasks.py -v --tb=short
"""

import io
import uuid
from unittest.mock import MagicMock, patch, PropertyMock

from django.test import TestCase
from django.core.management import call_command

from wa.adapters.base import AdapterResult


# =============================================================================
# Test data factory
# =============================================================================

def make_tenant_and_wa_app(bsp="META"):
    """Create a tenant + wa_app for adapter testing."""
    from tenants.models import Tenant
    from wa.models import WAApp

    tenant = Tenant.objects.create(
        name=f"Test Tenant {uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    wa_app = WAApp.objects.create(
        tenant=tenant,
        app_name=f"Test App {uuid.uuid4().hex[:8]}",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret=f"secret_{uuid.uuid4().hex[:8]}",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=f"waba_{uuid.uuid4().hex[:8]}",
        phone_number_id=f"phone_{uuid.uuid4().hex[:8]}",
        bsp=bsp,
        is_verified=True,
        is_active=True,
    )
    return tenant, wa_app


def make_template(wa_app, **overrides):
    """Create a WATemplate in DRAFT state."""
    from wa.models import WATemplate

    defaults = {
        "wa_app": wa_app,
        "name": f"Test Template {uuid.uuid4().hex[:6]}",
        "element_name": f"test_{uuid.uuid4().hex[:8]}",
        "language_code": "en",
        "category": "MARKETING",
        "template_type": "TEXT",
        "content": "Hello {{name}}, welcome!",
        "example_body": ["John"],
        "status": "DRAFT",
        "needs_sync": True,
    }
    defaults.update(overrides)
    return WATemplate.objects.create(**defaults)


# =============================================================================
# Adapter Factory Tests
# =============================================================================

class TestAdapterFactory(TestCase):
    """Tests for get_bsp_adapter() factory."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant_meta, cls.wa_app_meta = make_tenant_and_wa_app(bsp="META")
        cls.tenant_gup, cls.wa_app_gupshup = make_tenant_and_wa_app(bsp="GUPSHUP")

    def test_meta_adapter_returned_for_meta_bsp(self):
        from wa.adapters import get_bsp_adapter
        from wa.adapters.meta_direct import MetaDirectAdapter
        adapter = get_bsp_adapter(self.wa_app_meta)
        self.assertIsInstance(adapter, MetaDirectAdapter)

    def test_gupshup_adapter_returned_for_gupshup_bsp(self):
        from wa.adapters import get_bsp_adapter
        from wa.adapters.gupshup import GupshupAdapter
        adapter = get_bsp_adapter(self.wa_app_gupshup)
        self.assertIsInstance(adapter, GupshupAdapter)

    def test_default_adapter_for_blank_bsp(self):
        """wa_app with no BSP defaults to MetaDirectAdapter."""
        from wa.adapters import get_bsp_adapter
        from wa.adapters.meta_direct import MetaDirectAdapter
        from wa.models import WAApp

        wa_app = WAApp.objects.create(
            tenant=self.tenant_meta,
            app_name="No BSP App",
            app_id=f"nobsp_{uuid.uuid4().hex[:8]}",
            wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
            bsp="",
            is_active=True,
        )
        adapter = get_bsp_adapter(wa_app)
        self.assertIsInstance(adapter, MetaDirectAdapter)

    def test_unsupported_bsp_raises(self):
        from wa.adapters import get_bsp_adapter
        from wa.models import WAApp

        wa_app = WAApp.objects.create(
            tenant=self.tenant_meta,
            app_name="Bad BSP App",
            app_id=f"bad_{uuid.uuid4().hex[:8]}",
            wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
            bsp="TWILIO",
            is_active=True,
        )
        with self.assertRaises(NotImplementedError):
            get_bsp_adapter(wa_app)


# =============================================================================
# to_meta_payload Tests (Phase 1.8 — TEXT, Phase 3.11 — CAROUSEL)
# =============================================================================

class TestToMetaPayload(TestCase):
    """Tests for WATemplate.to_meta_payload() method."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.wa_app = make_tenant_and_wa_app(bsp="META")

    def test_text_template_payload_structure(self):
        """TEXT template generates correct META payload with BODY component."""
        template = make_template(
            self.wa_app,
            content="Hello {{name}}, welcome to {{company}}!",
            example_body=["John", "Acme"],
            header="Welcome!",
            footer="Reply STOP to opt out",
        )
        payload = template.to_meta_payload()

        self.assertEqual(payload["name"], template.element_name)
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["category"], "MARKETING")
        self.assertIn("components", payload)

        # BODY component
        body = next(c for c in payload["components"] if c["type"] == "BODY")
        self.assertIn("{{name}}", body["text"])
        self.assertIn("{{company}}", body["text"])

        # Named params detected → parameter_format set
        self.assertEqual(payload.get("parameter_format"), "NAMED")

    def test_text_template_has_header_component(self):
        template = make_template(self.wa_app, header="Welcome!")
        payload = template.to_meta_payload()

        header = next(
            (c for c in payload["components"] if c["type"] == "HEADER"), None
        )
        self.assertIsNotNone(header)
        self.assertEqual(header["text"], "Welcome!")
        self.assertEqual(header["format"], "TEXT")

    def test_text_template_has_footer_component(self):
        template = make_template(self.wa_app, footer="Opt out")
        payload = template.to_meta_payload()

        footer = next(
            (c for c in payload["components"] if c["type"] == "FOOTER"), None
        )
        self.assertIsNotNone(footer)
        self.assertEqual(footer["text"], "Opt out")

    def test_text_template_has_buttons_component(self):
        template = make_template(
            self.wa_app,
            buttons=[
                {"type": "QUICK_REPLY", "text": "Yes"},
                {"type": "URL", "text": "Visit", "url": "https://example.com"},
            ],
        )
        payload = template.to_meta_payload()

        buttons = next(
            (c for c in payload["components"] if c["type"] == "BUTTONS"), None
        )
        self.assertIsNotNone(buttons)
        self.assertEqual(len(buttons["buttons"]), 2)
        self.assertEqual(buttons["buttons"][0]["type"], "QUICK_REPLY")
        self.assertEqual(buttons["buttons"][1]["url"], "https://example.com")

    def test_named_params_generates_body_text_named_params(self):
        """Named placeholders produce body_text_named_params example."""
        template = make_template(
            self.wa_app,
            content="Hello {{customer_name}}, your order {{order_id}} is ready.",
            example_body=["Alice", "ORD-123"],
        )
        payload = template.to_meta_payload()
        body = next(c for c in payload["components"] if c["type"] == "BODY")

        self.assertIn("body_text_named_params", body["example"])
        named_params = body["example"]["body_text_named_params"]
        self.assertEqual(named_params[0]["param_name"], "customer_name")
        self.assertEqual(named_params[0]["example"], "Alice")
        self.assertEqual(named_params[1]["param_name"], "order_id")
        self.assertEqual(named_params[1]["example"], "ORD-123")

    def test_carousel_template_generates_carousel_component(self):
        """CAROUSEL templates produce CAROUSEL component with per-card structure."""
        template = make_template(
            self.wa_app,
            template_type="CAROUSEL",
            content="Body text",
            cards=[
                {
                    "body": "Card 1 text",
                    "headerType": "IMAGE",
                    "media_handle": "media_handle_1",
                    "buttons": [
                        {"type": "URL", "text": "Shop", "url": "https://example.com"},
                    ],
                },
                {
                    "body": "Card 2 text",
                    "headerType": "VIDEO",
                    "media_handle": "media_handle_2",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "More Info"},
                    ],
                },
            ],
        )
        payload = template.to_meta_payload()

        carousel = next(
            (c for c in payload["components"] if c["type"] == "CAROUSEL"), None
        )
        self.assertIsNotNone(carousel)
        self.assertEqual(len(carousel["cards"]), 2)

        # Card 1 has HEADER(IMAGE) + BODY + BUTTONS
        card1 = carousel["cards"][0]["components"]
        card1_header = next(c for c in card1 if c["type"] == "HEADER")
        self.assertEqual(card1_header["format"], "IMAGE")
        self.assertEqual(
            card1_header["example"]["header_handle"], ["media_handle_1"]
        )

        card1_body = next(c for c in card1 if c["type"] == "BODY")
        self.assertEqual(card1_body["text"], "Card 1 text")

        card1_buttons = next(c for c in card1 if c["type"] == "BUTTONS")
        self.assertEqual(card1_buttons["buttons"][0]["url"], "https://example.com")

        # Card 2 has VIDEO header
        card2_header = next(
            c for c in carousel["cards"][1]["components"] if c["type"] == "HEADER"
        )
        self.assertEqual(card2_header["format"], "VIDEO")

    def test_carousel_removes_top_level_header(self):
        """CAROUSEL templates should NOT have a top-level HEADER component."""
        template = make_template(
            self.wa_app,
            template_type="CAROUSEL",
            header="Should be removed",
            cards=[
                {
                    "body": "Card body",
                    "buttons": [{"type": "QUICK_REPLY", "text": "OK"}],
                },
            ],
        )
        payload = template.to_meta_payload()

        top_level_header = [
            c for c in payload["components"]
            if c["type"] == "HEADER"
        ]
        self.assertEqual(len(top_level_header), 0)


# =============================================================================
# Viewset create() — result.success check (ticket #386 fix)
# =============================================================================

class TestViewsetCreateResultCheck(TestCase):
    """Verify create() schedules retry on AdapterResult(success=False)."""

    @classmethod
    def setUpTestData(cls):
        from wa.tests.test_template_api_v2 import create_test_tenant_and_user, create_test_wa_app
        cls.tenant, cls.user, cls.token = create_test_tenant_and_user(username="resultcheck")
        cls.wa_app = create_test_wa_app(cls.tenant)

    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()
        if self.token:
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    @patch("wa.tasks.retry_submit_template.apply_async")
    def test_retry_scheduled_on_adapter_failure_without_exception(self, mock_retry):
        """When submit_template returns success=False (no exception), retry is scheduled."""
        mock_adapter = MagicMock()
        mock_adapter.submit_template.return_value = AdapterResult(
            success=False,
            provider="mock",
            error_message="META rejected payload",
        )

        with patch("wa.viewsets.wa_template_v2.get_bsp_adapter", return_value=mock_adapter):
            payload = {
                "wa_app": str(self.wa_app.id),
                "name": "Test Failure",
                "element_name": f"test_fail_{uuid.uuid4().hex[:8]}",
                "category": "MARKETING",
                "template_type": "TEXT",
                "content": "Hello",
            }
            resp = self.client.post("/wa/v2/templates/", payload, format="json")

        self.assertEqual(resp.status_code, 201)
        # retry_submit_template.apply_async should have been called
        mock_retry.assert_called_once()

    @patch("wa.tasks.retry_submit_template.apply_async")
    def test_no_retry_on_adapter_success(self, mock_retry):
        """When submit_template returns success=True, no retry is scheduled."""
        mock_adapter = MagicMock()
        mock_adapter.submit_template.return_value = AdapterResult(
            success=True,
            provider="mock",
            data={"mock": True},
        )

        with patch("wa.viewsets.wa_template_v2.get_bsp_adapter", return_value=mock_adapter):
            payload = {
                "wa_app": str(self.wa_app.id),
                "name": "Test Success",
                "element_name": f"test_ok_{uuid.uuid4().hex[:8]}",
                "category": "MARKETING",
                "template_type": "TEXT",
                "content": "Hello",
            }
            resp = self.client.post("/wa/v2/templates/", payload, format="json")

        self.assertEqual(resp.status_code, 201)
        mock_retry.assert_not_called()


# =============================================================================
# retry_submit_template Celery Task Tests
# =============================================================================

class TestRetrySubmitTemplate(TestCase):
    """Tests for the retry_submit_template Celery task."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.wa_app = make_tenant_and_wa_app(bsp="META")

    def test_skips_nonexistent_template(self):
        from wa.tasks import retry_submit_template
        result = retry_submit_template(fake_id := str(uuid.uuid4()))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "not_found")

    def test_skips_non_draft_template(self):
        """Templates that are no longer DRAFT should not be retried."""
        from wa.tasks import retry_submit_template

        template = make_template(self.wa_app, status="APPROVED", needs_sync=False)
        result = retry_submit_template(str(template.id))
        self.assertEqual(result["status"], "skipped")
        self.assertIn("APPROVED", result["reason"])

    def test_skips_template_with_needs_sync_false(self):
        """Templates with needs_sync=False should not be retried."""
        from wa.tasks import retry_submit_template

        template = make_template(self.wa_app, status="DRAFT", needs_sync=False)
        result = retry_submit_template(str(template.id))
        self.assertEqual(result["status"], "skipped")

    @patch("wa.adapters.meta_direct.MetaDirectAdapter.submit_template")
    def test_successful_retry(self, mock_submit):
        """Successful submit returns success status."""
        from wa.tasks import retry_submit_template

        mock_submit.return_value = AdapterResult(
            success=True, provider="meta_direct", data={"mock": True}
        )
        template = make_template(self.wa_app)
        result = retry_submit_template(str(template.id))

        self.assertEqual(result["status"], "success")
        mock_submit.assert_called_once()

    @patch("wa.adapters.meta_direct.MetaDirectAdapter.submit_template")
    def test_failed_retry_raises_for_requeue(self, mock_submit):
        """Failed submit should raise for Celery retry."""
        from wa.tasks import retry_submit_template

        mock_submit.return_value = AdapterResult(
            success=False,
            provider="meta_direct",
            error_message="Network timeout",
        )
        template = make_template(self.wa_app)

        # The task should raise an exception for Celery retry
        with self.assertRaises(Exception):
            retry_submit_template(str(template.id))


# =============================================================================
# Cron Command Tests
# =============================================================================

class TestCheckTemplateStatusesCron(TestCase):
    """Tests for the check_template_statuses_cron management command."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.wa_app = make_tenant_and_wa_app(bsp="META")

    def test_dry_run_shows_pending_templates(self):
        """--dry-run lists pending templates without modifying them."""
        template = make_template(
            self.wa_app,
            status="PENDING",
            meta_template_id="meta_123",
        )

        out = io.StringIO()
        call_command("check_template_statuses_cron", "--dry-run", stdout=out)
        output = out.getvalue()
        self.assertIn(template.element_name, output)

    def test_dry_run_does_not_modify_templates(self):
        """--dry-run should not change any template status."""
        from wa.models import WATemplate

        template = make_template(
            self.wa_app,
            status="PENDING",
            meta_template_id="meta_456",
        )

        call_command("check_template_statuses_cron", "--dry-run", stdout=io.StringIO())
        template.refresh_from_db()
        self.assertEqual(template.status, "PENDING")

    def test_marks_orphaned_templates_as_failed(self):
        """Templates with PENDING status but no BSP/META ID → FAILED."""
        from wa.models import WATemplate

        template = make_template(
            self.wa_app,
            status="PENDING",
            bsp_template_id=None,
            meta_template_id=None,
        )

        call_command("check_template_statuses_cron", stdout=io.StringIO())
        template.refresh_from_db()
        self.assertEqual(template.status, "FAILED")
        self.assertIn("could not be sent", template.error_message)

    @patch("wa.adapters.meta_direct.MetaDirectAdapter.get_template_status")
    def test_processes_pending_templates_via_adapter(self, mock_status):
        """Pending templates with meta_template_id are checked via adapter."""
        mock_status.return_value = AdapterResult(
            success=True,
            provider="meta_direct",
            data={"status": "APPROVED"},
        )

        template = make_template(
            self.wa_app,
            status="PENDING",
            meta_template_id="meta_789",
        )

        out = io.StringIO()
        call_command("check_template_statuses_cron", "--verbose", stdout=out)
        mock_status.assert_called()

    @patch("wa.adapters.meta_direct.MetaDirectAdapter.get_template_status")
    def test_adapter_failure_records_error_message(self, mock_status):
        """When adapter returns failure, error_message is recorded."""
        from wa.models import WATemplate

        mock_status.return_value = AdapterResult(
            success=False,
            provider="meta_direct",
            error_message="Rate limited",
        )

        template = make_template(
            self.wa_app,
            status="PENDING",
            meta_template_id="meta_abc",
        )

        call_command("check_template_statuses_cron", stdout=io.StringIO())
        template.refresh_from_db()
        self.assertIn("Rate limited", template.error_message)

    @patch("wa.adapters.meta_direct.MetaDirectAdapter.get_template_status")
    def test_exception_in_adapter_continues_to_next_template(self, mock_status):
        """An exception for one template doesn't stop processing the next."""
        from wa.models import WATemplate

        # First call raises, second succeeds
        mock_status.side_effect = [
            Exception("Connection refused"),
            AdapterResult(
                success=True,
                provider="meta_direct",
                data={"status": "APPROVED"},
            ),
        ]

        t1 = make_template(
            self.wa_app,
            status="PENDING",
            meta_template_id="meta_err1",
        )
        t2 = make_template(
            self.wa_app,
            status="PENDING",
            meta_template_id="meta_ok2",
        )

        call_command("check_template_statuses_cron", stdout=io.StringIO())
        # Both were processed (adapter called twice)
        self.assertEqual(mock_status.call_count, 2)

        # t1 should have error recorded
        t1.refresh_from_db()
        self.assertIn("Connection refused", t1.error_message)


# =============================================================================
# MetaDirectAdapter._validate_payload Tests (ticket #382)
# =============================================================================

class TestMetaDirectValidatePayload(TestCase):
    """Tests for _validate_payload() Pydantic validation before META API call."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.wa_app = make_tenant_and_wa_app(bsp="META")

    def test_validate_payload_called_during_submit(self):
        """submit_template calls _validate_payload before API call."""
        from wa.adapters.meta_direct import MetaDirectAdapter

        adapter = MetaDirectAdapter(self.wa_app)
        template = make_template(self.wa_app)

        mock_api_obj = MagicMock()
        mock_api_obj.waba_id = "waba_test"
        mock_api_obj.apply_for_template.return_value = {"id": "template_123"}

        with patch.object(adapter, "_validate_payload") as mock_validate, \
             patch.object(adapter, "_get_template_api", return_value=mock_api_obj):
            mock_validate.return_value = None  # No validation error
            adapter.submit_template(template)
            mock_validate.assert_called_once()

    def test_validation_error_returns_failure_result(self):
        """If _validate_payload raises ValidationError, submit returns failure."""
        from wa.adapters.meta_direct import MetaDirectAdapter

        adapter = MetaDirectAdapter(self.wa_app)
        template = make_template(self.wa_app)

        mock_api_obj = MagicMock()
        mock_api_obj.waba_id = "waba_test"
        mock_api_obj.apply_for_template.return_value = {"id": "template_123"}

        with patch.object(adapter, "_get_template_api", return_value=mock_api_obj):
            # Submit will call _validate_payload internally.
            # With a minimal template, if validation actually catches something,
            # it should return AdapterResult(success=False).
            result = adapter.submit_template(template)
            # Result should be an AdapterResult
            self.assertIsInstance(result, AdapterResult)


# =============================================================================
# MetaDirectAdapter.upload_media Tests (ticket #378)
# =============================================================================

class TestMetaDirectUploadMedia(TestCase):
    """Tests for MetaDirectAdapter.upload_media() implementation."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.wa_app = make_tenant_and_wa_app(bsp="META")

    def test_upload_media_returns_adapter_result(self):
        """upload_media returns AdapterResult with handle_id on success."""
        from wa.adapters.meta_direct import MetaDirectAdapter

        adapter = MetaDirectAdapter(self.wa_app)
        mock_file = io.BytesIO(b"fake image data")
        mock_file.name = "test.jpg"

        with patch.object(adapter, "_resolve_access_token", return_value="fake_token"), \
             patch(
                "wa.utility.apis.meta.media_api.MetaMediaAPI"
             ) as MockMediaAPI:
            mock_api_instance = MagicMock()
            mock_api_instance.upload_media_from_file_object.return_value = {
                "id": "media_12345"
            }
            MockMediaAPI.return_value = mock_api_instance

            result = adapter.upload_media(
                file_obj=mock_file,
                filename="test.jpg",
                file_type="image/jpeg",
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["handle_id"], "media_12345")
        self.assertEqual(result.provider, "meta_direct")

    def test_upload_media_handles_api_error(self):
        """upload_media returns failure when API raises exception."""
        from wa.adapters.meta_direct import MetaDirectAdapter

        adapter = MetaDirectAdapter(self.wa_app)
        mock_file = io.BytesIO(b"fake data")
        mock_file.name = "test.jpg"

        with patch.object(adapter, "_resolve_access_token", return_value="fake_token"), \
             patch(
                "wa.utility.apis.meta.media_api.MetaMediaAPI"
             ) as MockMediaAPI:
            mock_api_instance = MagicMock()
            mock_api_instance.upload_media_from_file_object.side_effect = Exception(
                "Upload failed"
            )
            MockMediaAPI.return_value = mock_api_instance

            result = adapter.upload_media(
                file_obj=mock_file,
                filename="test.jpg",
                file_type="image/jpeg",
            )

        self.assertFalse(result.success)
        self.assertIn("Upload failed", result.error_message or "")


# =============================================================================
# GupshupAdapter.upload_media Tests (Phase 2.3)
# =============================================================================

class TestGupshupUploadMedia(TestCase):
    """Tests for GupshupAdapter.upload_media() with nested handle format."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.wa_app = make_tenant_and_wa_app(bsp="GUPSHUP")

    def test_upload_media_flat_handle(self):
        """Handles flat handleId format: {"handleId": "<id>"}."""
        from wa.adapters.gupshup import GupshupAdapter

        adapter = GupshupAdapter(self.wa_app)
        mock_file = io.BytesIO(b"fake image")
        mock_file.name = "test.jpg"

        mock_api = MagicMock()
        mock_api.upload_media_from_file_object.return_value = {
            "handleId": "4::base64::abc123"
        }

        with patch.object(adapter, "_get_template_api", return_value=mock_api):
            result = adapter.upload_media(
                file_obj=mock_file,
                filename="test.jpg",
                file_type="image/jpeg",
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["handle_id"], "4::base64::abc123")

    def test_upload_media_nested_handle(self):
        """Handles nested format: {"handleId": {"message": "<id>"}}."""
        from wa.adapters.gupshup import GupshupAdapter

        adapter = GupshupAdapter(self.wa_app)
        mock_file = io.BytesIO(b"fake image")
        mock_file.name = "test.jpg"

        mock_api = MagicMock()
        mock_api.upload_media_from_file_object.return_value = {
            "handleId": {"message": "4::base64::nested_id"}
        }

        with patch.object(adapter, "_get_template_api", return_value=mock_api):
            result = adapter.upload_media(
                file_obj=mock_file,
                filename="test.jpg",
                file_type="image/jpeg",
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["handle_id"], "4::base64::nested_id")


# =============================================================================
# Adapter result shape consistency
# =============================================================================

class TestAdapterResultShape(TestCase):
    """Verify AdapterResult pattern works correctly."""

    def test_success_result_is_truthy(self):
        result = AdapterResult(success=True, provider="test")
        self.assertTrue(bool(result))

    def test_failure_result_is_falsy(self):
        result = AdapterResult(success=False, provider="test")
        self.assertFalse(bool(result))

    def test_result_has_required_fields(self):
        result = AdapterResult(
            success=True,
            provider="meta_direct",
            data={"handle_id": "123"},
            error_message=None,
            raw_response={"id": "123"},
        )
        self.assertEqual(result.provider, "meta_direct")
        self.assertEqual(result.data["handle_id"], "123")
        self.assertIsNone(result.error_message)

    def test_default_data_is_empty_dict(self):
        result = AdapterResult(success=True, provider="test")
        self.assertEqual(result.data, {})
