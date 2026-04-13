from django.urls import include, path
from rest_framework.routers import DefaultRouter

from telegram.views import TelegramWebhookView
from telegram.viewsets.bot_app import TelegramBotAppViewSet
from telegram.viewsets.webhook_event import TelegramWebhookEventViewSet

router = DefaultRouter()
router.register(r"v1/bots", TelegramBotAppViewSet, basename="tg-bots")
router.register(r"v1/webhook-events", TelegramWebhookEventViewSet, basename="tg-webhook-events")

urlpatterns = [
    path("v1/webhooks/<uuid:bot_app_id>/", TelegramWebhookView.as_view(), name="tg-webhook"),
    path("", include(router.urls)),
]
