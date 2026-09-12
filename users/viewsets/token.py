import logging

from django.db.models import Q
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from tenants.authentication import tenant_from_access_key
from tenants.models import TenantUser
from users.models import User
from users.serializers import JwtUserSerializer

logger = logging.getLogger(__name__)


class CrossTenantJwtUserSerializer(JwtUserSerializer):
    """Mints a token that admits it was issued across a tenant boundary (#301).

    A superuser names any tenant with ``X-ACCESS-KEY`` and, until these claims
    existed, received a token whose claims were identical to one the tenant's
    own owner would get: same ``tenant_id``, same role, nothing to distinguish
    it. Nothing downstream — a log line, an audit trail, a UI banner — could
    tell a borrowed session from a member's.

    Lives here rather than beside JwtUserSerializer because only this endpoint
    can decide a token is borrowed, and a serializer nothing else reaches for
    cannot be picked up by accident. #300 replaces the path outright; these
    claims are what makes the gap visible until it does.
    """

    def get_token(self, user):
        token = super().get_token(user)

        token["cross_tenant"] = True

        # Which tenant the holder actually belongs to, so a reader can see
        # whose session this is and not only whose data it reaches.
        home_tenant = user.tenant
        token["home_tenant_id"] = home_tenant.id if home_tenant else None

        return token


class JwtTokenObtainPairView(TokenObtainPairView):
    serializer_class = JwtUserSerializer
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Obtain JWT token pair with tenant access key",
        request_body=JwtUserSerializer,
        manual_parameters=[
            openapi.Parameter(
                "X-ACCESS-KEY",
                openapi.IN_HEADER,
                description="Tenant access key for authentication",
                type=openapi.TYPE_STRING,
                required=False,
                example="your-tenant-access-key-here",
            )
        ],
        responses={
            200: openapi.Response(
                description="JWT tokens successfully generated",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "access": openapi.Schema(type=openapi.TYPE_STRING, description="JWT Access Token"),
                        "refresh": openapi.Schema(type=openapi.TYPE_STRING, description="JWT Refresh Token"),
                        "user": openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            properties={
                                "id": openapi.Schema(type=openapi.TYPE_INTEGER),
                                "username": openapi.Schema(type=openapi.TYPE_STRING),
                                "email": openapi.Schema(type=openapi.TYPE_STRING),
                            },
                        ),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication failed"),
            400: openapi.Response(description="Bad request - missing required fields"),
        },
        tags=["Authentication"],
    )
    def post(self, request, *args, **kwargs):

        username_or_email = request.data.get("username")
        password = request.data.get("password")

        tenant = tenant_from_access_key(request)

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
        is_member = TenantUser.objects.filter(user=user, tenant=tenant).exists()
        if not is_member and not user.is_superuser:
            raise AuthenticationFailed("User does not belong to this tenant")

        # #301: the tenant is chosen by the caller, in a request header, not
        # derived from the authenticated user — so superuser credentials plus
        # any organisation's access key reach that organisation. The exemption
        # stays for now, because removing it would lock operators out of support
        # work before #300 lands an audited replacement, but it is no longer
        # silent: the warning below is the audit trail, and the serializer marks
        # the token so a borrowed session can be recognised as one.
        #
        # A superuser with no tenant at all is not borrowing anything, so
        # tenant=None stays off this path instead of filling the log with it.
        cross_tenant = not is_member and tenant is not None
        if cross_tenant:
            logger.warning(
                "Cross-tenant token issued (#301): superuser %s (user_id=%s) obtained a token for tenant_id=%s "
                "via X-ACCESS-KEY without a TenantUser membership",
                user.username,
                user.pk,
                tenant.pk,
            )

        # Generate token - pass the actual username for the serializer
        data = request.data.copy()
        data["username"] = user.username  # Use actual username for token generation
        serializer_class = CrossTenantJwtUserSerializer if cross_tenant else self.get_serializer_class()
        serializer = serializer_class(data=data, context={"tenant": tenant})
        serializer.is_valid(raise_exception=True)

        response_data = dict(serializer.validated_data)
        if cross_tenant:
            # Said in the response body too, so a client can show that this
            # session is not the organisation's own (#301).
            response_data["cross_tenant"] = True

        return Response(response_data, status=200)
