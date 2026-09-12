from django.urls import include, path
from rest_framework.routers import DefaultRouter

from wa.viewsets.broadcast import WABroadcastViewSet

# Public (unauthenticated) webhook receivers
from .views import GupshupWebhookView, MetaWebhookView
from .viewsets.contacts import WAContactsViewSet
from .viewsets.order_viewset import WAOrderViewSet
from .viewsets.rate_card import RateCardViewSet

# V2 BSP-Agnostic Viewsets
from .viewsets.wa_app import WAAppViewSet
from .viewsets.wa_message import WAMessageViewSet
from .viewsets.wa_subscription_v2 import WASubscriptionV2ViewSet
from .viewsets.wa_template_v2 import WATemplateV2ViewSet
from .viewsets.wa_webhook_event import WAWebhookEventViewSet

router = DefaultRouter()

# Legacy endpoints (existing)
router.register(r"contacts", WAContactsViewSet, basename="wacontacts")
router.register(r"broadcast", WABroadcastViewSet, basename="wabroadcast")
router.register(r"rate-card", RateCardViewSet, basename="wa-rate-card")

# V2 BSP-Agnostic endpoints
router.register(r"v2/apps", WAAppViewSet, basename="wa-apps-v2")
router.register(r"v2/templates", WATemplateV2ViewSet, basename="wa-templates-v2")
router.register(r"v2/messages", WAMessageViewSet, basename="wa-messages-v2")
router.register(r"v2/webhook-events", WAWebhookEventViewSet, basename="wa-webhook-events-v2")
router.register(r"v2/subscriptions", WASubscriptionV2ViewSet, basename="wa-subscriptions-v2")
router.register(r"v2/orders", WAOrderViewSet, basename="wa-orders-v2")

urlpatterns = [
    # Public webhook receivers (unauthenticated — BSPs POST here).
    #
    # Two shapes per BSP, both permanent (#310):
    #
    # * The unsuffixed path is the legacy, deployment-wide receiver. It is
    #   registered in live Meta App Dashboards and with Gupshup, so it keeps
    #   working byte-for-byte as it did: an upgrade must not make anyone
    #   re-register a URL. It authenticates against the global secret and is
    #   therefore single-app — it must not be shared between clients.
    # * The suffixed path carries ``TenantWAApp.webhook_identifier`` and names
    #   exactly one app, which is what lets a per-app secret be selected before
    #   the body is parsed. ``<str:...>`` rather than a stricter converter so an
    #   unowned or malformed identifier is answered by the view (200, counted,
    #   nothing written) instead of becoming a 404 — META throttles delivery to
    #   the whole deployment on non-200s.
    path("v2/webhooks/gupshup/", GupshupWebhookView.as_view(), name="gupshup-webhook"),
    path(
        "v2/webhooks/gupshup/<str:webhook_identifier>/",
        GupshupWebhookView.as_view(),
        name="gupshup-webhook-app",
    ),
    path("v2/webhooks/meta/", MetaWebhookView.as_view(), name="meta-webhook"),
    path(
        "v2/webhooks/meta/<str:webhook_identifier>/",
        MetaWebhookView.as_view(),
        name="meta-webhook-app",
    ),
    # DRF router
    path("", include(router.urls)),
]
