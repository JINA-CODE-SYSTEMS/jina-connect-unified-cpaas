"""
Management command to generate / refresh tenant rate cards.

Usage:
    python manage.py generate_rate_cards                     # all tenants, current month
    python manage.py generate_rate_cards --tenant-id 3       # specific tenant
    python manage.py generate_rate_cards --effective-from 2026-03-01
    python manage.py generate_rate_cards --dry-run
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tenants.models import Tenant
from wa.services.rate_card_service import RateCardService


class Command(BaseCommand):
    help = "Generate or refresh tenant rate cards from Meta base rates + FX + margins."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id",
            type=int,
            default=None,
            help="Generate for a specific tenant only. Omit for all tenants with WA apps.",
        )
        parser.add_argument(
            "--effective-from",
            type=str,
            default=None,
            help="Rate period start (YYYY-MM-DD). Defaults to 1st of current month.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be generated without writing.",
        )

    def handle(self, *args, **options):
        # Determine effective_from
        if options["effective_from"]:
            try:
                effective_from = date.fromisoformat(options["effective_from"])
            except ValueError:
                raise CommandError("Invalid date format. Use YYYY-MM-DD.")
        else:
            today = timezone.now().date()
            effective_from = today.replace(day=1)

        dry_run = options["dry_run"]
        tenant_id = options["tenant_id"]

        self.stdout.write(f"Effective from: {effective_from}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database changes"))

        if tenant_id:
            # Single tenant
            try:
                tenant = Tenant.objects.get(pk=tenant_id)
            except Tenant.DoesNotExist:
                raise CommandError(f"Tenant {tenant_id} not found.")

            svc = RateCardService(tenant)
            if dry_run:
                self.stdout.write(
                    f"Would generate rate cards for tenant '{tenant.name}' "
                    f"(id={tenant.id}, currency={svc.wallet_currency})"
                )
                return

            count = svc.generate_rate_cards(effective_from=effective_from)
            self.stdout.write(self.style.SUCCESS(f"Generated {count} rate card entries for '{tenant.name}'"))
        else:
            # All tenants
            if dry_run:
                from tenants.models import TenantWAApp

                tenant_ids = list(TenantWAApp.objects.values_list("tenant_id", flat=True).distinct())
                self.stdout.write(f"Would generate rate cards for {len(tenant_ids)} tenants")
                return

            results = RateCardService.generate_all_tenant_rate_cards(effective_from=effective_from)
            total = sum(results.values())
            self.stdout.write(
                self.style.SUCCESS(f"Generated {total} total rate card entries across {len(results)} tenants")
            )
            for tid, count in results.items():
                self.stdout.write(f"  Tenant {tid}: {count} entries")
