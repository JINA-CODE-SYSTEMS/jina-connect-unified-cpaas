from django.apps import apps
from django.contrib import admin, messages
from tenants.models import RolePermission, TenantRole, TenantWAApp


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0


@admin.register(TenantRole)
class TenantRoleAdmin(admin.ModelAdmin):
    list_display = ["name", "tenant", "slug", "priority", "is_system", "is_editable"]
    list_filter = ["is_system", "is_editable", "tenant"]
    search_fields = ["name", "slug"]
    inlines = [RolePermissionInline]


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ["role", "permission", "allowed"]
    list_filter = ["role__tenant", "role", "allowed"]
    search_fields = ["permission", "role__name"]


class TenantUserAdmin(admin.ModelAdmin):
    list_display = ["user", "tenant", "role", "is_active", "created_at"]
    list_filter = ["tenant", "role", "is_active"]
    search_fields = ["user__username", "user__email", "tenant__name"]
    raw_id_fields = ["user", "tenant"]
    autocomplete_fields = ["role"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Make role required in admin even if model allows null
        if "role" in form.base_fields:
            form.base_fields["role"].required = True
        return form


from tenants.models import TenantUser  # noqa: E402

admin.site.register(TenantUser, TenantUserAdmin)


# ── TenantWAApp Admin with "Reset Webhooks" button ──────────────────────

@admin.register(TenantWAApp)
class TenantWAAppAdmin(admin.ModelAdmin):
    list_display = (
        "id", "tenant", "app_name", "app_id", "wa_number",
        "bsp", "is_active", "subscription_status",
    )
    list_filter = ("bsp", "is_active", "tenant")
    search_fields = ("app_name", "app_id", "wa_number")
    actions = ["reset_and_register_webhooks"]

    @admin.display(description="Webhook Status")
    def subscription_status(self, obj):
        """Show current webhook subscription status inline."""
        from wa.models import WASubscription
        sub = WASubscription.objects.filter(wa_app=obj).order_by("-created_at").first()
        if not sub:
            return "❌ No subscription"
        return f"{sub.status} ({sub.webhook_url})"

    @admin.action(description="🔄 Reset & re-register webhooks with BSP")
    def reset_and_register_webhooks(self, request, queryset):
        """
        For each selected WA App:
        1. Purge all existing subscriptions on the BSP
        2. Delete stale local subscription records
        3. Create a fresh subscription covering all event types
        4. Register it with the BSP
        """
        from django.conf import settings as django_settings
        from wa.adapters import get_bsp_adapter
        from wa.models import (SubscriptionStatus, WASubscription,
                               WebhookEventType)

        base = getattr(django_settings, "DEFAULT_WEBHOOK_BASE_URL", "").rstrip("/")
        all_events = [et.value for et in WebhookEventType]
        success_count = 0
        fail_count = 0

        for wa_app in queryset:
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
                    f"❌ App {wa_app.pk} ({wa_app.app_name}, tenant {wa_app.tenant_id}): "
                    f"purge failed — {purge_result.error_message}",
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
                        f"✅ App {wa_app.pk} ({wa_app.app_name}, tenant {wa_app.tenant_id}): "
                        f"webhooks registered — status={sub.status}, url={webhook_url}",
                        messages.SUCCESS,
                    )
                    success_count += 1
                else:
                    self.message_user(
                        request,
                        f"⚠️ App {wa_app.pk} ({wa_app.app_name}): "
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
                    f"❌ App {wa_app.pk} ({wa_app.app_name}): exception — {exc}",
                    messages.ERROR,
                )
                fail_count += 1

        if success_count or fail_count:
            self.message_user(
                request,
                f"Done: {success_count} app(s) refreshed, {fail_count} failed.",
                messages.SUCCESS if fail_count == 0 else messages.WARNING,
            )


# Auto-register remaining models that aren't already registered
app_models = apps.get_app_config("tenants").get_models()

for model in app_models:

    class GenericAdmin(admin.ModelAdmin):
        list_display = [field.name for field in model._meta.fields]

    try:
        admin.site.register(model, GenericAdmin)
    except admin.sites.AlreadyRegistered:
        pass
