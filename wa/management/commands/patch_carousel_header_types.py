"""
Management command: patch_carousel_header_types

For every CAROUSEL WATemplate whose cards JSON contains entries without a
``headerType`` field, detect the correct type (IMAGE or VIDEO) and update
the stored JSON.

Detection priority per card:
  1. card_media M2M → TenantMedia file extension (.mp4/.3gp/.webm/.mov = VIDEO)
  2. media_handle / exampleMedia base64 content ('dmlkZW8v' = video/ MIME prefix)
  3. Default to IMAGE

Usage:
    # Dry-run (default) — show what would be patched, no changes
    python manage.py patch_carousel_header_types

    # Actually patch
    python manage.py patch_carousel_header_types --apply

    # Patch templates for a specific WA app
    python manage.py patch_carousel_header_types --apply --wa-app-id 2

Related:
    - FE fix: jina-connect-web#409 (headerType now sent in create payload)
    - BE issue: jina-connect#439
"""

import logging

from django.core.management.base import BaseCommand

from wa.models import TemplateType, WATemplate

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = ('.mp4', '.3gp', '.webm', '.mov')
VIDEO_BASE64_MARKER = 'dmlkZW8v'  # base64 for 'video/'


def _detect_card_media_type(card: dict, card_media_map: dict, card_index: int) -> str:
    """
    Detect whether a carousel card is IMAGE or VIDEO.

    Priority:
      1. card_media file extension
      2. media_handle / exampleMedia base64 marker
      3. Default IMAGE
    """
    # 1. Check card_media file extension
    tenant_media = card_media_map.get(card_index)
    if tenant_media and tenant_media.media:
        fname = (tenant_media.media.name or '').lower()
        if fname.endswith(VIDEO_EXTENSIONS):
            return 'VIDEO'
        return 'IMAGE'

    # 2. Check media_handle / exampleMedia for base64 video marker
    handle = card.get('media_handle') or card.get('exampleMedia') or ''
    if VIDEO_BASE64_MARKER in handle:
        return 'VIDEO'

    # 3. Default
    return 'IMAGE'


class Command(BaseCommand):
    help = (
        "Patch CAROUSEL WATemplates whose cards are missing the headerType field. "
        "Detects IMAGE vs VIDEO from card_media file extensions or media_handle content."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Actually write changes. Without this flag, runs in dry-run mode.',
        )
        parser.add_argument(
            '--wa-app-id',
            type=int,
            default=None,
            help='Only process templates belonging to this WA App ID.',
        )
        parser.add_argument(
            '--template-id',
            type=str,
            default=None,
            help='Only process a single template by UUID.',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        wa_app_id = options['wa_app_id']
        template_id = options['template_id']

        mode = 'APPLY' if apply else 'DRY-RUN'
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  patch_carousel_header_types  [{mode}]")
        self.stdout.write(f"{'='*60}\n")

        # Build queryset
        qs = WATemplate.objects.filter(
            template_type=TemplateType.CAROUSEL,
            cards__isnull=False,
        ).prefetch_related('card_media')

        if wa_app_id:
            qs = qs.filter(wa_app_id=wa_app_id)
            self.stdout.write(f"  Filtering by wa_app_id={wa_app_id}")

        if template_id:
            qs = qs.filter(pk=template_id)
            self.stdout.write(f"  Filtering by template_id={template_id}")

        templates = list(qs)
        self.stdout.write(f"  Found {len(templates)} CAROUSEL template(s)\n")

        patched = 0
        skipped = 0
        cards_patched = 0

        for template in templates:
            cards = template.cards
            if not cards or not isinstance(cards, list):
                skipped += 1
                continue

            # Build card_media map for this template
            card_media_map = template.get_card_media_by_index()

            changed = False
            for i, card in enumerate(cards):
                if card.get('headerType'):
                    # Already has headerType — skip
                    continue

                detected = _detect_card_media_type(card, card_media_map, i)
                card['headerType'] = detected
                changed = True
                cards_patched += 1

                self.stdout.write(
                    f"  [{template.element_name}] Card {i}: "
                    f"headerType missing → set to {detected}"
                )

            if not changed:
                skipped += 1
                continue

            if apply:
                template.cards = cards
                template.save(update_fields=['cards'])
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Saved {template.element_name} ({template.pk})"
                    )
                )
            else:
                self.stdout.write(
                    f"  (dry-run) Would save {template.element_name} ({template.pk})"
                )

            patched += 1

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Summary:")
        self.stdout.write(f"    Templates patched: {patched}")
        self.stdout.write(f"    Templates skipped: {skipped} (already had headerType)")
        self.stdout.write(f"    Cards patched:     {cards_patched}")
        if not apply and patched > 0:
            self.stdout.write(
                self.style.WARNING(
                    "\n  ⚠ This was a DRY RUN. Re-run with --apply to write changes."
                )
            )
        self.stdout.write(f"{'='*60}\n")
