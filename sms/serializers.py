import json

from rest_framework import serializers

from sms.models import SMSApp, SMSOutboundMessage, SMSWebhookEvent


class SMSAppSerializer(serializers.ModelSerializer):
    # Accept dict from API, store as encrypted JSON string in model
    provider_credentials = serializers.JSONField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = SMSApp
        fields = "__all__"
        read_only_fields = ["tenant", "webhook_secret", "webhook_url", "dlr_webhook_url", "created_at", "updated_at"]

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)
        creds = ret.get("provider_credentials")
        if isinstance(creds, dict):
            ret["provider_credentials"] = json.dumps(creds)
        elif creds is None:
            ret["provider_credentials"] = None
        return ret


class SMSOutboundMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSOutboundMessage
        fields = "__all__"


class SMSWebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSWebhookEvent
        fields = "__all__"
