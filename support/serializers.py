from datetime import timedelta

from rest_framework import serializers

from support import conf
from support.models import SupportTicket, SupportTicketEvent


class SupportTicketEventSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicketEvent
        # No GitHub comment id, no login: nothing here identifies a developer.
        fields = ["id", "kind", "actor", "agent_name", "author_name", "body", "created_at"]

    def get_author_name(self, event):
        user = event.author
        if user is None:
            return ""
        return (user.get_full_name() or "").strip() or user.email or ""


class SupportTicketListSerializer(serializers.ModelSerializer):
    number = serializers.IntegerField(source="github_number")
    status_label = serializers.CharField(source="get_status_display")
    assigned_to = serializers.SerializerMethodField()
    auto_close_at = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "number",
            "title",
            "status",
            "status_label",
            "reporter_name",
            "page_url",
            "assigned_to",
            "reported_at",
            "resolved_at",
            "auto_close_at",
            "closed_at",
            "updated_at",
        ]

    def get_assigned_to(self, ticket):
        # The support name, never ``assignee_login``.
        return conf.agent_name(ticket.assignee_login)

    def get_auto_close_at(self, ticket):
        if ticket.status != "resolved" or ticket.resolved_at is None:
            return None
        return ticket.resolved_at + timedelta(hours=conf.auto_close_hours())


class SupportTicketDetailSerializer(SupportTicketListSerializer):
    events = SupportTicketEventSerializer(many=True, read_only=True)

    class Meta(SupportTicketListSerializer.Meta):
        fields = [*SupportTicketListSerializer.Meta.fields, "description", "reporter_email", "close_reason", "events"]


class CustomerTextSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=5000, trim_whitespace=True)
