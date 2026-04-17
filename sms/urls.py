from django.urls import include, path
from rest_framework.routers import DefaultRouter

from sms.views import SMSDLRWebhookView, SMSInboundWebhookView
from sms.viewsets.broadcast import SMSBroadcastViewSet
from sms.viewsets.contacts import SMSContactsViewSet
from sms.viewsets.sms_app import SMSAppViewSet
from sms.viewsets.sms_message import SMSOutboundMessageViewSet
from wa.viewsets.channel_template import SMSTemplateViewSet

router = DefaultRouter()
router.register(r"v1/apps", SMSAppViewSet, basename="sms-apps")
router.register(r"v1/messages", SMSOutboundMessageViewSet, basename="sms-messages")
router.register(r"v1/templates", SMSTemplateViewSet, basename="sms-templates")
router.register(r"v1/broadcast", SMSBroadcastViewSet, basename="sms-broadcast")
router.register(r"v1/contacts", SMSContactsViewSet, basename="sms-contacts")

urlpatterns = [
    path("v1/webhooks/<uuid:sms_app_id>/inbound/", SMSInboundWebhookView.as_view(), name="sms-inbound-webhook"),
    path("v1/webhooks/<uuid:sms_app_id>/dlr/", SMSDLRWebhookView.as_view(), name="sms-dlr-webhook"),
    path("", include(router.urls)),
]
