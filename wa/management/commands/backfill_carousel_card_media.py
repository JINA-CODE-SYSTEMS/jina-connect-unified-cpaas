from django.core.management.base import BaseCommand
from django.db import transaction

from wa.models import TemplateTypeChoices, TenantMedia, WATemplate


class Command(BaseCommand):
    help = "Backfill card_media M2M and card_index for CAROUSEL WA templates"

    def handle(self, *args, **options):
        templates = (
            WATemplate.objects.filter(template_type=TemplateTypeChoices.CAROUSEL)
            .exclude(cards__isnull=True)
            .select_related("wa_app__tenant")
            .prefetch_related("card_media")
        )

        processed_cards = 0
        skipped_cards = 0
        skipped_templates = 0
        errors = 0
        processed_templates = 0

        for template in templates:
            if not template.cards:
                skipped_templates += 1
                continue

            tenant = template.wa_app.tenant
            existing_media_ids = {media.id for media in template.card_media.all()}

            template_processed_cards = 0
            template_skipped_cards = 0
            template_errors = 0

            try:
                with transaction.atomic():
                    for idx, card in enumerate(template.cards):
                        media_handle_id = card.get("mediaId")
                        if not media_handle_id:
                            template_skipped_cards += 1
                            continue

                        try:
                            media = TenantMedia.objects.get(
                                media_id=media_handle_id,
                                tenant=tenant,
                            )
                        except TenantMedia.DoesNotExist:
                            template_errors += 1
                            self.stderr.write(
                                self.style.WARNING(
                                    f"[Template {template.id}] "
                                    f"Media '{media_handle_id}' not found for tenant {tenant.id}"
                                )
                            )
                            continue

                        if media.card_index != idx:
                            media.card_index = idx
                            media.save(update_fields=["card_index"])

                        if media.id not in existing_media_ids:
                            template.card_media.add(media)
                            existing_media_ids.add(media.id)

                        template_processed_cards += 1

            except Exception as exc:
                errors += 1
                self.stderr.write(self.style.ERROR(f"[Template {template.id}] Transaction failed: {exc}"))
                continue

            # Counters are updated only after successful transaction
            processed_templates += 1
            processed_cards += template_processed_cards
            skipped_cards += template_skipped_cards
            errors += template_errors

        self.stdout.write(self.style.SUCCESS("Backfill completed"))
        self.stdout.write(f"Templates processed: {processed_templates}")
        self.stdout.write(f"Templates skipped (no cards): {skipped_templates}")
        self.stdout.write(f"Cards processed: {processed_cards}")
        self.stdout.write(f"Cards skipped: {skipped_cards}")
        self.stdout.write(f"Errors: {errors}")
