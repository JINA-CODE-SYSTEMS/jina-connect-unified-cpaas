from django.db.models import Max, Q

from contacts.viewsets.contacts import ContactsViewSet
from team_inbox.models import MessageDirectionChoices, MessagePlatformChoices
from wa.models import WAContacts
from wa.serializers import WAContactsSerializer


class WAContactsViewSet(ContactsViewSet):
    """
    ViewSet for managing WAContacts.
    """

    queryset = WAContacts.objects.all()
    serializer_class = WAContactsSerializer

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                _last_incoming_wa_timestamp=Max(
                    "messages__timestamp",
                    filter=Q(
                        messages__direction=MessageDirectionChoices.INCOMING,
                        messages__platform=MessagePlatformChoices.WHATSAPP,
                    ),
                )
            )
        )
