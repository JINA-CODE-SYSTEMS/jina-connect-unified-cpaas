"""``POST /impersonate/{tenant_id}/`` — view an organisation, read-only (#300).

Two endpoints, both deliberately thin. All the bounds live in
``users.impersonation`` so the same rules apply to a token whoever presents it,
and so nothing here can be the only thing standing between a borrowed token and
a write.

Start (``POST /impersonate/{tenant_id}/``) is called with the operator's own
token and returns a 15-minute read-only access token for the target
organisation — and no refresh token. Chaining is refused for free: starting a
session is a POST, and a POST made with an impersonation token is refused by
``users.impersonation`` before this view is reached.

End (``POST /impersonate/end/``) is called with the impersonation token and
closes the audit row, which also stops the token working. It is the one
non-safe request an impersonated session may make; see
``WRITE_EXEMPT_VIEW_NAMES``.
"""

import logging

from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tenants.models import Tenant
from users.impersonation import (
    IMPERSONATION_TOKEN_LIFETIME,
    SESSION_OVER_MESSAGE,
    issue_impersonation_token,
    live_session_for,
)
from users.models import User

logger = logging.getLogger(__name__)


class IsPlatformSuperUser(BasePermission):
    """Superuser only, checked against the database rather than the token.

    ``CustomJWTAuthentication`` sets ``user.is_superuser`` from the
    ``is_superuser`` claim, and an ordinary access token lives for 90 days. The
    claim is signed, so it cannot be forged — but it can be stale, and a
    revoked platform admin holding last month's token must not be able to mint
    a fresh key to a customer's account. So this asks the database.
    """

    message = "Only platform superusers can view an organisation."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return User.objects.filter(pk=user.pk, is_superuser=True, is_active=True).exists()


class ImpersonationStartView(APIView):
    """POST /impersonate/{tenant_id}/ — issue a read-only session token."""

    permission_classes = [IsAuthenticated, IsPlatformSuperUser]

    @swagger_auto_schema(
        operation_description=(
            "Issue a short-lived, read-only access token scoped to the given organisation. "
            "Superusers only. No refresh token is issued and the token cannot be refreshed; "
            "every non-safe HTTP method is refused while it is in use. Each call writes an "
            "audit row naming the calling user."
        ),
        request_body=no_body,
        responses={
            200: openapi.Response(
                description="Impersonation token issued",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "access": openapi.Schema(type=openapi.TYPE_STRING, description="Read-only access token"),
                        "expires_at": openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
                        "expires_in": openapi.Schema(type=openapi.TYPE_INTEGER, description="Seconds until expiry"),
                        "read_only": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                        "organisation": openapi.Schema(type=openapi.TYPE_OBJECT),
                        "impersonated_by": openapi.Schema(type=openapi.TYPE_OBJECT),
                        "session_id": openapi.Schema(type=openapi.TYPE_INTEGER),
                    },
                ),
            ),
            403: openapi.Response(description="Not a superuser, or already impersonating"),
            404: openapi.Response(description="No such organisation"),
        },
        tags=["Impersonation"],
    )
    def post(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)

        # The actor is the authenticated user, never a value from the request.
        # #301's bypass was exactly this mistake in the other direction: the
        # caller named the organisation *and* nothing named the caller.
        actor = User.objects.get(pk=request.user.pk)
        access, session = issue_impersonation_token(actor, tenant)

        logger.warning(
            "Impersonation session %s started (#300): %s (user_id=%s) viewing tenant_id=%s read-only until %s",
            session.pk,
            actor.username,
            actor.pk,
            tenant.pk,
            session.expires_at.isoformat(),
        )

        return Response(
            {
                # No "refresh" key, by design — there is nothing to refresh.
                "access": access,
                "expires_at": session.expires_at.isoformat(),
                "expires_in": int(IMPERSONATION_TOKEN_LIFETIME.total_seconds()),
                "read_only": True,
                "organisation": {"id": tenant.pk, "name": tenant.name},
                "impersonated_by": {"id": actor.pk, "username": actor.username},
                "session_id": session.pk,
            },
            status=status.HTTP_200_OK,
        )


class ImpersonationEndView(APIView):
    """POST /impersonate/end/ — exit the session and close the audit row.

    Called with the impersonation token itself. Closing the row is what makes
    the banner's exit a real control: the token stops authenticating at once
    instead of merely being dropped by one browser tab.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            "End the impersonation session the presented token belongs to. Call with the "
            "impersonation token. The token stops working immediately and the audit row records "
            "when the session ended. The only non-safe request an impersonated session may make."
        ),
        responses={
            200: openapi.Response(description="Session ended"),
            400: openapi.Response(description="Not an impersonation token"),
            401: openapi.Response(description="Session already ended or expired"),
        },
        tags=["Impersonation"],
    )
    def post(self, request):
        session = live_session_for(getattr(request, "auth", None))
        if session is None:
            # An ordinary token has no session to end; an ended one is already
            # refused by authentication before it reaches here.
            return Response({"detail": SESSION_OVER_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)

        ended_at = session.end()
        logger.info(
            "Impersonation session %s ended (#300): %s had been viewing tenant_id=%s",
            session.pk,
            session.actor_username,
            session.tenant_id,
        )

        return Response(
            {"detail": "Impersonation session ended.", "session_id": session.pk, "ended_at": ended_at.isoformat()},
            status=status.HTTP_200_OK,
        )
