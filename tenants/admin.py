from django import forms
from django.apps import apps
from django.contrib import admin, messages

from tenants.models import BrandingSettings, RolePermission, Tenant, TenantRole, TenantWAApp
from tenants.services.onboarding import create_tenant_with_owner, validate_new_tenant


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
        "id",
        "tenant",
        "app_name",
        "app_id",
        "wa_number",
        "bsp",
        "is_active",
        "subscription_status",
    )
    list_filter = ("bsp", "is_active", "tenant")
    search_fields = ("app_name", "app_id", "wa_number")
    actions = ["reset_and_register_webhooks"]
    readonly_fields = ("webhook_identifier_hint", "per_app_callback_url")

    @admin.display(description="Webhook identifier")
    def webhook_identifier_hint(self, obj):
        """The truncated identifier — enough to match a row to a log line.

        The full value lives in ``per_app_callback_url`` below, where it is
        there to be copied. This column exists so the list and the change form
        can *refer* to an identifier without reproducing it (#310).
        """
        return obj.webhook_identifier_hint or "—"

    @admin.display(description="Per-app callback URL")
    def per_app_callback_url(self, obj):
        """The URL an operator pastes into a client's BSP dashboard (#305 D-2).

        Operator-assisted setup is a supported path, so the URL has to be
        readable somewhere an operator works. The client-facing equivalent is
        ``GET /wa/v2/apps/<id>/webhook-setup/``.
        """
        from wa.services import webhook_identity

        if not obj.pk or not obj.webhook_identifier:
            return "—"
        return webhook_identity.callback_url(obj)

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
        from wa.adapters import get_bsp_adapter
        from wa.models import SubscriptionStatus, WASubscription, WebhookEventType
        from wa.services import webhook_identity

        all_events = [et.value for et in WebhookEventType]
        success_count = 0
        fail_count = 0

        for wa_app in queryset:
            # The per-app URL shown in ``per_app_callback_url`` above and handed
            # to the client by the webhook-setup endpoint — one answer to "which
            # URL is registered", where there used to be two (#334).
            webhook_url = webhook_identity.registration_callback_url(wa_app)

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
                        f"⚠️ App {wa_app.pk} ({wa_app.app_name}): BSP registration failed — {result.error_message}",
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


# ── Tenant ───────────────────────────────────────────────────────────────────
# Tenant used to fall through the generic auto-registration loop at the bottom
# of this file: every field in list_display, no fieldsets, no validation — and
# creating one there produced a tenant nobody could log into, because nothing
# made the owner user or the OWNER TenantUser row (#221).


class TenantCreationForm(forms.ModelForm):
    """Collects the owner alongside the tenant, because one is useless without the other."""

    owner_email = forms.EmailField(
        label="Owner email",
        help_text="If this address already has an account it is linked as owner and keeps its own password.",
    )
    owner_mobile = forms.CharField(
        label="Owner mobile",
        required=False,
        help_text="International format, e.g. +14155552671. Required for a new account; unique across all users.",
    )
    temporary_password = forms.CharField(
        label="Temporary password",
        required=False,
        widget=forms.PasswordInput(render_value=True),
        help_text="Only for a new account. The owner must replace it before they can sign in.",
    )
    owner_first_name = forms.CharField(label="Owner first name", required=False)
    owner_last_name = forms.CharField(label="Owner last name", required=False)

    class Meta:
        model = Tenant
        fields = ("name", "description")

    def clean(self):
        cleaned = super().clean()
        # Shared with the service, so the operator sees field errors here
        # rather than a 500 — and so the two cannot disagree about the rules.
        validate_new_tenant(
            name=cleaned.get("name", ""),
            owner_email=cleaned.get("owner_email", ""),
            owner_mobile=cleaned.get("owner_mobile", ""),
            temporary_password=cleaned.get("temporary_password", ""),
        )
        return cleaned


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "balance", "is_archived", "created_at")
    # is_archived is a property, not a field, so it can be displayed but not
    # filtered on. Filter by whether archived_at is set, which is the same
    # question asked of the column that actually exists.
    list_filter = (("archived_at", admin.EmptyFieldListFilter), "created_at")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at", "archived_at")
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("name", "description")}),
        ("Wallet", {"fields": ("balance", "credit_line", "threshold_alert")}),
        ("Lifecycle", {"fields": ("archived_at", "created_at", "updated_at")}),
    )

    add_fieldsets = (
        (None, {"fields": ("name", "description")}),
        (
            "Owner",
            {
                "description": (
                    "A tenant without an owner cannot be logged into. These create or link one "
                    "in the same transaction as the tenant."
                ),
                "fields": (
                    "owner_email",
                    "owner_mobile",
                    "temporary_password",
                    "owner_first_name",
                    "owner_last_name",
                ),
            },
        ),
    )

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = TenantCreationForm
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        return self.add_fieldsets if obj is None else self.fieldsets

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        # Creation goes through the service so the admin and the API cannot
        # drift. The form's clean() has already run the same validation, so
        # reaching here with bad input means a genuine race, not operator
        # error.
        result = create_tenant_with_owner(
            name=form.cleaned_data["name"],
            description=form.cleaned_data.get("description", "") or "",
            owner_email=form.cleaned_data["owner_email"],
            owner_mobile=form.cleaned_data.get("owner_mobile", "") or "",
            temporary_password=form.cleaned_data.get("temporary_password", "") or "",
            first_name=form.cleaned_data.get("owner_first_name", "") or "",
            last_name=form.cleaned_data.get("owner_last_name", "") or "",
        )
        # Hand the saved row back to the admin so its log entry and redirect
        # point at a real object.
        obj.pk = result.tenant.pk
        obj.refresh_from_db()
        self.message_user(request, result.summary, messages.SUCCESS)


@admin.register(BrandingSettings)
class BrandingSettingsAdmin(admin.ModelAdmin):
    """The single row that white-labels this deployment.

    Registered explicitly rather than through the generic loop below, which
    put every field in ``list_display``, offered no grouping, and — worse —
    showed an "Add" button for a model whose ``save()`` quietly folds a second
    row into the first. An operator who used it believed they had created a
    separate configuration and were editing that, while they were in fact
    overwriting the only one there is.
    """

    list_display = ["__str__", "effective_product_name", "effective_primary_color", "updated_at"]
    readonly_fields = ["effective_product_name", "effective_primary_color", "created_at", "updated_at"]

    fieldsets = (
        (
            "Text",
            {
                "fields": ("product_name", "effective_product_name"),
                "description": (
                    "Shown in page titles and transactional copy. Leave the product name blank to use "
                    "this deployment's default; the resolved value is shown beneath it."
                ),
            },
        ),
        (
            "Colour",
            {
                "fields": ("primary_color", "effective_primary_color"),
                "description": "The web app derives its whole brand ramp from this one colour.",
            },
        ),
        (
            "Assets",
            {
                "fields": (
                    "favicon",
                    "favicon_url",
                    "primary_logo",
                    "primary_logo_url",
                    "secondary_logo",
                    "secondary_logo_url",
                ),
                "description": "An uploaded file takes precedence over the matching external URL.",
            },
        ),
        ("Metadata", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def has_add_permission(self, request):
        """Enforce the singleton in the UI instead of papering over it in save()."""
        return not BrandingSettings.objects.exists()


# Auto-register remaining models that aren't already registered
app_models = apps.get_app_config("tenants").get_models()

for model in app_models:

    class GenericAdmin(admin.ModelAdmin):
        list_display = [field.name for field in model._meta.fields]

    try:
        admin.site.register(model, GenericAdmin)
    except admin.sites.AlreadyRegistered:
        pass
