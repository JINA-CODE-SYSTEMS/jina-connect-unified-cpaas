"""
WATemplate Serializers (v2)

Serializers for canonical WhatsApp Message Templates.
"""

from drf_yasg import openapi
from rest_framework import serializers

from abstract.serializers import BaseSerializer
from wa.models import WATemplate


class WATemplateV2ListSerializer(BaseSerializer):
    """
    Serializer for template list views.

    Includes display name, content, and number so broadcast pages
    can show template names, render previews, and submit correctly.
    """

    tenant_media_url = serializers.SerializerMethodField(
        read_only=True, help_text="Local file URL for header media (IMAGE/VIDEO/DOCUMENT templates)"
    )
    tenant_media_id = serializers.UUIDField(
        source="tenant_media.id", read_only=True, help_text="TenantMedia ID for header media"
    )

    class Meta:
        model = WATemplate
        fields = [
            "id",
            "name",
            "element_name",
            "language_code",
            "category",
            "template_type",
            "status",
            "is_active",
            "number",
            "content",
            "header",
            "footer",
            "buttons",
            "placeholder_mapping",
            "tenant_media",
            "tenant_media_id",
            "tenant_media_url",
            "example_media_url",
            "cards",
        ]
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WATemplateList",
            "description": "Minimal WhatsApp Template for list views",
        }

    def get_tenant_media_url(self, obj):
        if obj.tenant_media_id:
            request = self.context.get("request")
            serve_path = f"/tenants/tenant-media/{obj.tenant_media_id}/serve/"
            if request:
                return request.build_absolute_uri(serve_path)
            return serve_path
        return None


class WATemplateV2Serializer(BaseSerializer):
    """
    Full serializer for canonical WhatsApp Templates (v2).

    Handles CRUD operations for templates including:
    - Template content (header, body, footer)
    - Button configurations
    - Carousel cards
    - BSP sync status tracking
    """

    category_display = serializers.CharField(
        source="get_category_display", read_only=True, help_text="Human-readable category name"
    )
    status_display = serializers.CharField(
        source="get_status_display", read_only=True, help_text="Human-readable status"
    )
    template_type_display = serializers.CharField(
        source="get_template_type_display", read_only=True, help_text="Human-readable template type"
    )
    wa_app_phone = serializers.CharField(
        source="wa_app.phone_number", read_only=True, help_text="Phone number of the associated WA App"
    )
    tenant_media_url = serializers.SerializerMethodField(
        read_only=True, help_text="Local file URL for header media (IMAGE/VIDEO/DOCUMENT templates)"
    )
    tenant_media_id = serializers.UUIDField(
        source="tenant_media.id", read_only=True, help_text="TenantMedia ID for header media"
    )
    card_media_details = serializers.SerializerMethodField(
        read_only=True, help_text="Local file URLs for carousel card media, keyed by card_index"
    )

    class Meta:
        model = WATemplate
        fields = [
            "id",
            "wa_app",
            "wa_app_phone",
            "name",
            "number",
            "element_name",
            "language_code",
            "category",
            "category_display",
            "template_type",
            "template_type_display",
            "status",
            "status_display",
            "content",
            "header",
            "footer",
            "buttons",
            "example_body",
            "example_header",
            "media_handle",
            "example_media_url",
            "tenant_media",
            "tenant_media_id",
            "tenant_media_url",
            "cards",
            "card_media",
            "card_media_details",
            "meta_template_id",
            "bsp_template_id",
            "placeholder_mapping",
            "vertical",
            "is_lto",
            "lto_text",
            "lto_has_expiration",
            "error_message",
            "rejection_reason",
            "is_active",
            "needs_sync",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "meta_template_id",
            "bsp_template_id",
            "error_message",
            "rejection_reason",
            "placeholder_mapping",
            "tenant_media",
            "tenant_media_id",
            "tenant_media_url",
            "card_media",
            "card_media_details",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "element_name": {"help_text": "Unique template name (lowercase, underscores only)"},
            "language_code": {"help_text": "Template language code (e.g., en, en_US)"},
            "content": {"help_text": "Body text with placeholders {{name}}, {{order_id}}"},
            "buttons": {"help_text": "Array of button objects (max 3)"},
            "cards": {"help_text": "Carousel card objects (max 10)"},
        }
        swagger_schema_fields = {
            "type": openapi.TYPE_OBJECT,
            "title": "WATemplate",
            "description": "WhatsApp Message Template (v2)",
        }

    def validate_element_name(self, value):
        """
        Validate element name format - lowercase with underscores only.
        """
        import re

        if not value:
            raise serializers.ValidationError("Element name is required")

        # Convert to lowercase
        value = value.lower()

        # Replace spaces with underscores
        value = re.sub(r"\s+", "_", value).strip("_")

        # Check format - only lowercase letters, numbers, and underscores
        if not re.match(r"^[a-z][a-z0-9_]*$", value):
            raise serializers.ValidationError(
                "Element name must start with a letter and contain only lowercase letters, numbers, and underscores"
            )

        return value

    def get_tenant_media_url(self, obj):
        """Resolve tenant_media FK to a permanent serve-redirect URL.

        Returns the ``/serve/`` endpoint URL instead of a raw signed
        cloud-storage URL.  The serve endpoint issues a 302 redirect to
        a *fresh* signed URL on every request, so the URL embedded in
        the API response never expires.  (Fixes #377)
        """
        if obj.tenant_media_id:
            request = self.context.get("request")
            serve_path = f"/tenants/tenant-media/{obj.tenant_media_id}/serve/"
            if request:
                return request.build_absolute_uri(serve_path)
            return serve_path
        return None

    def get_card_media_details(self, obj):
        """
        Return card media as a list matching card order.
        Always returns an array for templates with cards — each entry contains
        the media_handle from the card JSON and, if a TenantMedia is linked,
        the local media_id and media_url for preview.
        """
        if not obj.cards:
            return None

        # Build a card_index → TenantMedia lookup from M2M
        card_media_map = {}
        for tm in obj.card_media.all():
            if tm.card_index is not None:
                card_media_map[tm.card_index] = tm

        request = self.context.get("request")
        result = []
        for i, card in enumerate(obj.cards):
            entry = {
                "card_index": i,
                "media_handle": card.get("media_handle") if isinstance(card, dict) else None,
                "media_id": None,
                "media_url": None,
            }
            tm = card_media_map.get(i)
            if tm:
                serve_path = f"/tenants/tenant-media/{tm.id}/serve/"
                url = request.build_absolute_uri(serve_path) if request else serve_path
                entry["media_id"] = str(tm.id)
                entry["media_url"] = url
            result.append(entry)
        return result

    def validate(self, data):
        """
        Object-level validation for template consistency.
        """
        buttons = data.get("buttons")
        cards = data.get("cards")
        category = data.get("category")
        template_type = data.get("template_type")
        content = data.get("content")

        # Mutual exclusivity: cannot have both buttons and cards
        has_buttons = buttons is not None and len(buttons) > 0
        has_cards = cards is not None and len(cards) > 0

        if has_buttons and has_cards:
            raise serializers.ValidationError(
                {
                    "non_field_errors": [
                        "Templates cannot have both cards and template-level buttons. "
                        "When using cards, buttons should be placed inside individual cards."
                    ]
                }
            )

        # Non-CAROUSEL templates must not have cards
        if has_cards and template_type and template_type != "CAROUSEL":
            raise serializers.ValidationError(
                {
                    "cards": (
                        f"Cards are only allowed for CAROUSEL templates, "
                        f"not {template_type}. Body text should be in 'content'."
                    )
                }
            )

        # Non-CAROUSEL/CATALOG templates must have content (body text)
        if template_type and template_type not in ("CAROUSEL", "CATALOG"):
            if not content or not content.strip():
                raise serializers.ValidationError({"content": "Body text is required for non-carousel templates."})

        # AUTHENTICATION category requires OTP buttons
        if category == "AUTHENTICATION":
            if not has_buttons:
                raise serializers.ValidationError({"buttons": "OTP buttons are mandatory for AUTHENTICATION templates"})

            # Check all buttons are OTP type
            non_otp = [btn for btn in buttons if btn.get("type") != "OTP"]
            if non_otp:
                raise serializers.ValidationError({"buttons": "AUTHENTICATION templates can only have OTP buttons"})

        return data
