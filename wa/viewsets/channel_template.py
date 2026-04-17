"""Channel-scoped template viewsets that filter WATemplate by platform."""

from wa.models import WATemplate
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

    def perform_create(self, serializer):
        tenant_user = self._get_tenant_user()
        serializer.save(
            platform=self._channel_platform,
            tenant=tenant_user.tenant,
        )


class SMSTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """SMS-scoped template API."""

    _channel_platform = "SMS"


class TelegramTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """Telegram-scoped template API."""

    _channel_platform = "TELEGRAM"


class RCSTemplateViewSet(_ChannelTemplateMixin, WATemplateV2ViewSet):
    """RCS-scoped template API."""

    _channel_platform = "RCS"
