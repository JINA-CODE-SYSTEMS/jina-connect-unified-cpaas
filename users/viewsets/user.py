from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from tenants.models import TenantUser
from users.models import User
from users.serializers import UserSafeSerializer, UserSelfSerializer


class UserViewSet(viewsets.ModelViewSet):
    """
    Authenticated-only user endpoint.

    - **list / retrieve** — returns users within the requester's tenant.
      Peer users see only safe fields (id, username, first/last name, image).
      Retrieving your own profile (``/users/user/me/``) returns the full
      self-profile serializer including email, mobile, address, etc.
    - **partial_update** — allowed on your own profile only.
    - **create / destroy** — disabled (registration flow handles user creation).
    """

    queryset = User.objects.all().order_by("-id")
    serializer_class = UserSafeSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch"]

    # ------------------------------------------------------------------
    # Queryset scoping — tenant-only
    # ------------------------------------------------------------------
    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return User.objects.all().order_by("-id")

        tenant = user.tenant
        if not tenant:
            return User.objects.none()

        tenant_user_ids = (
            TenantUser.objects
            .filter(tenant=tenant, is_active=True)
            .values_list("user_id", flat=True)
        )
        return User.objects.filter(id__in=tenant_user_ids).order_by("-id")

    # ------------------------------------------------------------------
    # Serializer selection — safe for peers, full for self
    # ------------------------------------------------------------------
    def get_serializer_class(self):
        if self.action == "retrieve":
            # If retrieving own profile, use self serializer
            if str(self.kwargs.get("pk")) == "me" or (
                self.kwargs.get("pk")
                and str(self.kwargs.get("pk")).isdigit()
                and int(self.kwargs.get("pk")) == self.request.user.pk
            ):
                return UserSelfSerializer
        if self.action == "partial_update":
            return UserSelfSerializer
        return UserSafeSerializer

    # ------------------------------------------------------------------
    # /users/user/me/ shortcut
    # ------------------------------------------------------------------
    def get_object(self):
        if str(self.kwargs.get("pk")) == "me":
            self.kwargs["pk"] = self.request.user.pk
        return super().get_object()

    # ------------------------------------------------------------------
    # Guard: PATCH only on own profile
    # ------------------------------------------------------------------
    def partial_update(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        if str(pk) != "me" and (
            not str(pk).isdigit() or int(pk) != request.user.pk
        ):
            return Response(
                {"detail": "You can only edit your own profile."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().partial_update(request, *args, **kwargs)

    # ------------------------------------------------------------------
    # Disabled actions
    # ------------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "User creation is handled via the registration flow."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )