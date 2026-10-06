"""A media template keeps hold of the header file it was created with.

The web app uploads the header file, then creates the template with only the
file's Meta handle and its seven-day signed URL. Nothing linked the
``TenantMedia``, so after that week every broadcast of the template sent Meta
a dead URL and failed with 131053.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_template_header_media_link.py -v
"""

import importlib

from django.apps import apps
from django.core.files.base import ContentFile
from rest_framework import status

from tenants.models import TenantMedia
from wa.models import WATemplate
from wa.tests.test_template_api_v2 import SAMPLE_IMAGE_TEMPLATE, TemplateTestBase, create_test_tenant_and_user

HANDLE = "4::aW1hZ2UvcG5n:ARY1ecCghq6qxqO4rt8k"
SIGNED_URL = "https://storage.googleapis.com/bucket/tenant_media/intro.png?X-Goog-Expires=604800&X-Goog-Signature=abc"


def _upload(tenant, handle, wa_handle_id=None, **extra):
    tm = TenantMedia.objects.create(tenant=tenant, wa_handle_id=wa_handle_id or {"handleId": handle}, **extra)
    tm.media.save("intro.png", ContentFile(b"\x89PNG"), save=True)
    return tm


class TestHeaderMediaLinkedOnCreate(TemplateTestBase):
    def _create(self, **overrides):
        resp = self.create_template(
            SAMPLE_IMAGE_TEMPLATE, media_handle=HANDLE, example_media_url=SIGNED_URL, **overrides
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return WATemplate.objects.get(pk=resp.data["id"])

    def test_upload_is_linked_by_handle(self):
        tm = _upload(self.tenant, HANDLE)
        self.assertEqual(self._create().tenant_media, tm)

    def test_bare_string_handle_from_sync_also_matches(self):
        tm = _upload(self.tenant, HANDLE, wa_handle_id=HANDLE)
        self.assertEqual(self._create().tenant_media, tm)

    def test_carousel_card_upload_is_not_the_header(self):
        _upload(self.tenant, HANDLE, card_index=0)
        self.assertIsNone(self._create().tenant_media)

    def test_another_tenants_upload_is_never_linked(self):
        other_tenant, _, _ = create_test_tenant_and_user(username="other")
        _upload(other_tenant, HANDLE)
        self.assertIsNone(self._create().tenant_media)

    def test_new_handle_on_edit_moves_the_link(self):
        _upload(self.tenant, HANDLE)
        template = self._create()
        replacement = _upload(self.tenant, "4::new-handle")

        resp = self.client.patch(self.detail_url(template.pk), {"media_handle": "4::new-handle"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        template.refresh_from_db()
        self.assertEqual(template.tenant_media, replacement)


class TestBackfillMigration(TemplateTestBase):
    def test_existing_template_is_linked(self):
        tm = _upload(self.tenant, HANDLE)
        template = WATemplate.objects.create(
            wa_app=self.wa_app,
            element_name="stuck_intro_image",
            language_code="en",
            category="MARKETING",
            template_type="IMAGE",
            content="Hi",
            media_handle=HANDLE,
            example_media_url=SIGNED_URL,
        )
        self.assertIsNone(template.tenant_media)

        migration = importlib.import_module("wa.migrations.0021_link_template_header_media")
        migration.link_header_media(apps, None)

        template.refresh_from_db()
        self.assertEqual(template.tenant_media, tm)
