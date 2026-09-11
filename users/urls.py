from django.urls import include, path
from rest_framework.routers import DefaultRouter

from users.viewsets.set_initial_password import SetInitialPasswordViewSet
from users.viewsets.user import UserViewSet
from users.viewsets.user_login_patch import LoginPatchViewSet

router = DefaultRouter()
router.register(r"user", UserViewSet, basename="user")
router.register(r"user-login-patch", LoginPatchViewSet, basename="user-login-patch")
router.register(r"set-initial-password", SetInitialPasswordViewSet, basename="set-initial-password")

urlpatterns = [
    path("", include(router.urls)),
]
