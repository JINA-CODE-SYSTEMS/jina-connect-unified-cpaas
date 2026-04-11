from wa.viewsets.broadcast import WABroadcastViewSet


class MobileWABroadcastViewSet(WABroadcastViewSet):
    """
    Mobile-specific ViewSet for WA Broadcasts.

    Identical functionality to WABroadcastViewSet but exposed
    under the mobile API prefix for client separation.
    Includes get_sending_quota and get_charge_breakdown actions.
    """

    pass
