
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from wa.viewsets.broadcast import WABroadcastViewSet

# Public (unauthenticated) webhook receivers
from .views import GupshupWebhookView, MetaWebhookView
from .viewsets.contacts import WAContactsViewSet
from .viewsets.rate_card import RateCardViewSet
# V2 BSP-Agnostic Viewsets
from .viewsets.wa_app import WAAppViewSet
from .viewsets.wa_message import WAMessageViewSet
from .viewsets.wa_subscription_v2 import WASubscriptionV2ViewSet
from .viewsets.wa_template_v2 import WATemplateV2ViewSet
from .viewsets.wa_webhook_event import WAWebhookEventViewSet
from .viewsets.order_viewset import WAOrderViewSet

router = DefaultRouter()

# Legacy endpoints (existing)
router.register(r'contacts', WAContactsViewSet, basename='wacontacts')
router.register(r'broadcast', WABroadcastViewSet, basename='wabroadcast')
router.register(r'rate-card', RateCardViewSet, basename='wa-rate-card')

# V2 BSP-Agnostic endpoints
router.register(r'v2/apps', WAAppViewSet, basename='wa-apps-v2')
router.register(r'v2/templates', WATemplateV2ViewSet, basename='wa-templates-v2')
router.register(r'v2/messages', WAMessageViewSet, basename='wa-messages-v2')
router.register(r'v2/webhook-events', WAWebhookEventViewSet, basename='wa-webhook-events-v2')
router.register(r'v2/subscriptions', WASubscriptionV2ViewSet, basename='wa-subscriptions-v2')
router.register(r'v2/orders', WAOrderViewSet, basename='wa-orders-v2')

urlpatterns = [
    # Public webhook receivers (unauthenticated — BSPs POST here)
    path("v2/webhooks/gupshup/", GupshupWebhookView.as_view(), name="gupshup-webhook"),
    path("v2/webhooks/meta/", MetaWebhookView.as_view(), name="meta-webhook"),

    # DRF router
    path("", include(router.urls)),
]

