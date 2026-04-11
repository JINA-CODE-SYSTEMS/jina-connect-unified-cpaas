"""
Management command: patch_template_media

For every IMAGE / VIDEO / DOCUMENT WATemplate that has an example_media_url
but NO tenant_media, this command will:

  1. Download the file from example_media_url
  2. Create a TenantMedia record and save the file locally
  3. Upload the file to Gupshup to obtain a wa_handle_id
  4. Link the TenantMedia back to the WATemplate.tenant_media

Usage:
    # Dry-run (default) — show what would be patched, no changes
    python manage.py patch_template_media

    # Actually patch
    python manage.py patch_template_media --apply

    # Patch a single template by UUID
    python manage.py patch_template_media --apply --template-id aa1af47d-...

    # Patch templates for a specific WA app
    python manage.py patch_template_media --apply --wa-app-id 2
"""

import logging
import mimetypes
import os

import requests
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from tenants.models import TenantMedia
from wa.models import TemplateType, WATemplate

logger = logging.getLogger(__name__)

MEDIA_TEMPLATE_TYPES = [
    TemplateType.IMAGE,
    TemplateType.VIDEO,
    TemplateType.DOCUMENT,
]


class Command(BaseCommand):
    help = (
        "Patch WATemplates that have example_media_url but no tenant_media. "
        "Downloads the media, creates TenantMedia, uploads to Gupshup for "
        "wa_handle_id, and links back to the template."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually apply changes. Without this flag, runs in dry-run mode.",
        )
        parser.add_argument(
            "--template-id",
            type=str,
            default=None,
            help="Patch a single template by its UUID.",
        )
        parser.add_argument(
            "--wa-app-id",
            type=int,
            default=None,
            help="Only patch templates belonging to this WA App ID.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        template_id = options["template_id"]
        wa_app_id = options["wa_app_id"]

        # Build queryset
        qs = (
            WATemplate.objects.filter(
                template_type__in=MEDIA_TEMPLATE_TYPES,
                tenant_media__isnull=True,
            )
            .exclude(example_media_url__isnull=True)
            .exclude(example_media_url="")
            .select_related("wa_app", "wa_app__tenant")
        )

        if template_id:
            qs = qs.filter(id=template_id)
        if wa_app_id:
            qs = qs.filter(wa_app_id=wa_app_id)

        templates = list(qs)

        if not templates:
            self.stdout.write(self.style.SUCCESS("No templates need patching."))
            return

        self.stdout.write(f"Found {len(templates)} template(s) to patch{' (DRY RUN)' if not apply else ''}:\n")

        for tpl in templates:
            self.stdout.write(f"  • {tpl.element_name}  type={tpl.template_type}  wa_app={tpl.wa_app_id}  id={tpl.id}")

        if not apply:
            self.stdout.write(self.style.WARNING("\nDry run — no changes made. Re-run with --apply to patch."))
            return

        # ── Apply ────────────────────────────────────────────────────
        success = 0
        failed = 0

        for tpl in templates:
            self.stdout.write(f"\n{'=' * 60}")
            self.stdout.write(f"Patching: {tpl.element_name} ({tpl.id})")

            try:
                self._patch_template(tpl)
                success += 1
                self.stdout.write(self.style.SUCCESS("  ✓ Patched successfully"))
            except Exception as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"  ✗ Failed: {exc}"))
                logger.exception(f"patch_template_media failed for {tpl.element_name}")

        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(self.style.SUCCESS(f"Done. Success: {success}, Failed: {failed}"))

    # ─────────────────────────────────────────────────────────────────
    def _patch_template(self, template: WATemplate):
        """Download → TenantMedia → Gupshup upload → link to template."""

        wa_app = template.wa_app
        if not wa_app:
            raise ValueError("Template has no wa_app")

        tenant = wa_app.tenant
        if not tenant:
            raise ValueError("WA App has no tenant")

        # ── 1. Download the example_media_url ────────────────────────
        self.stdout.write(f"  Downloading: {template.example_media_url[:80]}...")
        resp = requests.get(template.example_media_url, timeout=30)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "image/png")
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".png"
        filename = f"template_media_{template.element_name}{ext}"

        self.stdout.write(f"  Downloaded {len(resp.content)} bytes, type={content_type}")

        # ── 2. Create TenantMedia with local file ───────────────────
        with transaction.atomic():
            tm = TenantMedia.objects.create(
                tenant=tenant,
                platform="whatsapp",
            )
            tm.media.save(filename, ContentFile(resp.content), save=True)

        self.stdout.write(f"  Created TenantMedia id={tm.id}, file={tm.media.name}")

        # ── 3. Upload to Gupshup to get wa_handle_id ────────────────
        handle_id = self._upload_to_gupshup(wa_app, tm, content_type)
        if handle_id:
            tm.wa_handle_id = handle_id
            tm.save(update_fields=["wa_handle_id"])
            self.stdout.write(f"  Gupshup handle: {handle_id}")
        else:
            self.stdout.write(self.style.WARNING("  Gupshup upload skipped or failed (non-fatal)"))

        # ── 4. Link TenantMedia → template ──────────────────────────
        template.tenant_media = tm
        template.save(update_fields=["tenant_media"])
        self.stdout.write("  Linked tenant_media to template")

    # ─────────────────────────────────────────────────────────────────
    def _upload_to_gupshup(self, wa_app, tenant_media, content_type):
        """
        Upload the TenantMedia file to Gupshup and return the handle response.
        Returns the handle dict (e.g. {"handleId": "..."}) or None.
        """
        from tenants.models import BSPChoices

        bsp = getattr(wa_app, "bsp", None)
        if bsp and bsp != BSPChoices.GUPSHUP:
            self.stdout.write(f"  BSP is {bsp}, skipping Gupshup upload")
            return None

        app_id = wa_app.app_id
        app_secret = wa_app.app_secret
        if not app_id or not app_secret:
            self.stdout.write(self.style.WARNING("  Missing Gupshup app_id/app_secret, skipping upload"))
            return None

        from wa.utility.apis.gupshup.template_api import TemplateAPI as GupshupTemplateAPI

        api = GupshupTemplateAPI(appId=app_id, token=app_secret)

        # Re-open the saved file and upload
        tenant_media.media.open("rb")
        try:
            result = api.upload_media_from_file_object(
                file_obj=tenant_media.media,
                filename=os.path.basename(tenant_media.media.name),
                file_type=content_type,
            )
            return result  # typically {"handleId": "..."}
        except Exception as exc:
            self.stderr.write(self.style.WARNING(f"  Gupshup upload error: {exc}"))
            return None
        finally:
            tenant_media.media.close()
