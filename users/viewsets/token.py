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


class CrossTenantJwtUserSerializer(JwtUserSerializer):
    """Marked a token as issued across a tenant boundary (#301). Now unused.

    #327 removed the superuser exemption this served: ``/token/`` no longer
    issues a token for an organisation the user does not belong to, so nothing
    selects this serializer and no token carries ``cross_tenant`` or
    ``home_tenant_id`` any more.

    **Kept deliberately rather than deleted.** Nothing in this repository reads
    either claim — the only readers were the view below and its own test, both
    changed in #327 — but the web client is a separate repository that could
    not be checked from here, and a frontend that branches on a claim is not
    visible from the backend. Retaining an unreachable writer costs nothing;
    guessing wrong about a deployed client does not.

    Safe to delete once someone has confirmed ``jina-connect-web`` does not
    read ``cross_tenant`` or ``home_tenant_id``. Note that deletion cannot
    change the shape of any token that is still issued: the only tokens that
    ever carried these claims are the borrowed ones #327 refuses outright.
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
                description=(
                    "Access key naming which of the caller's own organisations to scope the token to. "
                    "The caller must belong to it: a key for an organisation they are not a member of "
                    "is refused, superuser or not (#327). To view another organisation, use "
                    "POST /impersonate/{tenant_id}/, which is read-only, time-boxed and audited."
                ),
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

        # #327: a token is only ever issued for an organisation the user belongs
        # to. Until now a superuser was exempt from this check outright, and the
        # tenant is chosen by the caller in the X-ACCESS-KEY header rather than
        # derived from the authenticated user — so superuser credentials plus any
        # organisation's access key produced a token indistinguishable from that
        # organisation's own owner's, unbounded and with full write access.
        #
        # #301 kept the exemption and made it loud, because there was no other
        # way to do support work. There is now: /impersonate/ (#300) is
        # read-only, expires in 15 minutes, cannot be refreshed, and is refused
        # unless its audit row is live — and it needs nothing from the customer,
        # where this path needed their access key.
        #
        # The one case that is not a crossing is a superuser with no membership
        # anywhere, which is what `createsuperuser` leaves behind. Its token
        # names no organisation at all (`tenant_id: None`), so it reaches no
        # customer's data; refusing it would lock a fresh platform admin out of
        # /token/ and so out of /impersonate/, which is started with their own
        # token. Non-superusers in that state are refused exactly as before.
        tenantless_superuser = user.is_superuser and tenant is None
        if not is_member and not tenantless_superuser:
            raise AuthenticationFailed("User does not belong to this tenant")

        # Generate token - pass the actual username for the serializer
        data = request.data.copy()
        data["username"] = user.username  # Use actual username for token generation
        serializer = self.get_serializer_class()(data=data, context={"tenant": tenant})
        serializer.is_valid(raise_exception=True)

        return Response(dict(serializer.validated_data), status=200)
