from broadcast.models import BroadcastPlatformChoices
from broadcast.viewsets.broadcast import BroadcastViewSet


class RCSBroadcastViewSet(BroadcastViewSet):
    """RCS-scoped broadcast API."""

    def get_queryset(self):
        return super().get_queryset().filter(platform=BroadcastPlatformChoices.RCS)
