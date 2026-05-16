"""``manage.py seed_voice_rate_cards`` — bootstrap a provider config (#170)."""

from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Seed default voice rate cards onto a VoiceProviderConfig."

    def add_arguments(self, parser):
        parser.add_argument(
            "--config-id",
            required=True,
            help="VoiceProviderConfig UUID to seed.",
        )
        parser.add_argument(
            "--currency",
            default="USD",
            help="Currency to record on the new rate cards (default USD).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-seed even if the config already has rate cards (creates duplicates).",
        )

    def handle(self, *args, **options):
        from voice.billing.seed_data import DEFAULT_RATE_CARDS
        from voice.models import VoiceProviderConfig, VoiceRateCard

        try:
            config_id = UUID(options["config_id"])
        except ValueError as exc:
            raise CommandError(f"--config-id must be a UUID: {exc}") from exc

        try:
            cfg = VoiceProviderConfig.objects.get(pk=config_id)
        except VoiceProviderConfig.DoesNotExist as exc:
            raise CommandError(f"VoiceProviderConfig {config_id} not found") from exc

        existing = VoiceRateCard.objects.filter(provider_config=cfg).count()
        if existing and not options["force"]:
            self.stdout.write(
                self.style.WARNING(f"Config {cfg.id} already has {existing} rate card(s); pass --force to add more.")
            )
            return

        currency = options["currency"]
        now = timezone.now()
        created = 0
        for prefix, rate, increment in DEFAULT_RATE_CARDS:
            VoiceRateCard.objects.create(
                name=f"Default {prefix}",
                provider_config=cfg,
                destination_prefix=prefix,
                rate_per_minute=rate,
                currency=currency,
                billing_increment_seconds=increment,
                valid_from=now,
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {created} rate card(s) for config {cfg.id}."))
