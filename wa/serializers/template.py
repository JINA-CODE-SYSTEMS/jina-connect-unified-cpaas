"""
WATemplate Serializer (Legacy)

Serializer for the legacy WhatsApp Template model.
This provides backwards compatibility with existing code.
"""

from rest_framework import serializers

from abstract.serializers import BaseSerializer
from wa.models import WATemplate
from wa.serializers.fields import TemplateButtonsField, TemplateCardsField


class WATemplateSerializer(BaseSerializer):
    """
    Serializer for WhatsApp templates (legacy).

    Handles creation and updates of WhatsApp templates including:
    - Template metadata (name, language, category, type)
    - Content validation with dynamic parameters
    - Button validation with type-specific requirements
    - Card validation for carousel templates
    """

    buttons = TemplateButtonsField(
        required=False, allow_null=True, help_text="List of template buttons (maximum 3 allowed)"
    )

    cards = TemplateCardsField(
        required=False, allow_null=True, help_text="List of template cards for carousel templates (maximum 10 allowed)"
    )

    tag_names = serializers.SerializerMethodField(help_text="List of tag names associated with the template")
    template_media_url = serializers.SerializerMethodField(help_text="URL of the media associated with the template")
    cards_media_urls = serializers.SerializerMethodField(help_text="List of media URLs for each card in the template")

    def get_template_media_url(self, obj):
        if obj.tenant_media:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.tenant_media.media.url)
            return obj.tenant_media.media.url
        return None

    def get_cards_media_urls(self, obj):
        media_urls = []
        if obj.card_media:
            request = self.context.get("request")
            for media in obj.card_media.all():
                if request:
                    media_urls.append(request.build_absolute_uri(media.media.url))
                else:
                    media_urls.append(media.media.url)
        return media_urls

    def get_tag_names(self, obj):
        if hasattr(obj, "tag"):
            return list(obj.tag.values_list("name", flat=True))
        return []

    class Meta:
        model = WATemplate
        fields = "__all__"
        extra_kwargs = {
            "element_name": {"help_text": "Unique element name (letters, numbers, underscores, hyphens only)"},
            "language_code": {"help_text": "Template language code (e.g., en_US, hi)"},
            "category": {"help_text": "Template category determines usage restrictions"},
            "template_type": {"help_text": "Type of template content"},
            "content": {"help_text": "Template body text. Use {{name}}, {{code}}, etc. for dynamic parameters"},
            "number": {"read_only": True},
        }

    def validate_element_name(self, value):
        """Validate element name format."""
        import re

        if not value:
            raise serializers.ValidationError("Element name is required")

        if " " in value:
            raise serializers.ValidationError("Element name cannot contain spaces")

        if not value.replace("_", "").replace("-", "").isalnum():
            raise serializers.ValidationError(
                "Element name can only contain letters, numbers, underscores, and hyphens"
            )

        value = re.sub(r"\s+", "_", value).strip("_")
        return value

    def validate(self, data):
        """Object-level validation."""
        buttons = data.get("buttons")
        cards = data.get("cards")
        category = data.get("category")

        has_buttons = buttons is not None and len(buttons) > 0
        has_cards = cards is not None and len(cards) > 0

        if has_buttons and has_cards:
            raise serializers.ValidationError(
                {"non_field_errors": ["Templates cannot have both cards and template-level buttons."]}
            )

        if category == "AUTHENTICATION" and not has_buttons:
            raise serializers.ValidationError({"buttons": "OTP buttons are mandatory for AUTHENTICATION templates"})

        return data
