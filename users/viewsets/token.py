from django.conf import settings
from django.db.models import Q
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from tenants.authentication import TenantAccessKeyAuthentication
from tenants.models import TenantUser
from users.models import User
from users.serializers import JwtUserSerializer


class JwtTokenObtainPairView(TokenObtainPairView):
    serializer_class = JwtUserSerializer
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Obtain JWT token pair with tenant access key",
        request_body=JwtUserSerializer,
        manual_parameters=[
            openapi.Parameter(
                'X-ACCESS-KEY',
                openapi.IN_HEADER,
                description="Tenant access key for authentication",
                type=openapi.TYPE_STRING,
                required=False,
                example="your-tenant-access-key-here"
            )
        ],
        responses={
            200: openapi.Response(
                description="JWT tokens successfully generated",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'access': openapi.Schema(type=openapi.TYPE_STRING, description='JWT Access Token'),
                        'refresh': openapi.Schema(type=openapi.TYPE_STRING, description='JWT Refresh Token'),
                        'user': openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            properties={
                                'id': openapi.Schema(type=openapi.TYPE_INTEGER),
                                'username': openapi.Schema(type=openapi.TYPE_STRING),
                                'email': openapi.Schema(type=openapi.TYPE_STRING),
                            }
                        )
                    }
                )
            ),
            401: openapi.Response(description="Authentication failed"),
            400: openapi.Response(description="Bad request - missing required fields")
        },
        tags=['Authentication']
    )
    def post(self, request, *args, **kwargs):
        

        username_or_email = request.data.get("username")
        password = request.data.get("password")

        _, tenant = TenantAccessKeyAuthentication().authenticate(request)

        
        # User check - try username first, then email
        try:
            user = User.objects.get(Q(username=username_or_email) | Q(email__iexact=username_or_email))
        except User.DoesNotExist:
            raise AuthenticationFailed("Invalid username/email or password")
        except User.MultipleObjectsReturned:
            # If multiple users found (edge case), try exact username match first
            try:
                user = User.objects.get(username=username_or_email)
            except User.DoesNotExist:
                user = User.objects.filter(email__iexact=username_or_email).first()

        if not user.check_password(password):
            raise AuthenticationFailed("Invalid username/email or password")
        
        # Check if user is active (email verified)
        if not user.is_active:
            raise AuthenticationFailed("Please verify your email before logging in.")

        if tenant is None:
            tenant = user.tenant
        # TenantUser mapping check
        if not TenantUser.objects.filter(user=user, tenant=tenant).exists():
            if user.is_superuser:
                pass
            else:
                raise AuthenticationFailed("User does not belong to this tenant")

        # Generate token - pass the actual username for the serializer
        data = request.data.copy()
        data['username'] = user.username  # Use actual username for token generation
        serializer = self.get_serializer(data=data, context={"tenant": tenant})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=200)
