
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .viewsets.broadcast import BroadcastViewSet
from .viewsets.dashboard import BroadcastDashboardViewSet
from .viewsets.messages import BroadcastMessageViewSet
from .url_tracker.viewsets import TrackedURLViewSet

# from wa.viewsets.broadcast import BroadcastViewSet


router = DefaultRouter()


router.register(r'messages', BroadcastMessageViewSet, basename='broadcast-messages')
router.register(r'url-tracking', TrackedURLViewSet, basename='url-tracking')
router.register(r'dashboard', BroadcastDashboardViewSet, basename='broadcast-dashboard')
router.register(r'', BroadcastViewSet, basename='tenant-broadcasts')

urlpatterns = [
    path("", include(router.urls)),
]

