"""
WAApp Serializers (v2)

Serializers for WhatsApp Business App configuration.
"""

from drf_yasg import openapi
from rest_framework import serializers

from abstract.serializers import BaseSerializer
from wa.models import WAApp


class WAAppListSerializer(BaseSerializer):
    """
    Minimal serializer for WA App list views.

    Used for efficient list endpoints with only essential fields.
    """

    phone_number = serializers.CharField(source="wa_number", read_only=True)

    class Meta:
        model = WAApp
        fields = [
            "id",
            "app_name",
            "phone_number",
            "bsp",
            "is_active",
            "is_verified",
        ]
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WAAppList",
            "description": "Minimal WhatsApp Business App for list views",
        }


class WAAppSerializer(BaseSerializer):
    """
    Full serializer for WhatsApp Business App configuration.

    Handles CRUD operations for WA App entities including:
    - Phone number and WABA configuration
    - BSP credentials management
    - Quota and tier tracking
    """

    phone_number = serializers.CharField(
        source="wa_number", help_text="WhatsApp phone number with country code (e.g., +919876543210)"
    )
    bsp_display = serializers.CharField(source="get_bsp_display", read_only=True, help_text="Human-readable BSP name")

    class Meta:
        model = WAApp
        fields = [
            "id",
            "tenant",
            "app_name",
            "description",
            "phone_number",
            "waba_id",
            "phone_number_id",
            "bsp",
            "bsp_display",
            "app_id",
            "is_active",
            "is_verified",
            "daily_limit",
            "messages_sent_today",
            "tier",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "messages_sent_today",
            "is_verified",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "bsp_credentials": {"write_only": True},
            "waba_id": {"help_text": "WhatsApp Business Account ID from META"},
            "bsp": {"help_text": "Business Solution Provider (META, GUPSHUP, etc.)"},
        }
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WAApp",
            "description": "WhatsApp Business App configuration",
            "properties": {
                "id": openapi.Schema(type=openapi.TYPE_INTEGER),
                "app_name": openapi.Schema(type=openapi.TYPE_STRING),
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING),
                "bsp": openapi.Schema(type=openapi.TYPE_STRING, enum=["META", "GUPSHUP", "TWILIO", "MESSAGEBIRD"]),
                "is_active": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                "is_verified": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                "daily_limit": openapi.Schema(type=openapi.TYPE_INTEGER),
            },
        }

    def validate_wa_number(self, value):
        """
        Validate phone number format.
        """
        import re

        if not value:
            raise serializers.ValidationError("Phone number is required")

        # Remove spaces and dashes for validation
        cleaned = re.sub(r"[\s\-]", "", value)

        # Should start with + and contain only digits after
        if not re.match(r"^\+\d{10,15}$", cleaned):
            raise serializers.ValidationError("Phone number must be in E.164 format (e.g., +919876543210)")

        return cleaned


class WAAppSafeSerializer(BaseSerializer):
    """
    Safe serializer for WA App — hides BSP identifiers (app_id, waba_id, phone_number_id).
    Ticket #251: MANAGER and below get this instead of WAAppSerializer.
    """

    phone_number = serializers.CharField(source="wa_number", help_text="WhatsApp phone number with country code")
    bsp_display = serializers.CharField(source="get_bsp_display", read_only=True, help_text="Human-readable BSP name")

    class Meta:
        model = WAApp
        fields = [
            "id",
            "tenant",
            "app_name",
            "description",
            "phone_number",
            "bsp",
            "bsp_display",
            "is_active",
            "is_verified",
            "daily_limit",
            "messages_sent_today",
            "tier",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "messages_sent_today",
            "is_verified",
            "created_at",
            "updated_at",
        ]


class WAAppCreateSerializer(WAAppSerializer):
    """
    Serializer for creating new WA Apps.

    Includes additional validation for required fields during creation.
    """

    class Meta(WAAppSerializer.Meta):
        extra_kwargs = {
            **WAAppSerializer.Meta.extra_kwargs,
            "app_name": {"required": True},
            "wa_number": {"required": True},
            "bsp": {"required": True},
        }

    def validate(self, data):
        """
        Validate that required BSP-specific fields are provided.
        """
        bsp = data.get("bsp")

        if bsp == "META":
            if not data.get("waba_id"):
                raise serializers.ValidationError({"waba_id": "WABA ID is required for META BSP"})
            if not data.get("phone_number_id"):
                raise serializers.ValidationError({"phone_number_id": "Phone Number ID is required for META BSP"})

        return data
