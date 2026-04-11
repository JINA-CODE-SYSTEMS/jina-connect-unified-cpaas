from broadcast.viewsets.broadcast import BroadcastViewSet


class MobileBroadcastViewSet(BroadcastViewSet):
    """
    Mobile-specific ViewSet for Broadcasts.

    Identical functionality to BroadcastViewSet but exposed
    under the mobile API prefix for client separation.
    """

    pass
