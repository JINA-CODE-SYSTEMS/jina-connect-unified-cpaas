from contacts.models import ContactSource, TenantContact
from contacts.serializers import TenantContactSerializer
from contacts.viewsets.contacts import ContactsViewSet


class SMSContactsViewSet(ContactsViewSet):
    """SMS-scoped contacts API."""

    queryset = TenantContact.objects.all()
    serializer_class = TenantContactSerializer

    def get_queryset(self):
        return super().get_queryset().filter(source__in=[ContactSource.SMS, ContactSource.MANUAL])
