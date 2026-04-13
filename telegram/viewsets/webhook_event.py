"""
TelegramWebhookEvent ViewSet — read-only event audit + retry action.
"""
import logging

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from telegram.models import TelegramWebhookEvent
from telegram.serializers import TelegramWebhookEventSerializer

logger = logging.getLogger(__name__)


class TelegramWebhookEventViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only listing of webhook events, scoped to the user's tenant."""

    permission_classes = [IsAuthenticated]
    serializer_class = TelegramWebhookEventSerializer
    filterset_fields = ["event_type", "is_processed"]

    def get_queryset(self):
        return TelegramWebhookEvent.objects.filter(
            tenant__tenant_users__user=self.request.user
        ).order_by("-created_at")

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Re-queue event processing."""
        event = self.get_object()
        if event.is_processed:
            return Response({"ok": False, "error": "Event already processed"})

        from telegram.tasks import process_tg_event_task

        process_tg_event_task.delay(str(event.pk))
        return Response({"ok": True, "message": "Event re-queued for processing"})
