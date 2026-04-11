"""
Admin registration for URL tracking models.

Note: The broadcast app's admin.py auto-registers all models with a generic
admin class. We override those registrations here with richer admin views.
"""

from django.contrib import admin

from broadcast.url_tracker.models import TrackedURL, TrackedURLClick


class TrackedURLClickInline(admin.TabularInline):
    model = TrackedURLClick
    extra = 0
    readonly_fields = ["clicked_at", "ip_address", "user_agent", "referer"]
    can_delete = False


class TrackedURLAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "short_original_url",
        "button_text",
        "click_count",
        "contact",
        "broadcast",
        "created_at",
    ]
    list_filter = ["tenant", "created_at"]
    search_fields = ["code", "original_url", "button_text", "contact__phone"]
    readonly_fields = [
        "code",
        "tracked_url",
        "click_count",
        "first_clicked_at",
        "last_clicked_at",
        "created_at",
    ]
    inlines = [TrackedURLClickInline]

    def short_original_url(self, obj):
        return obj.original_url[:80] + ("..." if len(obj.original_url) > 80 else "")

    short_original_url.short_description = "Original URL"


class TrackedURLClickAdmin(admin.ModelAdmin):
    list_display = ["tracked_url", "clicked_at", "ip_address"]
    list_filter = ["clicked_at"]
    readonly_fields = ["tracked_url", "clicked_at", "ip_address", "user_agent", "referer"]


# Unregister the auto-registered generic versions, then register ours
try:
    admin.site.unregister(TrackedURL)
except admin.sites.NotRegistered:
    pass
try:
    admin.site.unregister(TrackedURLClick)
except admin.sites.NotRegistered:
    pass

admin.site.register(TrackedURL, TrackedURLAdmin)
admin.site.register(TrackedURLClick, TrackedURLClickAdmin)
