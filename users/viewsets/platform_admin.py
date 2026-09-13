"""List, invite and revoke platform administrators (#358).

    GET    /users/platform-admins/          → who administers the platform
    POST   /users/platform-admins/invite/   → grant it to an email address
    DELETE /users/platform-admins/{user_id}/ → take it away again

────────────────────────────────────────────────────────────────────────────
The decision this file deliberately did **not** make
────────────────────────────────────────────────────────────────────────────

#358 asks whether ``is_superuser`` is the right grain for platform access, or
whether the platform needs roles of its own the way an organisation has
``TenantRole.priority`` and ``RolePermission``. A support engineer who should
read every organisation and impersonate is genuinely not the same person as one
who should move wallet balances or hand out platform access.

**That question is deferred, on purpose, and this ships the flat version.** The
reasoning, so the next person knows it was a choice and not an oversight:

* Granting maximum privilege is *already* what happens today. The only ways to
  make a platform administrator are ``manage.py createsuperuser`` and the Django
  admin, and both hand over everything with no record of who did it. A flat
  version of this endpoint does not make access worse — it makes the same grant
  **visible, audited and revocable**, which is strictly better than the status
  quo it replaces. Refusing to ship until the role model exists leaves the shell
  as the only way, which is the actual harm.
* It does not foreclose the roles. The surface here — list, invite, revoke —
  survives a platform role model unchanged; what such a model adds is a
  ``role`` column on the list and a ``role`` field on the invite. Nothing here
  has to be undone to get there, and the audit table (``PlatformAdminChange``)
  is already an event log rather than a boolean, so it can record a role change
  the day one exists.

What is *not* deferred, because deferring it would be shipping the thing the
ticket warns against — "an invite button that silently grants the maximum and
calls it done" — is the audit trail and the revoke. Both are here.

The known cost of the flat grain, stated rather than hidden: an administrator's
rights are all-or-nothing, so an invite made to let somebody read support
tickets also lets them write every customer's data. Until platform roles exist,
that is what the button means, and the list exists so somebody can see who it
was handed to.

────────────────────────────────────────────────────────────────────────────
Who may use it
────────────────────────────────────────────────────────────────────────────

Only a platform administrator, asked through
``BaseModelViewSet.acting_as_platform_operator`` — the single definition #353
and #356 were both created by re-deriving. No tenant role reaches this at any
priority: ``TenantRolePermission`` is not in ``permission_classes`` at all, so
there is no permission key an organisation's owner could be granted that would
open it.

Two consequences of reusing that helper, both deliberate and both pinned by
tests:

* **An impersonated session is refused, including the read.** A borrowed token
  keeps ``is_superuser`` true and holds no membership — the exact shape of a
  platform operator — and #300 only refuses *writes*. So ``GET`` here would have
  been reachable from a customer-facing read-only session and is not, because
  ``acting_as_platform_operator`` answers False while impersonating.
* **A superuser who is a member of an organisation is refused too.** #352's
  rule: holding a membership means acting as that organisation's user. It means
  a platform operator who added themselves to a customer's organisation to debug
  it must remove themselves again before they can grant platform access. That is
  the same trade the WhatsApp credential endpoint (#353) and the member endpoint
  (#356) already make, and having this one disagree is precisely the drift those
  two tickets were.
"""

import logging

from django.db import transaction
from django.db.models import OuterRef, Subquery
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from abstract.viewsets.base import BaseModelViewSet
from users.models import PlatformAdminChange, User
from users.serializers import (
    InvitePlatformAdministratorSerializer,
    PlatformAdministratorSerializer,
)
from users.services.account_provisioning import (
    create_pending_user,
    find_user_by_email,
    send_account_verification,
)

logger = logging.getLogger(__name__)

NOT_A_PLATFORM_ADMINISTRATOR_MESSAGE = "Only a platform administrator may view or change platform administration."

# Refusing the last one is not a courtesy. ``is_superuser`` is the only key to
# the Django admin and to every platform-operator workflow, so an empty set of
# administrators cannot be repaired through any endpoint in this project — the
# recovery is a shell on the production host and ``manage.py createsuperuser``.
# The ticket asks for it as "nobody may revoke themselves if they are the last
# one"; it is enforced for *any* last one, because revoking the last
# administrator locks everybody out whether or not the caller is that person.
LAST_ADMINISTRATOR_MESSAGE = (
    "This is the last platform administrator. Revoking it would leave the platform with none, "
    "which cannot be undone through the API. Grant platform administration to someone else first."
)

ALREADY_AN_ADMINISTRATOR_MESSAGE = "This user is already a platform administrator."


class IsPlatformAdministrator(BasePermission):
    """A platform administrator, still one *now*, and not impersonating.

    Two questions, asked of two different sources, and both are needed:

    * ``view.acting_as_platform_operator()`` — the shared definition (#353,
      #356). It is what rules out an impersonated session and a superuser who
      holds an organisation membership. Never re-derived here as
      ``request.user.is_superuser``: that inline test is the mistake both of
      those tickets were.
    * A database read of ``is_superuser`` and ``is_active``. This is not a
      second opinion about the same thing — it is a *freshness* check, and #358
      is the ticket that makes it load-bearing. ``CustomJWTAuthentication``
      stamps ``user.is_superuser`` from the token claim, and an ordinary access
      token lives ninety days. Without this read, the very first thing a
      revoked administrator could do with last month's token is grant
      themselves back. ``ImpersonationStartView.IsPlatformSuperUser`` already
      asks the database for the same reason.

    The wider version of that gap is out of #358's scope and worth knowing
    about: ``TenantRolePermission`` reads ``is_superuser`` off the claim too, so
    a revoked administrator keeps superuser reach elsewhere in the API until
    their token expires. Revocation is immediate *here*, so it cannot be undone
    by its own subject; making it immediate everywhere is token invalidation,
    which is a different change.
    """

    message = NOT_A_PLATFORM_ADMINISTRATOR_MESSAGE

    def has_permission(self, request, view):
        if not view.acting_as_platform_operator():
            return False
        return User.objects.filter(pk=request.user.pk, is_superuser=True, is_active=True).exists()


class PlatformAdminViewSet(BaseModelViewSet):
    """The platform's own administrator list — see the module docstring.

    The queryset is ``User`` rather than the audit table because
    ``is_superuser`` is the authority on who is an administrator; the audit
    table says how they became one. So ``{id}`` in the detail route is a **user
    id**, which is also what the list returns as ``id``, and revoking somebody
    is ``DELETE`` on the same id the list handed out.
    """

    queryset = User.objects.filter(is_superuser=True)
    serializer_class = PlatformAdministratorSerializer
    permission_classes = [IsAuthenticated, IsPlatformAdministrator]
    # No PUT and no PATCH: there is nothing about an administrator to edit
    # while the grain is flat. The day platform roles exist, the role change
    # arrives as PATCH here.
    http_method_names = ["get", "post", "delete"]
    # Oldest first, unlike the project default of ``-id``: the administrator
    # list is short and read as a roster, and a roster whose top entry changes
    # every time somebody is invited is harder to scan than a stable one.
    ordering = ["id"]
    search_fields = ["email", "username", "first_name", "last_name"]

    def get_queryset(self):
        """Every administrator, each carrying the grant that explains them.

        Annotated with the *most recent* ``granted`` event rather than the first
        one: rights get taken away and given back, and "granted by" should name
        the grant that is currently in force, not a historical one that was
        already revoked.

        Subqueries rather than a join or a per-row lookup, so the answer stays
        one query whatever the page size — and so an administrator with **no**
        grant row (every one made with ``createsuperuser``) still appears, with
        nulls, instead of being dropped by an inner join.
        """
        latest_grant = PlatformAdminChange.objects.filter(
            subject=OuterRef("pk"),
            action=PlatformAdminChange.ACTION_GRANTED,
        ).order_by("-changed_at", "-id")

        return (
            User.objects.filter(is_superuser=True)
            .annotate(
                granted_at=Subquery(latest_grant.values("changed_at")[:1]),
                granted_by_user_id=Subquery(latest_grant.values("actor_id")[:1]),
                granted_by_username=Subquery(latest_grant.values("actor_username")[:1]),
            )
            .order_by("id")
        )

    def _acting_administrator(self):
        """The caller as a database row, for the audit record.

        Read fresh rather than using ``request.user`` directly because
        ``CustomJWTAuthentication`` mutates that object's ``is_superuser`` and
        ``tenant_id`` from token claims. The audit trail should name the account,
        not a request-scoped copy of it, and ``ImpersonationStartView`` reads the
        actor the same way for the same reason.
        """
        return User.objects.get(pk=self.request.user.pk)

    def _serialized(self, user):
        """One administrator, rendered exactly as the list renders them.

        Re-fetched through ``get_queryset`` rather than serialized from the
        in-memory instance so that ``granted_at`` and ``granted_by`` are the
        annotations, and so an invite's response and a subsequent list of the
        same person cannot describe them differently.
        """
        return PlatformAdministratorSerializer(self.get_queryset().get(pk=user.pk)).data

    # ------------------------------------------------------------------
    # Invite (grant)
    # ------------------------------------------------------------------
    @swagger_auto_schema(
        operation_description=(
            "Grant platform administration to an email address. If no account holds that address, "
            "one is created unverified and sent a verification email — the same path "
            "POST /tenants/members/add/ uses. Platform administrators only. Writes an audit row."
        ),
        request_body=InvitePlatformAdministratorSerializer,
        responses={
            201: PlatformAdministratorSerializer,
            400: openapi.Response(description="Missing password or first name for a new account"),
            403: openapi.Response(description="Not a platform administrator, or impersonating"),
            409: openapi.Response(description="Already a platform administrator"),
        },
        tags=["Platform Administration"],
    )
    @action(detail=False, methods=["post"], url_path="invite", url_name="invite")
    def invite(self, request):
        """Grant platform administration to an address, creating the account if new.

        Two branches, and they are ``add_member``'s two branches on purpose —
        the account-and-verification path is shared code, not a copy (see
        ``users.services.account_provisioning``). The ticket asked for reuse
        specifically so a second invite path could not drift from the first.

        A newly created account is ``is_active=False`` until the holder clicks
        the verification link, and an inactive account cannot obtain a token at
        all. So the grant lands immediately but is **inert** until the address is
        proven — which is the right order: nothing is waiting on a human to
        remember a second step, and nothing is usable before the invitee proves
        they are the invitee. It is also why such an account does not count
        towards the last-administrator rule below.
        """
        serializer = InvitePlatformAdministratorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        actor = self._acting_administrator()

        with transaction.atomic():
            user = find_user_by_email(email)
            created = user is None

            if created:
                try:
                    user = create_pending_user(
                        email=email,
                        password=serializer.validated_data.get("password"),
                        first_name=serializer.validated_data.get("first_name"),
                        last_name=serializer.validated_data.get("last_name", ""),
                    )
                except ValueError as exc:
                    # The serializer already refuses both of these, so reaching
                    # here means a caller found a way past it — answer with the
                    # service's own message rather than a 500.
                    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            elif user.is_superuser:
                # 409 rather than a silent 201: the response to "make this person
                # an administrator" must not look identical whether or not this
                # call is what made them one. ``add_member`` answers a repeated
                # invite the same way.
                return Response({"detail": ALREADY_AN_ADMINISTRATOR_MESSAGE}, status=status.HTTP_409_CONFLICT)

            user.is_superuser = True
            user.save(update_fields=["is_superuser"])

            PlatformAdminChange.record(
                action=PlatformAdminChange.ACTION_GRANTED,
                actor=actor,
                subject=user,
            )

        # After the commit, not inside it: an email promising somebody platform
        # access must not go out for a grant that then rolled back. Failure to
        # send is swallowed and logged — the token exists either way and
        # /users/set-initial-password/ and the resend paths still work.
        if created:
            send_account_verification(user)

        # WARNING, matching the impersonation start log: this is the most
        # consequential thing anyone can do through this API, and the audit row
        # should not be the only place it shows up when somebody is reading logs
        # after the fact.
        logger.warning(
            "Platform administration GRANTED (#358): %s (user_id=%s) granted it to %s (user_id=%s, new_account=%s)",
            actor.username,
            actor.pk,
            user.username,
            user.pk,
            created,
        )

        data = self._serialized(user)
        data["message"] = (
            "Account created. Verification email sent — the invitee must verify before the grant can be used."
            if created
            else "Existing user granted platform administration."
        )
        return Response(data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Revoke
    # ------------------------------------------------------------------
    @swagger_auto_schema(
        operation_description=(
            "Revoke platform administration from a user. The user account and every organisation "
            "membership it holds are left untouched — only the platform grant is removed. "
            "Refused when it would leave the platform with no usable administrator. Writes an audit row."
        ),
        responses={
            200: openapi.Response(description="Revoked"),
            400: openapi.Response(description="This is the last platform administrator"),
            403: openapi.Response(description="Not a platform administrator, or impersonating"),
            404: openapi.Response(description="No such platform administrator"),
        },
        tags=["Platform Administration"],
    )
    def destroy(self, request, *args, **kwargs):
        """Take platform administration away, and nothing else.

        **What this must not do**, spelled out because the route is a ``DELETE``
        on something addressed by user id and the obvious reading of that is
        wrong: it does not delete the ``User`` row and it does not touch a single
        ``TenantUser``. Platform administration and organisation membership are
        independent — the person losing platform rights may still be an owner of
        their own organisation tomorrow, and deleting their account to revoke a
        flag would destroy data belonging to a customer.

        Answered ``200`` with a body rather than ``204`` for the same reason:
        ``204 No Content`` on a ``DELETE`` reads as "that thing is gone", and the
        thing addressed here — the person — is still very much there.

        The last-administrator refusal counts only administrators who are
        ``is_active``. An inactive account cannot obtain a token
        (``SimpleJWT`` refuses one) so leaving it as the sole administrator
        would be the same lockout as leaving none, arrived at through a
        loophole. The count is taken under ``select_for_update`` so two
        concurrent revocations cannot each see the other as the survivor and
        both proceed.
        """
        with transaction.atomic():
            administrator = self.get_object()

            # Locks the administrator rows for the duration, so a second revoke
            # racing this one waits and then counts a set this one has already
            # changed.
            usable_administrators = list(
                User.objects.select_for_update().filter(is_superuser=True, is_active=True).values_list("pk", flat=True)
            )
            if not [pk for pk in usable_administrators if pk != administrator.pk]:
                return Response({"detail": LAST_ADMINISTRATOR_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)

            actor = self._acting_administrator()

            administrator.is_superuser = False
            administrator.save(update_fields=["is_superuser"])

            change = PlatformAdminChange.record(
                action=PlatformAdminChange.ACTION_REVOKED,
                actor=actor,
                subject=administrator,
            )

        logger.warning(
            "Platform administration REVOKED (#358): %s (user_id=%s) revoked it from %s (user_id=%s)",
            actor.username,
            actor.pk,
            administrator.username,
            administrator.pk,
        )

        return Response(
            {
                "detail": "Platform administration revoked. The user account and its organisation memberships are unchanged.",
                "id": administrator.pk,
                "revoked_at": change.changed_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )
