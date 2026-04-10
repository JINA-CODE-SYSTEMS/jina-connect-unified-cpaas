"""
Django management command for checking WhatsApp template statuses.
This command can be called by django-crontab or executed manually.
"""
import json
from datetime import datetime

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Check WhatsApp template statuses via BSP adapters and update local database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without making any changes',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose logging output',
        )

    def handle(self, *args, **options):
        """Main command handler"""
        dry_run = options.get('dry_run', False)
        verbose = options.get('verbose', False)

        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No changes will be made')
            )
            return self._dry_run_check()
        else:
            return self._run_template_status_check(verbose)

    def _dry_run_check(self):
        """Show what templates would be processed without making changes"""
        from wa.models import WATemplate, StatusChoices

        pending_templates = WATemplate.objects.filter(
            status=StatusChoices.PENDING,
            is_active=True
        ).exclude(
            bsp_template_id__isnull=True, meta_template_id__isnull=True
        ).select_related('wa_app')

        self.stdout.write(f"Found {pending_templates.count()} pending templates that would be checked:")

        for template in pending_templates:
            self.stdout.write(f"  - {template.element_name} (ID: {template.bsp_template_id or template.meta_template_id}) - App: {template.wa_app.app_id}")

        return json.dumps({
            "processed": 0,
            "updated": 0,
            "total_pending": pending_templates.count()
        })

    def _run_template_status_check(self, verbose=False):
        """
        Check template statuses using the BSP adapter pattern.
        Works for all providers (Gupshup, META Direct, etc.).
        The adapter handles API calls, status mapping, and DB saves.
        """
        from wa.adapters import get_bsp_adapter
        from wa.models import WATemplate, StatusChoices

        start_time = datetime.now()
        if verbose:
            self.stdout.write(f"[{start_time}] Starting template status check...")

        # Mark templates that were never submitted (no BSP/META ID) as FAILED
        orphaned = WATemplate.objects.filter(
            bsp_template_id__isnull=True,
            meta_template_id__isnull=True,
            status=StatusChoices.PENDING
        ).update(status=StatusChoices.FAILED, error_message="Template could not be sent to BSP")

        if verbose and orphaned:
            self.stdout.write(f"Marked {orphaned} orphaned templates as FAILED")

        # Get all pending templates that have a BSP or META template ID
        pending_templates = WATemplate.objects.filter(
            status=StatusChoices.PENDING,
            is_active=True
        ).exclude(
            bsp_template_id__isnull=True, meta_template_id__isnull=True
        ).select_related('wa_app')

        if verbose:
            self.stdout.write(f"Found {pending_templates.count()} pending templates to check")

        templates_processed = 0
        templates_updated = 0

        for template in pending_templates:
            try:
                adapter = get_bsp_adapter(template.wa_app)

                if verbose:
                    self.stdout.write(f"Checking {template.element_name} via {adapter.PROVIDER_NAME}...")

                # Adapter handles: API call → status mapping → template.save()
                result = adapter.get_template_status(template)
                templates_processed += 1

                if result.success:
                    new_status = result.data.get("status")
                    if verbose:
                        self.stdout.write(f"  → {template.element_name}: {new_status}")
                    if new_status and new_status != StatusChoices.PENDING:
                        templates_updated += 1
                else:
                    if verbose:
                        self.stdout.write(f"  ❌ {template.element_name}: {result.error_message}")
                    # Keep status as PENDING for retry, but record the error
                    WATemplate.objects.filter(id=template.id).update(
                        error_message=f"Status check failed: {result.error_message}"
                    )

            except Exception as e:
                if verbose:
                    self.stdout.write(f"  ❌ Error checking {template.element_name}: {str(e)}")
                try:
                    WATemplate.objects.filter(id=template.id).update(
                        error_message=f"Status check error: {str(e)}"
                    )
                except Exception:
                    pass
                continue

        end_time = datetime.now()
        result = {
            "processed": templates_processed,
            "updated": templates_updated,
            "total_pending": pending_templates.count()
        }

        if verbose:
            self.stdout.write(f"✅ Completed: {templates_processed} processed, {templates_updated} updated")
            self.stdout.write(f"[{end_time}] Command completed successfully")

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Template status check completed!\n'
                f'   Processed: {result["processed"]} templates\n'
                f'   Updated: {result["updated"]} templates\n'
                f'   Total pending: {result["total_pending"]} templates'
            )
        )

        return json.dumps(result)
            
