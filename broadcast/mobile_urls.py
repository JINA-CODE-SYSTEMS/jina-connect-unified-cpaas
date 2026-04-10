from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .viewsets.mobile_broadcast import MobileBroadcastViewSet
from .viewsets.mobile_messages import MobileBroadcastMessageViewSet

router = DefaultRouter()

router.register(r'messages', MobileBroadcastMessageViewSet, basename='mobile-broadcast-messages')
router.register(r'', MobileBroadcastViewSet, basename='mobile-tenant-broadcasts')

urlpatterns = [
    path("", include(router.urls)),
]
