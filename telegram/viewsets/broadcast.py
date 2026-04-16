from broadcast.models import BroadcastPlatformChoices
from broadcast.viewsets.broadcast import BroadcastViewSet


class TelegramBroadcastViewSet(BroadcastViewSet):
    """Telegram-scoped broadcast API."""

    def get_queryset(self):
        return super().get_queryset().filter(platform=BroadcastPlatformChoices.TELEGRAM)
