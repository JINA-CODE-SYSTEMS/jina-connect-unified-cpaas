from django.urls import include, path
from rest_framework.routers import DefaultRouter

from telegram.views import TelegramWebhookView
from telegram.viewsets.bot_app import TelegramBotAppViewSet
from telegram.viewsets.broadcast import TelegramBroadcastViewSet
from telegram.viewsets.contacts import TelegramContactsViewSet
from telegram.viewsets.message import TelegramMessageViewSet
from telegram.viewsets.webhook_event import TelegramWebhookEventViewSet
from wa.viewsets.channel_template import TelegramTemplateViewSet

router = DefaultRouter()
router.register(r"v1/bots", TelegramBotAppViewSet, basename="tg-bots")
router.register(r"v1/webhook-events", TelegramWebhookEventViewSet, basename="tg-webhook-events")
router.register(r"v1/templates", TelegramTemplateViewSet, basename="tg-templates")
router.register(r"v1/broadcast", TelegramBroadcastViewSet, basename="tg-broadcast")
router.register(r"v1/contacts", TelegramContactsViewSet, basename="tg-contacts")
router.register(r"v1/messages", TelegramMessageViewSet, basename="tg-messages")

urlpatterns = [
    path("v1/webhooks/<uuid:bot_app_id>/", TelegramWebhookView.as_view(), name="tg-webhook"),
    path("", include(router.urls)),
]
