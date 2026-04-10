from django.urls import include, path
from rest_framework.routers import DefaultRouter
from users.viewsets.user import UserViewSet
from users.viewsets.user_login_patch import LoginPatchViewSet

router = DefaultRouter()
router.register(r"user", UserViewSet, basename="user")
router.register(r"user-login-patch", LoginPatchViewSet, basename="user-login-patch")

urlpatterns = [
    path("", include(router.urls)),
]
