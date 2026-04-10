"""
Serializers for URL tracking analytics.
"""

from rest_framework import serializers

from broadcast.url_tracker.models import TrackedURL, TrackedURLClick


class TrackedURLClickSerializer(serializers.ModelSerializer):
    """Individual click event."""

    class Meta:
        model = TrackedURLClick
        fields = ['id', 'clicked_at', 'ip_address', 'user_agent', 'referer']


class TrackedURLSerializer(serializers.ModelSerializer):
    """Tracked URL with click summary."""

    tracked_url = serializers.CharField(read_only=True)
    contact_phone = serializers.CharField(source='contact.phone', read_only=True, default=None)
    contact_name = serializers.SerializerMethodField()

    class Meta:
        model = TrackedURL
        fields = [
            'id', 'code', 'original_url', 'tracked_url',
            'button_index', 'button_text',
            'click_count', 'first_clicked_at', 'last_clicked_at',
            'contact', 'contact_phone', 'contact_name',
            'broadcast', 'broadcast_message',
            'created_at',
        ]
        read_only_fields = fields

    def get_contact_name(self, obj):
        if obj.contact:
            return obj.contact.full_name if hasattr(obj.contact, 'full_name') else str(obj.contact)
        return None


class BroadcastClickAnalyticsSerializer(serializers.Serializer):
    """Aggregated click analytics for a broadcast."""

    total_tracked_urls = serializers.IntegerField()
    total_clicks = serializers.IntegerField()
    unique_contacts_clicked = serializers.IntegerField()
    buttons = serializers.ListField(child=serializers.DictField())
