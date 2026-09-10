"""Archive a customer account.

Usage:
    python manage.py archive_tenant 42
    python manage.py archive_tenant 42 --when 2026-09-10
    python manage.py archive_tenant 42 --dry-run

Archiving stamps ``Tenant.archived_at`` and keeps the row. That row is the
billing record the Active Customer Account report (partner agreement Cl. 4.2)
pro-rates against, so it must survive even when the account's customer data is
purged. Purging that data is a separate operation.

Deliberately an ops command rather than a UI action for now: archiving is
irreversible in billing terms and rare enough that a deliberate, logged
invocation is preferable to a button.
"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tenants.models import Tenant


class Command(BaseCommand):
    help = "Archive a customer account, stamping archived_at while keeping the billing record."

    def add_arguments(self, parser):
        parser.add_argument("tenant_id", type=int, help="ID of the tenant to archive")
        parser.add_argument(
            "--when",
            help="Archival date as YYYY-MM-DD (defaults to now). Interpreted in the deployment timezone.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args, **options):
        tenant_id = options["tenant_id"]

        try:
            tenant = Tenant.objects.get(pk=tenant_id)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"No tenant with id {tenant_id}.") from exc

        if tenant.is_archived:
            archived_on = timezone.localtime(tenant.archived_at).strftime("%d %b %Y %H:%M %Z")
            self.stdout.write(
                self.style.WARNING(f"Tenant {tenant_id} ({tenant.name}) is already archived: {archived_on}.")
            )
            return

        when = None
        if options["when"]:
            try:
                parsed = datetime.strptime(options["when"], "%Y-%m-%d")
            except ValueError as exc:
                raise CommandError("--when must be YYYY-MM-DD.") from exc
            when = timezone.make_aware(parsed)

        if options["dry_run"]:
            effective = when or timezone.now()
            self.stdout.write(
                f"Would archive tenant {tenant_id} ({tenant.name}) at "
                f"{timezone.localtime(effective).strftime('%d %b %Y %H:%M %Z')}."
            )
            return

        archived_at = tenant.archive(when=when)
        stamped = timezone.localtime(archived_at).strftime("%d %b %Y %H:%M %Z")

        self.stdout.write(self.style.SUCCESS(f"Archived tenant {tenant_id} ({tenant.name}) at {stamped}."))
        self.stdout.write(
            "The tenant row is retained as the billing record. Purge its customer data separately if required."
        )
