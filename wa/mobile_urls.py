from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .viewsets.mobile_broadcast import MobileWABroadcastViewSet
from .viewsets.rate_card import RateCardViewSet

router = DefaultRouter()

router.register(r'broadcast', MobileWABroadcastViewSet, basename='mobile-wabroadcast')
router.register(r'rate-card', RateCardViewSet, basename='mobile-wa-rate-card')

urlpatterns = [
    path("", include(router.urls)),
]
