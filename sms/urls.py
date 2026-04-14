from django.urls import include, path
from rest_framework.routers import DefaultRouter

from sms.views import SMSDLRWebhookView, SMSInboundWebhookView
from sms.viewsets.sms_app import SMSAppViewSet
from sms.viewsets.sms_message import SMSOutboundMessageViewSet

router = DefaultRouter()
router.register(r"v1/apps", SMSAppViewSet, basename="sms-apps")
router.register(r"v1/messages", SMSOutboundMessageViewSet, basename="sms-messages")

urlpatterns = [
    path("v1/webhooks/<uuid:sms_app_id>/inbound/", SMSInboundWebhookView.as_view(), name="sms-inbound-webhook"),
    path("v1/webhooks/<uuid:sms_app_id>/dlr/", SMSDLRWebhookView.as_view(), name="sms-dlr-webhook"),
    path("", include(router.urls)),
]
