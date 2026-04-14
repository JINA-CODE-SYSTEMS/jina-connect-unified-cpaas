from rest_framework import serializers

from sms.models import SMSApp, SMSOutboundMessage, SMSWebhookEvent


class SMSAppSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSApp
        fields = "__all__"
        extra_kwargs = {
            "provider_credentials": {"write_only": True},
            "webhook_secret": {"write_only": True},
        }


class SMSOutboundMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSOutboundMessage
        fields = "__all__"


class SMSWebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSWebhookEvent
        fields = "__all__"
