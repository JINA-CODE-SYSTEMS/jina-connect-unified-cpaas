from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .viewsets.contacts import ContactsViewSet

# from wa.viewsets.broadcast import BroadcastViewSet


router = DefaultRouter()

router.register(r"", ContactsViewSet, basename="tenant-contacts")

urlpatterns = [
    path("", include(router.urls)),
]
