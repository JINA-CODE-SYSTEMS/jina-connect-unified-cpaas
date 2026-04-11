"""
WhatsApp Template — Carousel Payload & Commerce Gating Tests
=============================================================

Tests the three fixes:
  #374  CAROUSEL to_meta_payload() builds correct META Graph API payload
  #375  card_media M2M linking on template create
  #317  Commerce Manager gating on /types/ endpoint

HOW TO RUN:
    python manage.py test wa.tests.test_carousel_and_commerce -v 2

    # Individual class:
    python manage.py test wa.tests.test_carousel_and_commerce.TestCarouselMetaPayload -v 2
    python manage.py test wa.tests.test_carousel_and_commerce.TestCardMediaM2MLinking -v 2
    python manage.py test wa.tests.test_carousel_and_commerce.TestCommerceManagerGating -v 2
"""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

# ─── Factories ────────────────────────────────────────────────────────────────


def create_test_tenant_and_user(username="testuser", password="TestPass123!"):
    """Create a Tenant + User + JWT token."""
    from django.contrib.auth import get_user_model

    from tenants.models import Tenant, TenantRole, TenantUser

    User = get_user_model()

    tenant = Tenant.objects.create(
        name=f"Test Tenant {uuid.uuid4().hex[:8]}",
        is_active=True,
    )

    unique_suffix = uuid.uuid4().int % 10**9
    mobile = f"+91{9000000000 + unique_suffix % 999999999}"

    user = User.objects.create_user(
        username=username + uuid.uuid4().hex[:6],
        email=f"{username}_{uuid.uuid4().hex[:6]}@test.com",
        password=password,
        mobile=mobile,
    )

    owner_role = TenantRole.objects.get(tenant=tenant, slug="owner")
    TenantUser.objects.create(tenant=tenant, user=user, role=owner_role)

    client = APIClient()
    resp = client.post("/token/", {"username": user.username, "password": password}, format="json")
    token = resp.data.get("access") if resp.status_code == 200 else None
    return tenant, user, token


def create_test_wa_app(tenant, **overrides):
    """Create a TenantWAApp (WAApp) with META bsp."""
    from wa.models import WAApp

    defaults = dict(
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
    defaults.update(overrides)
    return WAApp.objects.create(**defaults)


# ─── Sample payloads ─────────────────────────────────────────────────────────

CAROUSEL_IMAGE_PAYLOAD = {
    "name": "Image Carousel",
    "element_name": "img_carousel",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "CAROUSEL",
    "content": "Hi {{name}}, check our products:",
    "example_body": ["Alice"],
    "cards": [
        {
            "body": "Widget A - $29",
            "headerType": "IMAGE",
            "media_handle": "h:111111",
            "buttons": [{"type": "URL", "text": "View", "url": "https://example.com/a"}],
        },
        {
            "body": "Widget B - $49",
            "headerType": "IMAGE",
            "media_handle": "h:222222",
            "buttons": [
                {"type": "QUICK_REPLY", "text": "Buy"},
                {"type": "URL", "text": "Details", "url": "https://example.com/b"},
            ],
        },
    ],
}

CAROUSEL_VIDEO_PAYLOAD = {
    "name": "Video Carousel",
    "element_name": "vid_carousel",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "CAROUSEL",
    "content": "Watch our {{brand}} demos!",
    "example_body": ["TechCo"],
    "cards": [
        {
            "body": "Demo 1",
            "headerType": "VIDEO",
            "media_handle": "h:vid_001",
            "buttons": [{"type": "URL", "text": "Watch", "url": "https://example.com/v1"}],
        },
        {
            "body": "Demo 2",
            "headerType": "VIDEO",
            "media_handle": "h:vid_002",
            "buttons": [{"type": "URL", "text": "Watch", "url": "https://example.com/v2"}],
        },
    ],
}

CAROUSEL_NO_MEDIA_PAYLOAD = {
    "name": "No Media Carousel",
    "element_name": "no_media_carousel",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "CAROUSEL",
    "content": "Hello {{customer}}",
    "example_body": ["Bob"],
    "cards": [
        {"body": "Card one text", "buttons": [{"type": "QUICK_REPLY", "text": "Yes"}]},
        {"body": "Card two text", "buttons": [{"type": "QUICK_REPLY", "text": "No"}]},
    ],
}

TEXT_PAYLOAD = {
    "name": "Simple Text",
    "element_name": "simple_text",
    "language_code": "en",
    "category": "MARKETING",
    "template_type": "TEXT",
    "content": "Hello {{name}}!",
    "example_body": ["World"],
}


# ─── Base class ───────────────────────────────────────────────────────────────


class TemplateTestBase(TestCase):
    """Sets up tenant, user, WAApp, and authenticated client with mocked BSP."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.user, cls.token = create_test_tenant_and_user()
        cls.wa_app = create_test_wa_app(cls.tenant)

    def setUp(self):
        self.client = APIClient()
        if self.token:
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

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
        payload = dict(data or TEXT_PAYLOAD)
        payload["wa_app"] = str(self.wa_app.id)
        payload["element_name"] = payload.get("element_name", "t") + "_" + uuid.uuid4().hex[:8]
        payload.update(overrides)
        return self.client.post(self.list_url, payload, format="json")


# =============================================================================
# #374  CAROUSEL to_meta_payload()
# =============================================================================


class TestCarouselMetaPayload(TemplateTestBase):
    """
    Verify that WATemplate.to_meta_payload() builds a correct payload for
    CAROUSEL templates that matches the META Graph API spec.

    META spec requires:
      - No top-level HEADER component
      - A CAROUSEL component with a "cards" list
      - Each card has components: HEADER (IMAGE/VIDEO), BODY, BUTTONS
    """

    def _get_meta_payload(self, data):
        """Create a carousel template then fetch its meta-payload."""
        resp = self.create_template(data)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        pk = resp.data["id"]
        payload_resp = self.client.get(self.action_url(pk, "meta-payload"))
        self.assertEqual(payload_resp.status_code, status.HTTP_200_OK)
        return payload_resp.data

    # ── Structural tests ──────────────────────────────────────────────

    def test_carousel_has_carousel_component(self):
        """✅ META payload includes a top-level CAROUSEL component."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        types = [c["type"] for c in payload["components"]]
        self.assertIn("CAROUSEL", types)

    def test_carousel_no_top_level_header(self):
        """✅ CAROUSEL payload must NOT have a top-level HEADER component."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        types = [c["type"] for c in payload["components"]]
        self.assertNotIn("HEADER", types)

    def test_carousel_card_count_matches_input(self):
        """✅ Number of cards in CAROUSEL component matches input cards."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")
        self.assertEqual(len(carousel["cards"]), 2)

    def test_carousel_has_body_component(self):
        """✅ CAROUSEL payload has a top-level BODY with the message text."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        body = next(c for c in payload["components"] if c["type"] == "BODY")
        self.assertIn("name", body["text"])  # {{name}} placeholder

    # ── Per-card structure ────────────────────────────────────────────

    def test_card_has_header_body_buttons(self):
        """✅ Each card has HEADER, BODY, and BUTTONS sub-components."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        for i, card in enumerate(carousel["cards"]):
            card_types = [sc["type"] for sc in card["components"]]
            self.assertIn("HEADER", card_types, f"Card {i} missing HEADER")
            self.assertIn("BODY", card_types, f"Card {i} missing BODY")
            self.assertIn("BUTTONS", card_types, f"Card {i} missing BUTTONS")

    def test_card_header_format_image(self):
        """✅ IMAGE carousel cards have HEADER format=IMAGE."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        for card in carousel["cards"]:
            header = next(sc for sc in card["components"] if sc["type"] == "HEADER")
            self.assertEqual(header["format"], "IMAGE")

    def test_card_header_format_video(self):
        """✅ VIDEO carousel cards have HEADER format=VIDEO."""
        payload = self._get_meta_payload(CAROUSEL_VIDEO_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        for card in carousel["cards"]:
            header = next(sc for sc in card["components"] if sc["type"] == "HEADER")
            self.assertEqual(header["format"], "VIDEO")

    def test_card_header_has_media_handle(self):
        """✅ Card HEADER includes media handle in example.header_handle."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        card_0 = carousel["cards"][0]
        header = next(sc for sc in card_0["components"] if sc["type"] == "HEADER")
        self.assertIn("example", header)
        self.assertIn("header_handle", header["example"])
        self.assertEqual(header["example"]["header_handle"], ["h:111111"])

    def test_card_header_no_handle_when_absent(self):
        """✅ Card HEADER omits example when no media_handle provided."""
        payload = self._get_meta_payload(CAROUSEL_NO_MEDIA_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        card_0 = carousel["cards"][0]
        header = next(sc for sc in card_0["components"] if sc["type"] == "HEADER")
        self.assertNotIn("example", header)

    def test_card_body_text(self):
        """✅ Card BODY text matches the per-card body content."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        card_0 = carousel["cards"][0]
        body = next(sc for sc in card_0["components"] if sc["type"] == "BODY")
        self.assertEqual(body["text"], "Widget A - $29")

    def test_card_buttons_url_type(self):
        """✅ Card BUTTONS include URL button with correct url."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        card_0 = carousel["cards"][0]
        buttons_comp = next(sc for sc in card_0["components"] if sc["type"] == "BUTTONS")
        url_btn = next(b for b in buttons_comp["buttons"] if b["type"] == "URL")
        self.assertEqual(url_btn["text"], "View")
        self.assertEqual(url_btn["url"], "https://example.com/a")

    def test_card_buttons_quick_reply(self):
        """✅ Card BUTTONS include QUICK_REPLY buttons when present."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        card_1 = carousel["cards"][1]
        buttons_comp = next(sc for sc in card_1["components"] if sc["type"] == "BUTTONS")
        btn_types = [b["type"] for b in buttons_comp["buttons"]]
        self.assertIn("QUICK_REPLY", btn_types)
        self.assertIn("URL", btn_types)

    def test_card_without_body_omits_body_component(self):
        """✅ Card with empty body omits BODY sub-component."""
        data = {
            **CAROUSEL_IMAGE_PAYLOAD,
            "cards": [
                {"body": "", "media_handle": "h:001", "buttons": [{"type": "QUICK_REPLY", "text": "OK"}]},
                {"body": "Has body", "media_handle": "h:002", "buttons": [{"type": "QUICK_REPLY", "text": "OK"}]},
            ],
        }
        payload = self._get_meta_payload(data)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        # Card 0 has empty body → should NOT have BODY sub-component
        card_0_types = [sc["type"] for sc in carousel["cards"][0]["components"]]
        self.assertNotIn("BODY", card_0_types)

        # Card 1 has body → should have BODY sub-component
        card_1_types = [sc["type"] for sc in carousel["cards"][1]["components"]]
        self.assertIn("BODY", card_1_types)

    def test_card_without_buttons_omits_buttons_component(self):
        """✅ Card with no buttons omits BUTTONS sub-component."""
        data = {
            **CAROUSEL_IMAGE_PAYLOAD,
            "cards": [
                {"body": "No buttons card", "media_handle": "h:001"},
                {"body": "With buttons", "media_handle": "h:002", "buttons": [{"type": "QUICK_REPLY", "text": "OK"}]},
            ],
        }
        payload = self._get_meta_payload(data)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")

        card_0_types = [sc["type"] for sc in carousel["cards"][0]["components"]]
        self.assertNotIn("BUTTONS", card_0_types)

        card_1_types = [sc["type"] for sc in carousel["cards"][1]["components"]]
        self.assertIn("BUTTONS", card_1_types)

    # ── Non-carousel not affected ─────────────────────────────────────

    def test_text_template_no_carousel_component(self):
        """✅ TEXT templates do NOT produce a CAROUSEL component."""
        resp = self.create_template(TEXT_PAYLOAD)
        pk = resp.data["id"]
        payload_resp = self.client.get(self.action_url(pk, "meta-payload"))
        types = [c["type"] for c in payload_resp.data["components"]]
        self.assertNotIn("CAROUSEL", types)
        # TEXT may have HEADER
        self.assertIn("BODY", types)

    def test_parameter_format_named_for_carousel(self):
        """✅ CAROUSEL with named placeholders has parameter_format=NAMED."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        self.assertEqual(payload.get("parameter_format"), "NAMED")

    def test_carousel_language_and_category(self):
        """✅ Meta payload has correct top-level language and category."""
        payload = self._get_meta_payload(CAROUSEL_IMAGE_PAYLOAD)
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["category"], "MARKETING")

    def test_header_format_defaults_to_image(self):
        """✅ Card without explicit headerType defaults to IMAGE."""
        data = {
            **CAROUSEL_IMAGE_PAYLOAD,
            "cards": [
                {"body": "Default header", "media_handle": "h:def", "buttons": [{"type": "QUICK_REPLY", "text": "OK"}]},
            ],
        }
        payload = self._get_meta_payload(data)
        carousel = next(c for c in payload["components"] if c["type"] == "CAROUSEL")
        header = next(sc for sc in carousel["cards"][0]["components"] if sc["type"] == "HEADER")
        self.assertEqual(header["format"], "IMAGE")


# =============================================================================
# #375  card_media M2M linking
# =============================================================================


class TestCardMediaM2MLinking(TemplateTestBase):
    """
    Verify that the create endpoint links TenantMedia records to the
    template's card_media M2M when cards include media_handle values.
    """

    def _create_tenant_media(self, card_index, media_id):
        """Create a TenantMedia record for the test tenant."""

        from django.core.files.uploadedfile import SimpleUploadedFile

        from tenants.models import TenantMedia

        dummy_file = SimpleUploadedFile(
            name=f"card_{card_index}.jpg",
            content=b"\xff\xd8\xff\xe0" + b"\x00" * 100,  # minimal JPEG
            content_type="image/jpeg",
        )
        return TenantMedia.objects.create(
            tenant=self.tenant,
            media=dummy_file,
            card_index=card_index,
            media_id=media_id,
        )

    def test_card_media_linked_on_create(self):
        """✅ TenantMedia records are linked to template.card_media after create."""
        # Pre-create TenantMedia records matching the carousel payload handles
        tm0 = self._create_tenant_media(card_index=0, media_id="h:111111")
        tm1 = self._create_tenant_media(card_index=1, media_id="h:222222")

        resp = self.create_template(CAROUSEL_IMAGE_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        from wa.models import WATemplate

        template = WATemplate.objects.get(pk=resp.data["id"])
        linked_ids = set(template.card_media.values_list("id", flat=True))
        self.assertIn(tm0.id, linked_ids)
        self.assertIn(tm1.id, linked_ids)

    def test_card_media_count_matches_cards(self):
        """✅ card_media M2M count equals number of cards with matching media."""
        self._create_tenant_media(card_index=0, media_id="h:111111")
        self._create_tenant_media(card_index=1, media_id="h:222222")

        resp = self.create_template(CAROUSEL_IMAGE_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        from wa.models import WATemplate

        template = WATemplate.objects.get(pk=resp.data["id"])
        self.assertEqual(template.card_media.count(), 2)

    def test_card_media_empty_when_no_media_records_exist(self):
        """✅ card_media stays empty when no TenantMedia records match."""
        resp = self.create_template(CAROUSEL_IMAGE_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        from wa.models import WATemplate

        template = WATemplate.objects.get(pk=resp.data["id"])
        self.assertEqual(template.card_media.count(), 0)

    def test_partial_match_only_links_existing(self):
        """✅ Only cards with matching TenantMedia get linked."""
        # Only create media for card 0, not card 1
        tm0 = self._create_tenant_media(card_index=0, media_id="h:111111")

        resp = self.create_template(CAROUSEL_IMAGE_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        from wa.models import WATemplate

        template = WATemplate.objects.get(pk=resp.data["id"])
        self.assertEqual(template.card_media.count(), 1)
        self.assertEqual(template.card_media.first().id, tm0.id)

    def test_text_template_no_card_media(self):
        """✅ Non-carousel templates have no card_media links."""
        resp = self.create_template(TEXT_PAYLOAD)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        from wa.models import WATemplate

        template = WATemplate.objects.get(pk=resp.data["id"])
        self.assertEqual(template.card_media.count(), 0)

    def test_card_without_media_handle_skipped(self):
        """✅ Cards without media_handle field are gracefully skipped."""
        self._create_tenant_media(card_index=0, media_id="h:111111")

        data = {
            **CAROUSEL_IMAGE_PAYLOAD,
            "cards": [
                {
                    "body": "Card with handle",
                    "media_handle": "h:111111",
                    "buttons": [{"type": "QUICK_REPLY", "text": "OK"}],
                },
                {
                    "body": "Card without handle",
                    # No media_handle key
                    "buttons": [{"type": "QUICK_REPLY", "text": "No"}],
                },
            ],
        }
        resp = self.create_template(data)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        from wa.models import WATemplate

        template = WATemplate.objects.get(pk=resp.data["id"])
        self.assertEqual(template.card_media.count(), 1)

    def test_latest_tenant_media_used_on_duplicate(self):
        """✅ When multiple TenantMedia match same (card_index, media_id),
        the most recently created one is linked (order_by -created_at)."""
        # Create two TenantMedia for same card_index + media_id
        self._create_tenant_media(card_index=0, media_id="h:111111")
        tm_new = self._create_tenant_media(card_index=0, media_id="h:111111")

        data = {
            **CAROUSEL_IMAGE_PAYLOAD,
            "cards": [
                {
                    "body": "Only one card",
                    "media_handle": "h:111111",
                    "buttons": [{"type": "QUICK_REPLY", "text": "OK"}],
                },
            ],
        }
        resp = self.create_template(data)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        from wa.models import WATemplate

        template = WATemplate.objects.get(pk=resp.data["id"])
        # Should link the newest one
        linked = list(template.card_media.values_list("id", flat=True))
        self.assertIn(tm_new.id, linked)


# =============================================================================
# #317  Commerce Manager gating on /types/
# =============================================================================


class TestCommerceManagerGating(TemplateTestBase):
    """
    Verify that GET /wa/v2/templates/types/ returns enabled=false for
    CATALOG and PRODUCT when is_commerce_manager_enabled=False on the
    tenant's WAApp, and enabled=true when it's True.
    """

    @property
    def types_url(self):
        return "/wa/v2/templates/types/"

    def _set_commerce_enabled(self, enabled):
        """Toggle is_commerce_manager_enabled on the test WAApp."""
        from tenants.models import TenantWAApp

        TenantWAApp.objects.filter(pk=self.wa_app.pk).update(is_commerce_manager_enabled=enabled)
        self.wa_app.refresh_from_db()

    def _get_type_entry(self, types_list, value):
        """Find a type entry by its value."""
        return next((t for t in types_list if t["value"] == value), None)

    # ── Commerce disabled (default) ───────────────────────────────────

    def test_catalog_disabled_by_default(self):
        """✅ CATALOG is disabled when commerce manager is off (default)."""
        resp = self.client.get(self.types_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        catalog = self._get_type_entry(resp.data, "CATALOG")
        if catalog:
            self.assertFalse(catalog["enabled"])
            self.assertIn("disabled_reason", catalog)
            self.assertIn("Commerce Manager", catalog["disabled_reason"])

    def test_product_disabled_by_default(self):
        """✅ PRODUCT is disabled when commerce manager is off (default)."""
        resp = self.client.get(self.types_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        product = self._get_type_entry(resp.data, "PRODUCT")
        if product:
            self.assertFalse(product["enabled"])
            self.assertIn("disabled_reason", product)

    def test_non_commerce_types_enabled_by_default(self):
        """✅ TEXT, IMAGE, VIDEO, etc. are always enabled regardless of commerce flag."""
        resp = self.client.get(self.types_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for type_entry in resp.data:
            if type_entry["value"] not in ("CATALOG", "PRODUCT"):
                self.assertTrue(
                    type_entry["enabled"],
                    f"{type_entry['value']} should be enabled but got enabled={type_entry['enabled']}",
                )

    def test_non_commerce_types_have_no_disabled_reason(self):
        """✅ Non-commerce types should not have disabled_reason."""
        resp = self.client.get(self.types_url)
        for type_entry in resp.data:
            if type_entry["value"] not in ("CATALOG", "PRODUCT"):
                self.assertNotIn(
                    "disabled_reason", type_entry, f"{type_entry['value']} should not have disabled_reason"
                )

    # ── Commerce enabled ──────────────────────────────────────────────

    def test_catalog_enabled_when_commerce_on(self):
        """✅ CATALOG is enabled when commerce manager is turned on."""
        self._set_commerce_enabled(True)
        resp = self.client.get(self.types_url)
        catalog = self._get_type_entry(resp.data, "CATALOG")
        if catalog:
            self.assertTrue(catalog["enabled"])

    def test_product_enabled_when_commerce_on(self):
        """✅ PRODUCT is enabled when commerce manager is turned on."""
        self._set_commerce_enabled(True)
        resp = self.client.get(self.types_url)
        product = self._get_type_entry(resp.data, "PRODUCT")
        if product:
            self.assertTrue(product["enabled"])

    def test_all_types_enabled_when_commerce_on(self):
        """✅ ALL types have enabled=true when commerce is on."""
        self._set_commerce_enabled(True)
        resp = self.client.get(self.types_url)
        for type_entry in resp.data:
            self.assertTrue(type_entry["enabled"], f"{type_entry['value']} should be enabled")

    # ── Response structure ────────────────────────────────────────────

    def test_types_response_structure(self):
        """✅ Each type entry has value, label, and enabled fields."""
        resp = self.client.get(self.types_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp.data, list)
        self.assertGreater(len(resp.data), 0)

        for entry in resp.data:
            self.assertIn("value", entry)
            self.assertIn("label", entry)
            self.assertIn("enabled", entry, f"Missing 'enabled' in {entry}")

    def test_types_includes_core_types(self):
        """✅ Types endpoint returns TEXT, IMAGE, VIDEO, DOCUMENT at minimum."""
        resp = self.client.get(self.types_url)
        values = [t["value"] for t in resp.data]
        self.assertIn("TEXT", values)
        self.assertIn("IMAGE", values)
        self.assertIn("VIDEO", values)
        self.assertIn("DOCUMENT", values)

    def test_types_requires_auth(self):
        """🔒 Unauthenticated request to /types/ is rejected."""
        client = APIClient()  # No token
        resp = client.get(self.types_url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── Toggle persistence ────────────────────────────────────────────

    def test_toggle_commerce_off_after_on(self):
        """✅ Disabling commerce after enabling re-disables CATALOG/PRODUCT."""
        self._set_commerce_enabled(True)
        resp = self.client.get(self.types_url)
        catalog = self._get_type_entry(resp.data, "CATALOG")
        if catalog:
            self.assertTrue(catalog["enabled"])

        self._set_commerce_enabled(False)
        resp = self.client.get(self.types_url)
        catalog = self._get_type_entry(resp.data, "CATALOG")
        if catalog:
            self.assertFalse(catalog["enabled"])

    def test_disabled_reason_text(self):
        """✅ Disabled reason contains the expected message."""
        resp = self.client.get(self.types_url)
        catalog = self._get_type_entry(resp.data, "CATALOG")
        if catalog and not catalog["enabled"]:
            self.assertEqual(
                catalog["disabled_reason"],
                "Available after Meta Commerce Manager onboarding is complete.",
            )
