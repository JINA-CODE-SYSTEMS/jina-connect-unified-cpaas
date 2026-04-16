from django.urls import include, path
from rest_framework.routers import DefaultRouter

from rcs.views import RCSWebhookView
from rcs.viewsets.broadcast import RCSBroadcastViewSet
from rcs.viewsets.contacts import RCSContactsViewSet
from rcs.viewsets.rcs_app import RCSAppViewSet
from rcs.viewsets.rcs_message import RCSOutboundMessageViewSet
from wa.viewsets.wa_template_v2 import WATemplateV2ViewSet

router = DefaultRouter()
router.register(r"v1/apps", RCSAppViewSet, basename="rcs-apps")
router.register(r"v1/messages", RCSOutboundMessageViewSet, basename="rcs-messages")
router.register(r"v1/templates", WATemplateV2ViewSet, basename="rcs-templates")
router.register(r"v1/broadcast", RCSBroadcastViewSet, basename="rcs-broadcast")
router.register(r"v1/contacts", RCSContactsViewSet, basename="rcs-contacts")

urlpatterns = [
    path("v1/webhooks/<uuid:rcs_app_id>/", RCSWebhookView.as_view(), name="rcs-webhook"),
    path("", include(router.urls)),
]
