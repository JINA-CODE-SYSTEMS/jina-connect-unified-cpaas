"""
TelegramBotApp ViewSet — CRUD + custom actions for bot management.
"""

import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from telegram.constants import WEBHOOK_ALLOWED_UPDATES
from telegram.models import TelegramBotApp
from telegram.serializers import TelegramBotAppCreateSerializer, TelegramBotAppSerializer

logger = logging.getLogger(__name__)


class TelegramBotAppViewSet(viewsets.ModelViewSet):
    """CRUD for Telegram bot apps, scoped to the user's tenant."""

    permission_classes = [IsAuthenticated]
    serializer_class = TelegramBotAppSerializer

    def get_queryset(self):
        return TelegramBotApp.objects.filter(tenant__tenant_users__user=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return TelegramBotAppCreateSerializer
        return TelegramBotAppSerializer

    def perform_create(self, serializer):
        """Create the bot app, auto-populate bot_username and bot_user_id."""
        from telegram.services.bot_client import TelegramBotClient

        tenant_user = self.request.user.user_tenants.first()
        if not tenant_user:
            raise PermissionDenied("User has no associated tenant.")
        bot_app = serializer.save(tenant=tenant_user.tenant)

        # Populate bot info from getMe
        try:
            client = TelegramBotClient(token=bot_app.bot_token)
            me = client.get_me()
            bot_app.bot_username = me.get("username", "")
            bot_app.bot_user_id = me.get("id")
            bot_app.save(update_fields=["bot_username", "bot_user_id"])
        except Exception:
            logger.exception("[TelegramBotAppViewSet] Failed to fetch bot info on create")

    @action(detail=True, methods=["post"])
    def register_webhook(self, request, pk=None):
        """Register the webhook with Telegram."""
        bot_app = self.get_object()
        from telegram.services.bot_client import TelegramAPIError, TelegramBotClient

        try:
            client = TelegramBotClient(token=bot_app.bot_token)
            client.set_webhook(
                url=bot_app.webhook_url,
                secret_token=bot_app.webhook_secret,
                allowed_updates=WEBHOOK_ALLOWED_UPDATES,
            )
            return Response({"ok": True, "webhook_url": bot_app.webhook_url})
        except TelegramAPIError as exc:
            return Response(
                {"ok": False, "error": exc.description},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"])
    def test_auth(self, request, pk=None):
        """Test bot authentication by calling getMe."""
        bot_app = self.get_object()
        from telegram.services.bot_client import TelegramAPIError, TelegramBotClient

        try:
            client = TelegramBotClient(token=bot_app.bot_token)
            me = client.get_me()
            return Response({"ok": True, "bot": me})
        except TelegramAPIError as exc:
            return Response(
                {"ok": False, "error": exc.description},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """Deactivate the bot and remove the webhook."""
        bot_app = self.get_object()
        from telegram.services.bot_client import TelegramBotClient

        try:
            client = TelegramBotClient(token=bot_app.bot_token)
            client.delete_webhook()
        except Exception:
            logger.exception("[TelegramBotAppViewSet] Failed to delete webhook on deactivate")

        bot_app.is_active = False
        bot_app.save(update_fields=["is_active"])
        return Response({"ok": True, "is_active": False})
