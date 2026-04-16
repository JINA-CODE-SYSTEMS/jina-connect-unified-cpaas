from broadcast.models import BroadcastPlatformChoices
from broadcast.viewsets.broadcast import BroadcastViewSet


class SMSBroadcastViewSet(BroadcastViewSet):
    """SMS-scoped broadcast API."""

    def get_queryset(self):
        return super().get_queryset().filter(platform=BroadcastPlatformChoices.SMS)
