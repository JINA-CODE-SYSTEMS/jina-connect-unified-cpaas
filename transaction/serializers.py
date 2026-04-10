from abstract.serializers import BaseSerializer
from broadcast.models import BroadcastStatusChoices
from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers
from transaction.models import TenantTransaction


class TenantTransactionSerializer(BaseSerializer):
    amount = MoneyField(max_digits=14, decimal_places=2, default_currency='INR')
    tenant_name = serializers.CharField(source='tenant.name', read_only=True)
    broadcast_name = serializers.SerializerMethodField(read_only=True)
    broadcast_status = serializers.SerializerMethodField(read_only=True)
    broadcast_scheduled_time = serializers.SerializerMethodField(read_only=True)
    broadcast_recipient_count = serializers.SerializerMethodField(read_only=True)
    broadcast_platform = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = TenantTransaction
        fields = "__all__"
        read_only_fields = ("system_transaction_id",)
    
    def _get_historical_broadcast(self, obj):
        """Get historical broadcast record if broadcast_history_id exists"""
        if obj.broadcast_history_id and obj.broadcast:
            try:
                return obj.broadcast.history.get(history_id=obj.broadcast_history_id)
            except:
                pass
        return None
    
    def get_broadcast_name(self, obj):
        """Get broadcast name from history or current broadcast"""
        historical = self._get_historical_broadcast(obj)
        if historical:
            return historical.name
        return obj.broadcast.name if obj.broadcast else None
    
    def get_broadcast_status(self, obj):
        """Get broadcast status from history (historical state at transaction time)"""
        historical = self._get_historical_broadcast(obj)
        if historical:
            if historical.status == BroadcastStatusChoices.QUEUED:
                return "QUEUED/SENT"
            return historical.status                    
        return obj.broadcast.status if obj.broadcast else None
    
    def get_broadcast_scheduled_time(self, obj):
        """Get broadcast scheduled_time from history"""
        historical = self._get_historical_broadcast(obj)
        if historical:
            return historical.scheduled_time
        return obj.broadcast.scheduled_time if obj.broadcast else None
    
    def get_broadcast_recipient_count(self, obj):
        """Get recipient count from current broadcast"""
        return obj.broadcast.recipients.count() if obj.broadcast else None
    
    def get_broadcast_platform(self, obj):
        """Get broadcast platform from history"""
        historical = self._get_historical_broadcast(obj)
        if historical:
            return historical.platform
        return obj.broadcast.platform if obj.broadcast else None
