from django.urls import path
from rest_framework.routers import DefaultRouter

from support.views import GitHubWebhookView, SupportTicketViewSet

router = DefaultRouter()
router.register(r"tickets", SupportTicketViewSet, basename="support-tickets")

urlpatterns = [
    path("github/webhook/", GitHubWebhookView.as_view(), name="github-webhook"),
    *router.urls,
]
