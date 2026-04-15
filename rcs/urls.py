from django.urls import include, path
from rest_framework.routers import DefaultRouter

from rcs.views import RCSWebhookView
from rcs.viewsets.rcs_app import RCSAppViewSet
from rcs.viewsets.rcs_message import RCSOutboundMessageViewSet

router = DefaultRouter()
router.register(r"v1/apps", RCSAppViewSet, basename="rcs-apps")
router.register(r"v1/messages", RCSOutboundMessageViewSet, basename="rcs-messages")

urlpatterns = [
    path("v1/webhooks/<uuid:rcs_app_id>/", RCSWebhookView.as_view(), name="rcs-webhook"),
    path("", include(router.urls)),
]
