from broadcast.viewsets.messages import BroadcastMessageViewSet


class MobileBroadcastMessageViewSet(BroadcastMessageViewSet):
    """
    Mobile-specific ViewSet for Broadcast Messages.
    
    Identical functionality to BroadcastMessageViewSet but exposed
    under the mobile API prefix for client separation.
    """
    pass
