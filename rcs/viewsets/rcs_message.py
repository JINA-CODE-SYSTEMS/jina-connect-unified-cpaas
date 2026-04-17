import logging

from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from rcs.models import RCSOutboundMessage
from rcs.serializers import RCSOutboundMessageSerializer

logger = logging.getLogger(__name__)


class RCSOutboundMessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RCSOutboundMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return RCSOutboundMessage.objects.filter(tenant__tenant_users__user=self.request.user).order_by("-created_at")

    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        """Revoke a sent RCS message via the provider API (#112).

        Only messages in SENT or DELIVERED status can be revoked.
        """
        from rcs.providers import get_rcs_provider

        msg = self.get_object()

        if msg.status not in ("SENT", "DELIVERED"):
            return Response(
                {"detail": f"Cannot revoke message in status {msg.status}."},
                status=400,
            )

        try:
            provider = get_rcs_provider(msg.rcs_app)
            provider.revoke_message(msg.provider_message_id)
            msg.status = "REVOKED"
            msg.save(update_fields=["status"])
            logger.info("[RCS revoke] Message %s revoked successfully", msg.pk)
            return Response({"detail": "Message revoked.", "status": msg.status})
        except Exception as exc:
            logger.exception("[RCS revoke] Failed to revoke message %s", msg.pk)
            return Response({"detail": f"Revocation failed: {exc}"}, status=502)
