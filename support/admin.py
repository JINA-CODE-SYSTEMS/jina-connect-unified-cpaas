from django.contrib import admin

from support.models import SupportTicket, SupportTicketEvent


class SupportTicketEventInline(admin.TabularInline):
    model = SupportTicketEvent
    extra = 0
    fields = ["created_at", "kind", "actor", "agent_name", "author", "body"]
    readonly_fields = fields


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ["github_number", "title", "status", "assignee_login", "reporter_name", "reported_at"]
    list_filter = ["status"]
    search_fields = ["title", "reporter_name", "reporter_email", "github_number"]
    readonly_fields = ["github_repo", "github_number", "reported_at", "resolved_at", "closed_at", "updated_at"]
    inlines = [SupportTicketEventInline]
