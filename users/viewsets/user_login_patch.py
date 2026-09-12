from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.exceptions import NotAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from tenants.authentication import TenantAccessKeyAuthentication
from users.serializers import LoginPatchUserSerializer


class LoginPatchViewSet(viewsets.ViewSet):
    """Create-or-fetch a user inside the tenant that owns the access key.

    The access key is the only credential here, which makes it more than a
    read capability: whoever holds one can provision an account in that
    organisation and have it join as an AGENT. #301 hashed the keys at rest and
    gave them revocation for exactly this reason; the endpoint itself still
    wants replacing with an invited, tenant-authorised flow before it can be
    called safe, since the key is a single shared secret with no per-account
    authorisation behind it.
    """

    authentication_classes = [TenantAccessKeyAuthentication]
    permission_classes = [AllowAny]
    serializer_class = LoginPatchUserSerializer

    access_key_param = openapi.Parameter(
        "X-ACCESS-KEY",  # header name
        in_=openapi.IN_HEADER,
        description="Tenant access key",
        type=openapi.TYPE_STRING,
        required=True,
    )

    @swagger_auto_schema(
        request_body=LoginPatchUserSerializer,
        manual_parameters=[access_key_param],
        responses={201: LoginPatchUserSerializer},
        operation_description="Create or get a user associated with the tenant identified by the access key.",
    )
    def create(self, request, *args, **kwargs):
        if request.auth is None:
            # AllowAny plus an optional access key meant a request with no
            # header at all reached the serializer with tenant=None, which then
            # created the user and died on TenantUser(tenant=None) — a 500 that
            # left a tenant-less account behind (#301). The key is this
            # endpoint's only credential, so its absence is a 401.
            raise NotAuthenticated("A tenant access key is required.")

        serializer = self.serializer_class(
            data=request.data,
            context={"tenant": request.auth},
        )
        serializer.is_valid(raise_exception=True)
        user, created = serializer.save()
        status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK

        return Response(serializer.to_representation((user, created)), status=status_code)
