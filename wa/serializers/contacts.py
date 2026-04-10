"""
WAContacts Serializer

Serializer for WhatsApp Contacts with conversation window tracking.
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from abstract.serializers import BaseSerializer
from contacts.models import ContactSource
from wa.models import WAContacts


class WAContactsSerializer(BaseSerializer):
    """
    Serializer for WhatsApp Contacts.
    
    Handles contact data with WhatsApp-specific fields like
    expiration tracking for conversation windows.
    """
    source = serializers.ChoiceField(choices=[
        ContactSource.MANUAL,
        ContactSource.WHATSAPP
    ], default=ContactSource.MANUAL)
    expires_at = serializers.SerializerMethodField()
    last_message_at = serializers.SerializerMethodField()

    def get_expires_at(self, obj):
        """Get CSW expiration as ISO timestamp (24h after last incoming WA message).
        Returns null if the window has already expired."""
        ts = getattr(obj, '_last_incoming_wa_timestamp', None)
        if ts is not None:
            expiry = ts + timedelta(hours=24)
            if expiry > timezone.now():
                return expiry.isoformat()
        return None

    def get_last_message_at(self, obj):
        """Get ISO timestamp of last incoming WA message (for CSW countdown)."""
        ts = getattr(obj, '_last_incoming_wa_timestamp', None)
        if ts is not None:
            return ts.isoformat()
        return None
    
    class Meta:
        model = WAContacts
        fields = '__all__'
        read_only_fields = ['tenant']
