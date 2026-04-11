"""
Management command to reprocess a template webhook from IncomingTemplateWebHookDump.

Usage:
    # Process a specific webhook by ID
    python manage.py reprocess_template_webhook --id 123

    # Process all unprocessed webhooks
    python manage.py reprocess_template_webhook --all-unprocessed

    # Process webhook and skip email notification
    python manage.py reprocess_template_webhook --id 123 --skip-email

    # Dry run - show what would happen without making changes
    python manage.py reprocess_template_webhook --id 123 --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from wa.models import IncomingTemplateWebHookDump
from wa.utility.data_model.gupshup.template_input import TemplateInput


class Command(BaseCommand):
    help = "Reprocess template webhook(s) from IncomingTemplateWebHookDump table"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, help="ID of the IncomingTemplateWebHookDump row to process")
        parser.add_argument("--all-unprocessed", action="store_true", help="Process all unprocessed webhooks")
        parser.add_argument("--skip-email", action="store_true", help="Skip sending email notifications")
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")

    def handle(self, *args, **options):
        webhook_id = options.get("id")
        all_unprocessed = options.get("all_unprocessed")
        skip_email = options.get("skip_email")
        dry_run = options.get("dry_run")

        if not webhook_id and not all_unprocessed:
            raise CommandError("Please provide --id or --all-unprocessed")

        if webhook_id and all_unprocessed:
            raise CommandError("Cannot use both --id and --all-unprocessed")

        # Get webhooks to process
        if webhook_id:
            try:
                webhooks = [IncomingTemplateWebHookDump.objects.get(pk=webhook_id)]
            except IncomingTemplateWebHookDump.DoesNotExist:
                raise CommandError(f"IncomingTemplateWebHookDump with id={webhook_id} not found")
        else:
            webhooks = list(IncomingTemplateWebHookDump.objects.filter(is_processed=False))
            if not webhooks:
                self.stdout.write(self.style.WARNING("No unprocessed webhooks found"))
                return

        self.stdout.write(f"Found {len(webhooks)} webhook(s) to process")

        for webhook in webhooks:
            self.process_webhook(webhook, skip_email=skip_email, dry_run=dry_run)

    def process_webhook(self, webhook: IncomingTemplateWebHookDump, skip_email: bool = False, dry_run: bool = False):
        """Process a single webhook."""
        from tenants.models import TenantWAApp
        from wa.models import StatusChoices, WATemplate
        from wa.services.template_notifications import TemplateNotificationService

        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(f"Processing webhook ID: {webhook.pk}")
        self.stdout.write(f"Created at: {webhook.created_at}")
        self.stdout.write(f"Is processed: {webhook.is_processed}")

        payload = webhook.payload
        if not payload:
            self.stdout.write(self.style.ERROR("  No payload found"))
            return

        self.stdout.write(f"Payload: {payload}")

        # Parse the webhook
        try:
            processed_data: TemplateInput = TemplateInput.from_webhook_payload(payload)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Failed to parse payload: {str(e)}"))
            return

        self.stdout.write("\nParsed data:")
        self.stdout.write(f"  gs_app_id: {processed_data.gs_app_id}")
        self.stdout.write(f"  gs_template_id: {processed_data.gs_template_id}")
        self.stdout.write(f"  event: {processed_data.event}")
        self.stdout.write(f"  reason: {processed_data.reason}")
        self.stdout.write(f"  new_category: {processed_data.new_category}")
        self.stdout.write(f"  previous_category: {processed_data.previous_category}")
        self.stdout.write(f"  message_template_name: {processed_data.message_template_name}")

        # Find WA app
        try:
            wa_app = TenantWAApp.objects.get(app_id=processed_data.gs_app_id)
            self.stdout.write(f"\nFound WA app: {wa_app.app_name} (tenant: {wa_app.tenant.name})")
        except TenantWAApp.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"  WA app not found for app_id: {processed_data.gs_app_id}"))
            return

        # Find template
        template = WATemplate.objects.filter(template_id=processed_data.gs_template_id, wa_app=wa_app).first()

        if not template:
            self.stdout.write(
                self.style.WARNING(f"  Template not found for gs_template_id: {processed_data.gs_template_id}")
            )
            # Try to find by element_name if provided
            if processed_data.message_template_name:
                template = WATemplate.objects.filter(
                    element_name=processed_data.message_template_name, wa_app=wa_app
                ).first()
                if template:
                    self.stdout.write(self.style.SUCCESS(f"  Found template by element_name: {template.element_name}"))

        if not template:
            self.stdout.write(self.style.ERROR("  Could not find matching template"))
            return

        self.stdout.write("\nFound template:")
        self.stdout.write(f"  ID: {template.pk}")
        self.stdout.write(f"  Element name: {template.element_name}")
        self.stdout.write(f"  Current category: {template.category}")
        self.stdout.write(f"  Current status: {template.status}")
        self.stdout.write(f"  Template ID (Gupshup): {template.template_id}")

        # Handle category update
        if processed_data.event == "CATEGORY_UPDATE":
            self.stdout.write("\n>>> CATEGORY UPDATE detected")
            self.stdout.write(f"  Old category: {processed_data.previous_category}")
            self.stdout.write(f"  New category: {processed_data.new_category}")

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"  [DRY RUN] Would update category: {template.category} -> {processed_data.new_category}"
                    )
                )
                self.stdout.write(self.style.WARNING(f"  [DRY RUN] Would update status: {template.status} -> PENDING"))
            else:
                old_category = template.category
                old_status = template.status
                template.category = processed_data.new_category
                template.status = StatusChoices.PENDING  # Set status to PENDING on category change
                template.save(update_fields=["category", "status"])
                self.stdout.write(
                    self.style.SUCCESS(f"  Updated category: {old_category} -> {processed_data.new_category}")
                )
                self.stdout.write(self.style.SUCCESS(f"  Updated status: {old_status} -> PENDING"))

                if not skip_email:
                    self.stdout.write("  Sending email notification...")
                    success = TemplateNotificationService.send_category_change_notification(
                        template=template, old_category=old_category, new_category=processed_data.new_category
                    )
                    if success:
                        self.stdout.write(self.style.SUCCESS("  Email sent successfully"))
                    else:
                        self.stdout.write(self.style.WARNING("  Email sending failed"))
                else:
                    self.stdout.write("  Skipping email notification (--skip-email)")

        # Handle status update
        elif processed_data.event:
            self.stdout.write("\n>>> STATUS UPDATE detected")
            self.stdout.write(f"  Event: {processed_data.event}")

            status_map = {
                "APPROVED": StatusChoices.APPROVED,
                "REJECTED": StatusChoices.REJECTED,
                "FAILED": StatusChoices.FAILED,
                "PENDING": StatusChoices.PENDING,
                "DISABLED": StatusChoices.DISABLED,
            }
            new_status = status_map.get(processed_data.event.upper())

            if new_status:
                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(f"  [DRY RUN] Would update status: {template.status} -> {new_status}")
                    )
                else:
                    old_status = template.status
                    template.status = new_status
                    if processed_data.reason:
                        template.error_message = processed_data.reason
                        template.save(update_fields=["status", "error_message"])
                    else:
                        template.save(update_fields=["status"])
                    self.stdout.write(self.style.SUCCESS(f"  Updated status: {old_status} -> {new_status}"))

                    if not skip_email and processed_data.event.upper() in [
                        "APPROVED",
                        "REJECTED",
                        "FAILED",
                        "DISABLED",
                    ]:
                        self.stdout.write("  Sending email notification...")
                        success = TemplateNotificationService.send_status_change_notification(
                            template=template,
                            old_status=old_status,
                            new_status=processed_data.event.upper(),
                            reason=processed_data.reason,
                        )
                        if success:
                            self.stdout.write(self.style.SUCCESS("  Email sent successfully"))
                        else:
                            self.stdout.write(self.style.WARNING("  Email sending failed"))
                    else:
                        self.stdout.write("  Skipping email notification")
            else:
                self.stdout.write(self.style.WARNING(f"  Unknown event type: {processed_data.event}"))

        # Mark webhook as processed
        if not dry_run:
            webhook.wa_app = wa_app
            webhook.status = processed_data.event
            webhook.error_message = processed_data.reason
            webhook.template_id = processed_data.gs_template_id
            webhook.is_processed = True
            webhook.save()
            self.stdout.write(self.style.SUCCESS("\nWebhook marked as processed"))
        else:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] Webhook would be marked as processed"))
