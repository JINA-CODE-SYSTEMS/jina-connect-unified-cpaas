"""Channel-scoped template viewsets that filter WATemplate by platform."""

from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.response import Response

from wa.models import TemplateStatus, WATemplate
from wa.serializers import WATemplateV2Serializer
from wa.viewsets.wa_template_v2 import WATemplateV2ViewSet


class _ChannelTemplateMixin:
    """Mixin to scope WATemplate queryset to a specific platform."""

    _channel_platform: str = ""
    _MEDIA_TEMPLATE_TYPES = {"IMAGE", "VIDEO", "DOCUMENT"}

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return WATemplate.objects.filter(platform=self._channel_platform)
        return WATemplate.objects.filter(
            platform=self._channel_platform,
            tenant__tenant_users__user=user,
        )

    def get_serializer(self, *args, **kwargs):
        """Make wa_app optional for non-WA channel templates."""
        serializer = super().get_serializer(*args, **kwargs)
        if hasattr(serializer, "fields") and "wa_app" in serializer.fields:
            serializer.fields["wa_app"].required = False
            serializer.fields["wa_app"].allow_null = True
            serializer.fields["wa_app"].default = None
        return serializer

    def create(self, request, *args, **kwargs):
        """Override create to inject platform and tenant for non-WA templates."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tenant_user = self._get_tenant_user()
        if not tenant_user:
            from rest_framework.exceptions import ValidationError

            raise ValidationError("Could not determine tenant for this request.")

        # For non-WA channels, media types (IMAGE/VIDEO/DOCUMENT) require example_media_url.
        # Telegram (and other channels) use URL-based media references — file upload is not supported.
        template_type = serializer.validated_data.get("template_type", "")
        if (
            self._channel_platform != "WHATSAPP"
            and template_type in self._MEDIA_TEMPLATE_TYPES
            and not serializer.validated_data.get("example_media_url")
        ):
            from rest_framework.exceptions import ValidationError as DRFValidationError

            raise DRFValidationError(
                {"example_media_url": f"example_media_url is required for {template_type} templates."}
            )

        # Non-WhatsApp templates don't need approval - set APPROVED immediately
        # WhatsApp templates need to go through Meta approval process
        initial_status = TemplateStatus.APPROVED if self._channel_platform != "WHATSAPP" else TemplateStatus.DRAFT
        needs_sync = self._channel_platform == "WHATSAPP"  # Only WhatsApp templates need BSP sync

        template = serializer.save(
            platform=self._channel_platform,
            tenant=tenant_user.tenant,
            status=initial_status,
            needs_sync=needs_sync,
        )
        return Response(WATemplateV2Serializer(template).data, status=drf_status.HTTP_201_CREATED)


class SMSTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """SMS-scoped template API."""

    _channel_platform = "SMS"


class TelegramTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """Telegram-scoped template API."""

    _channel_platform = "TELEGRAM"

    # Only these types are supported by the Telegram Bot API.
    # WA-specific types (CAROUSEL, CATALOG, PRODUCT, ORDER_DETAILS, LOCATION) are excluded.
    _TELEGRAM_TYPES = {"TEXT", "IMAGE", "VIDEO", "DOCUMENT"}

    @action(detail=False, methods=["get"], url_path="types")
    def types(self, request):
        """Return only Telegram-applicable template types."""
        from wa.models import TemplateType

        result = [
            {"value": value, "label": label, "enabled": True}
            for value, label in TemplateType.choices
            if value in self._TELEGRAM_TYPES
        ]
        return Response(result)


class RCSTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """RCS-scoped template API."""

    _channel_platform = "RCS"
