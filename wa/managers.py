from django.db.models import Case, CharField, Count, ExpressionWrapper, F, FloatField, Q, Value, When

from abstract.managers import BaseTenantModelForFilterUserManager
from broadcast.models import BroadcastPlatformChoices
from contacts.models import ContactSource


class BroadcastMessageStatusManager(BaseTenantModelForFilterUserManager):
    def broadcast_summary(self, broadcast_id=None):
        qs = self.get_queryset()
        if broadcast_id is not None:
            qs = qs.filter(broadcast_id=broadcast_id)
        return (
            qs.values(
                broadcast_pk=F("broadcast__id"),  # 👈 avoid conflict
                broadcast_name=F("broadcast__name"),
                broadcast_scheduling_time=F("broadcast__scheduling_time"),
            )
            .annotate(
                queued=Count("id", filter=Q(status="QUEUED")),
                pending=Count("id", filter=Q(status="PENDING")),
                delivered_only=Count("id", filter=Q(status="DELIVERED")),
                sent_only=Count("id", filter=Q(status="SENT")),
                read_only=Count("id", filter=Q(status="READ")),
                replied=Count("id", filter=Q(status="REPLIED")),
                failed=Count("id", filter=Q(status="FAILED")),
                total_messages=Count("id"),
            )
            .annotate(
                sent=F("sent_only") + F("read_only") + F("replied"),
                read=F("read_only") + F("replied"),
                delivered=F("delivered_only") + F("sent"),
            )
            .annotate(
                broad_status=Case(
                    When(Q(queued__gt=0) | Q(pending__gt=0), then=Value("PENDING")),
                    default=Value("COMPLETED"),
                    output_field=CharField(),
                ),
                # ✅ Percentages
                success_percent=ExpressionWrapper(
                    100.0 * F("delivered") / F("total_messages"),
                    output_field=FloatField(),
                ),
                read_percent=ExpressionWrapper(
                    100.0 * F("read") / F("total_messages"),
                    output_field=FloatField(),
                ),
                replied_percent=ExpressionWrapper(
                    100.0 * F("replied") / F("total_messages"),
                    output_field=FloatField(),
                ),
                failure_percent=ExpressionWrapper(
                    100.0 * F("failed") / F("total_messages"),
                    output_field=FloatField(),
                ),
            )
        )


class WAContactsManager(BaseTenantModelForFilterUserManager):
    def get_queryset(self):
        """
        Source needs to be WHATSAPP or Manual for WA contacts
        """
        return super().get_queryset().filter(source__in=[ContactSource.WHATSAPP, ContactSource.MANUAL])


class WABroadcastManager(BaseTenantModelForFilterUserManager):
    def get_queryset(self):
        """
        Manager for WA Broadcasts
        """
        return super().get_queryset().filter(platform=BroadcastPlatformChoices.WHATSAPP)
