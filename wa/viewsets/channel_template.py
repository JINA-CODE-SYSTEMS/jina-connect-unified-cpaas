"""Channel-scoped template viewsets that filter WATemplate by platform."""

from rest_framework import status as drf_status
from rest_framework.response import Response

from wa.models import TemplateStatus, WATemplate
from wa.serializers import WATemplateV2Serializer
from wa.viewsets.wa_template_v2 import WATemplateV2ViewSet


class _ChannelTemplateMixin:
    """Mixin to scope WATemplate queryset to a specific platform."""

    _channel_platform: str = ""

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return WATemplate.objects.filter(platform=self._channel_platform)
        return WATemplate.objects.filter(
            platform=self._channel_platform,
            tenant__tenant_users__user=user,
        )

    def create(self, request, *args, **kwargs):
        """Override create to inject platform and tenant for non-WA templates."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tenant_user = self._get_tenant_user()
        template = serializer.save(
            platform=self._channel_platform,
            tenant=tenant_user.tenant,
            status=TemplateStatus.DRAFT,
            needs_sync=True,
        )
        return Response(WATemplateV2Serializer(template).data, status=drf_status.HTTP_201_CREATED)


class SMSTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """SMS-scoped template API."""

    _channel_platform = "SMS"


class TelegramTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """Telegram-scoped template API."""

    _channel_platform = "TELEGRAM"


class RCSTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """RCS-scoped template API."""

    _channel_platform = "RCS"
