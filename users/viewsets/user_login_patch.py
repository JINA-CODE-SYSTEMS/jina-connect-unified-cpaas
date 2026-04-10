from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from tenants.authentication import TenantAccessKeyAuthentication
from users.serializers import LoginPatchUserSerializer


class LoginPatchViewSet(viewsets.ViewSet):
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
        operation_description="Create or get a user associated with the tenant identified by the access key."
    )
    def create(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data,
            context={"tenant": request.auth},
        )
        serializer.is_valid(raise_exception=True)
        user, created = serializer.save()
        status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK

        return Response(serializer.to_representation((user, created)), status=status_code)
