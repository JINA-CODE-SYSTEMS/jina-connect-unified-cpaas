"""RCS serializers."""

import json

from rest_framework import serializers

from rcs.models import RCSApp, RCSOutboundMessage, RCSWebhookEvent


class RCSAppSerializer(serializers.ModelSerializer):
    provider_credentials = serializers.JSONField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = RCSApp
        fields = "__all__"
        read_only_fields = ["tenant", "webhook_client_token", "webhook_url", "created_at", "updated_at"]

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)
        creds = ret.get("provider_credentials")
        if isinstance(creds, dict):
            ret["provider_credentials"] = json.dumps(creds)
        elif creds is None:
            ret["provider_credentials"] = None
        return ret


class RCSOutboundMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = RCSOutboundMessage
        fields = "__all__"


class RCSWebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RCSWebhookEvent
        fields = "__all__"
