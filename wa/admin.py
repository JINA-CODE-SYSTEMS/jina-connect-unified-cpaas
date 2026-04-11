from django.apps import apps

# Register your models here.
from django.contrib import admin, messages

from wa.models import SubscriptionStatus, WASubscription, WATemplate, WAWebhookEvent, WebhookEventType

# ── WATemplate Admin ─────────────────────────────────────────────────────


@admin.register(WATemplate)
class WATemplateAdmin(admin.ModelAdmin):
    list_display = [f.name for f in WATemplate._meta.fields if f.name not in ("description", "vertical")]
    list_filter = ("status", "category", "template_type", "wa_app")
    search_fields = ("element_name", "meta_template_id", "bsp_template_id")
    readonly_fields = ("meta_template_id", "bsp_template_id")


# ── WASubscription Admin ─────────────────────────────────────────────────


@admin.register(WASubscription)
class WASubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "wa_app",
        "webhook_url",
        "status",
        "bsp_subscription_id",
        "error_message",
        "last_event_at",
        "is_active",
        "created_at",
    )
    list_filter = ("status", "is_active", "wa_app")
    search_fields = ("webhook_url", "bsp_subscription_id")
    readonly_fields = ("bsp_subscription_id", "error_message", "last_event_at")
    actions = ["reset_and_register_webhooks"]

    @admin.action(description="🔄 Reset & re-register webhooks with BSP")
    def reset_and_register_webhooks(self, request, queryset):
        """
        For each selected subscription's wa_app:
        1. Purge all existing subscriptions on the BSP
        2. Delete stale local records for that app
        3. Create a fresh subscription covering all event types
        4. Register it with the BSP

        This is the admin equivalent of the /refresh/ API endpoint.
        """

        # Collect unique wa_apps from selected subscriptions
        wa_app_ids = set(queryset.values_list("wa_app_id", flat=True))
        self._do_refresh_for_apps(request, wa_app_ids)

    def _do_refresh_for_apps(self, request, wa_app_ids):
        """Shared logic: purge + re-create subscriptions for given app IDs."""
        from django.conf import settings as django_settings

        from wa.adapters import get_bsp_adapter
        from wa.models import WAApp

        base = getattr(django_settings, "DEFAULT_WEBHOOK_BASE_URL", "").rstrip("/")
        all_events = [et.value for et in WebhookEventType]
        success_count = 0
        fail_count = 0

        for wa_app in WAApp.objects.filter(pk__in=wa_app_ids):
            bsp_path = {
                "GUPSHUP": "/wa/v2/webhooks/gupshup/",
                "META": "/wa/v2/webhooks/meta/",
            }.get(wa_app.bsp, "/wa/v2/webhooks/gupshup/")
            webhook_url = f"{base}{bsp_path}"

            adapter = get_bsp_adapter(wa_app)

            # Step 1: Purge BSP-side subscriptions
            purge_result = adapter.purge_all_webhooks()
            if not purge_result.success:
                self.message_user(
                    request,
                    f"❌ App {wa_app.pk} (tenant {wa_app.tenant_id}): purge failed — {purge_result.error_message}",
                    messages.ERROR,
                )
                fail_count += 1
                continue

            # Step 2: Delete local stale records
            WASubscription.objects.filter(wa_app=wa_app).delete()

            # Step 3: Create fresh subscription
            sub = WASubscription.objects.create(
                wa_app=wa_app,
                name=f"webhook_{wa_app.bsp.lower()}_{wa_app.pk}",
                webhook_url=webhook_url,
                event_types=all_events,
                status=SubscriptionStatus.PENDING,
            )

            # Step 4: Register with BSP
            try:
                result = adapter.register_webhook(sub)
                sub.refresh_from_db()

                if result.success:
                    self.message_user(
                        request,
                        f"✅ App {wa_app.pk} (tenant {wa_app.tenant_id}): "
                        f"subscription created — status={sub.status}, "
                        f"url={webhook_url}",
                        messages.SUCCESS,
                    )
                    success_count += 1
                else:
                    self.message_user(
                        request,
                        f"⚠️ App {wa_app.pk} (tenant {wa_app.tenant_id}): "
                        f"BSP registration failed — {result.error_message}",
                        messages.WARNING,
                    )
                    fail_count += 1
            except Exception as exc:
                sub.status = SubscriptionStatus.FAILED
                sub.error_message = str(exc)
                sub.save(update_fields=["status", "error_message"])
                self.message_user(
                    request,
                    f"❌ App {wa_app.pk} (tenant {wa_app.tenant_id}): exception — {exc}",
                    messages.ERROR,
                )
                fail_count += 1

        if success_count:
            self.message_user(
                request,
                f"Done: {success_count} app(s) refreshed successfully, {fail_count} failed.",
                messages.SUCCESS if fail_count == 0 else messages.WARNING,
            )


# ── WAWebhookEvent Admin ──────────────────────────────────────────────────


@admin.register(WAWebhookEvent)
class WAWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "wa_app",
        "event_type",
        "bsp",
        "is_processed",
        "retry_count",
        "error_message_short",
        "created_at",
    )
    list_filter = ("event_type", "bsp", "is_processed", "wa_app")
    search_fields = ("id", "error_message")
    readonly_fields = ("id", "payload", "bsp", "message", "created_at")
    ordering = ("-created_at",)
    actions = ["reprocess_webhook_events"]

    @admin.display(description="Error")
    def error_message_short(self, obj):
        if not obj.error_message:
            return "—"
        return obj.error_message[:80] + "…" if len(obj.error_message) > 80 else obj.error_message

    @admin.action(description="🔄 Reprocess selected webhook events")
    def reprocess_webhook_events(self, request, queryset):
        """
        Reset selected webhook events to unprocessed and re-queue them
        through the Celery processing pipeline.
        """
        from wa.tasks import process_webhook_event_task

        # Reset processing state
        queryset.update(
            is_processed=False,
            error_message=None,
        )

        success = 0
        failed = 0
        for event in queryset:
            try:
                process_webhook_event_task.delay(str(event.pk))
                success += 1
            except Exception as exc:
                failed += 1
                self.message_user(
                    request,
                    f"❌ Event {event.pk}: failed to queue — {exc}",
                    messages.ERROR,
                )

        self.message_user(
            request,
            f"🔄 Queued {success} webhook event(s) for reprocessing"
            + (f", {failed} failed to queue" if failed else ""),
            messages.SUCCESS if failed == 0 else messages.WARNING,
        )


# ── Auto-register remaining wa models ────────────────────────────────────

app_models = apps.get_app_config("wa").get_models()

for model in app_models:

    class GenericAdmin(admin.ModelAdmin):
        list_display = [field.name for field in model._meta.fields]

    try:
        admin.site.register(model, GenericAdmin)
    except admin.sites.AlreadyRegistered:
        pass
