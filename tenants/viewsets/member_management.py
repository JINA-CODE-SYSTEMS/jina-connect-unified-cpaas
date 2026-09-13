"""
Member Management ViewSet (RBAC-12 to RBAC-17).

Provides list, add, role-change, remove, and resend-verification endpoints
at /tenants/members/.
Ownership transfer lives on TenantViewSet at /tenants/transfer-ownership/.
"""

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from abstract.viewsets.base import FOREIGN_TENANT_WRITE_MESSAGE, BaseTenantModelViewSet
from tenants.models import Tenant, TenantRole, TenantUser
from tenants.rbac_validators import check_target_priority, get_requester_tenant_user
from tenants.serializers import (
    AddMemberSerializer,
    ChangeRoleSerializer,
    MemberSerializer,
)
from tenants.services.member_service import add_member_to_tenant

# The body field a platform operator uses to name the organisation they are
# acting on. Deliberately the same spelling ``scope_write_to_permitted_tenant``
# already polices on every other write in the project (#346), so an operator
# who has just created an organisation and set its WhatsApp credentials does
# not have to learn a second word for "which organisation" at the last step.
TENANT_FIELD = "tenant"

# What the three write actions say to a caller who is neither a member of the
# organisation in question nor the platform operator. One message because there
# is now one rule: before #356 the same refusal was spelled two different ways
# across three actions, and ``destroy`` did not make it at all.
NOT_AUTHORISED_MESSAGE = "You are not an active member with a role in this organisation."


class MemberManagementViewSet(BaseTenantModelViewSet):
    """
    Manage tenant members: list, add, change role, remove, resend verification.

    Endpoints:
        GET    /tenants/members/                          → list members
        GET    /tenants/members/{id}/                     → retrieve member
        POST   /tenants/members/add/                      → add a member (create user if new)
        PATCH  /tenants/members/{id}/role/                → change a member's role
        DELETE /tenants/members/{id}/                     → soft-remove a member
        POST   /tenants/members/{id}/resend-verification/ → resend verification email
    """

    queryset = TenantUser.objects.select_related("user", "role").all()
    serializer_class = MemberSerializer
    http_method_names = ["get", "post", "patch", "delete"]
    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
        "user__username",
    ]
    required_permissions = {
        "create": "users.invite",
        # Changing a member's role is privilege assignment.
        "partial_update": "users.change_role",
        "list": "users.view",
        "retrieve": "users.view",
        "add_member": "users.invite",
        "change_role": "users.change_role",
        "destroy": "users.remove",
        "resend_verification": "users.invite",
        "default": "users.view",
    }

    # ------------------------------------------------------------------
    # Who may act, and on which organisation (#356)
    #
    # The three write actions below used to answer this three different ways:
    # ``add_member`` and ``change_role`` refused anyone without a ``TenantUser``
    # row, while ``destroy`` carried an ``if not request.user.is_superuser``
    # escape. So a platform operator could remove a member from an organisation
    # they had just created and could not add one — somebody had hit the wall on
    # delete and patched that call site instead of the rule. The helpers here are
    # the rule, asked once per action.
    # ------------------------------------------------------------------

    def _submitted_tenant_id(self, request):
        """The organisation id the request body names, or ``None`` if it names none.

        A body that is not an object at all (a list, a bare string) cannot name
        one either, and says so as ``None`` rather than raising: the serializer's
        own validation describes a malformed body far better than this can.
        """
        data = getattr(request, "data", None)
        if not hasattr(data, "get"):
            return None
        raw = data.get(TENANT_FIELD, None)
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValidationError({TENANT_FIELD: "Must be the id of an organisation."})

    def authority_in(self, request, tenant):
        """The caller's membership in *tenant*, or ``None`` for a platform operator.

        ``None`` is not "nobody" — it is the one caller who is entitled to act
        here while holding no row to point at, and every call site below has to
        say what it does with that. Anyone else who cannot produce an active
        membership *with a role* in this specific organisation is refused here
        and never reaches the write.

        Filtering by *tenant* rather than taking whatever membership the caller
        happens to have is the #352 half of this. Without it, a superuser who is
        an owner of organisation A had their A-role priority compared against a
        member of organisation B — the priority guard was answering a question
        about the wrong organisation, and answering it favourably. It also
        settles the multi-organisation member, whose membership was previously
        resolved by ``.first()``.
        """
        # Asked before the membership lookup because it is the *absence* of a
        # membership that qualifies an operator; looking one up first and
        # falling back would make the fallback the load-bearing branch, which is
        # how ``destroy``'s superuser escape came to sit under a refusal.
        if self.acting_as_platform_operator():
            return None

        requester_tu = get_requester_tenant_user(request, tenant=tenant)
        if not requester_tu or not requester_tu.role:
            raise PermissionDenied(NOT_AUTHORISED_MESSAGE)
        return requester_tu

    def refuse_if_outranked(self, requester_tu, target_role, action_verb):
        """The priority guard, with an explicit answer for the platform operator.

        ``check_target_priority`` compares two role priorities and returns
        ``None`` — meaning "allowed" — whenever either side is missing. A
        platform operator is missing from both sides for the same reason, so
        passing them straight through would produce the right outcome by
        coincidence: indistinguishable, in the code and in review, from having
        forgotten the guard. #356 asks for it said out loud, so this exists to
        say it.

        **The guard does not apply to a platform operator.** It exists so a
        member cannot reach above their own ceiling inside their own
        organisation — an admin demoting the owner who appointed them. An
        operator has no ceiling in this organisation because they have no
        standing in it at all; their authority comes from outside, and it is
        already the authority that created the organisation (#345) and set its
        WhatsApp credentials (#353). Refusing them would be telling the caller
        who is above everyone here that they may not act above themselves.

        What still binds them is ``validate_role_assignment``: OWNER is not
        assignable by anyone through this endpoint, operator included, because
        ownership moves through transfer-ownership and nowhere else.
        """
        if requester_tu is None:
            return None
        return check_target_priority(requester_tu, target_role, action_verb=action_verb)

    def _target_tenant_for_add(self, request):
        """Which organisation ``add_member`` puts the new member into.

        The only one of the three actions that has to be *told*: ``change_role``
        and ``destroy`` read it off the row they are editing, and this one is
        creating that row.

        A platform operator names it and must — there is no membership to fall
        back on, and silently inventing one would be worse than a 400. Everyone
        else gets their own organisation, and naming a different one is refused
        rather than ignored, for the reason ``scope_write_to_permitted_tenant``
        gives at length: quietly substituting their own turns an attempt to
        plant a member in somebody else's organisation into an ordinary success
        in every log and response. Naming their *own* is accepted — agreement is
        not an error, and it is what a client that fills the field in from the
        session will send.
        """
        submitted = self._submitted_tenant_id(request)

        if self.acting_as_platform_operator():
            if submitted is None:
                raise ValidationError(
                    {TENANT_FIELD: "Name the organisation to add this member to — you belong to none yourself."}
                )
            tenant = Tenant.objects.filter(pk=submitted, is_active=True).first()
            if tenant is None:
                # Told apart from a foreign organisation deliberately, unlike
                # #301's deliberately-ambiguous message: an operator can already
                # list every organisation, so there is nothing here to enumerate,
                # and "no such organisation" is the difference between a typo and
                # a permissions problem.
                raise ValidationError({TENANT_FIELD: "No active organisation with that id."})
            return tenant

        requester_tu = get_requester_tenant_user(request)
        if not requester_tu or not requester_tu.role:
            raise PermissionDenied(NOT_AUTHORISED_MESSAGE)
        if submitted is not None and submitted != requester_tu.tenant_id:
            raise PermissionDenied(FOREIGN_TENANT_WRITE_MESSAGE)
        return requester_tu.tenant

    # ------------------------------------------------------------------
    # Block unintended ModelViewSet actions (create, partial_update)
    # ------------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        """Use /tenants/members/add/ instead."""
        return Response(
            {"detail": "Use POST /tenants/members/add/ to add members."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def partial_update(self, request, *args, **kwargs):
        """Use /tenants/members/{id}/role/ instead."""
        return Response(
            {"detail": "Use PATCH /tenants/members/{id}/role/ to change roles."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # Add Member (RBAC-13)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["post"], url_path="add", url_name="add-member")
    def add_member(self, request):
        """
        Add a member to this tenant.

        Two paths (per PRD §4.1.4):
        1. Email belongs to an existing user → create TenantUser (201).
        2. No account → create User (is_active=False) + TenantUser +
           EmailVerificationToken + send verification email (201).

        Request:  { "email": "bob@example.com", "password": "Str0ng!Pass",
                    "first_name": "Bob", "last_name": "Smith", "role_id": 5 }

        A platform operator adds ``"tenant": <id>`` to say which organisation —
        the last step of the onboarding workflow #345 exists for, and the step
        that used to send them to Django admin (#356).
        """
        tenant = self._target_tenant_for_add(request)
        operator = self.acting_as_platform_operator()

        serializer = AddMemberSerializer(
            data=request.data,
            # The tenant settled above, not the caller's own: for an operator
            # there is no "own", and for a member the two are the same by
            # construction. ``role_id`` is then validated inside the
            # organisation the member will actually land in, which is the pairing
            # #356 found broken here.
            context={"request": request, "tenant": tenant, "platform_operator": operator},
        )
        serializer.is_valid(raise_exception=True)

        role = TenantRole.objects.get(
            id=serializer.validated_data["role_id"],
            tenant=tenant,
        )

        try:
            tenant_user, is_new_user = add_member_to_tenant(
                tenant=tenant,
                email=serializer.validated_data["email"],
                role=role,
                password=serializer.validated_data.get("password"),
                first_name=serializer.validated_data.get("first_name"),
                last_name=serializer.validated_data.get("last_name", ""),
                created_by=request.user,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        data = MemberSerializer(TenantUser.objects.select_related("user", "role").get(pk=tenant_user.pk)).data

        if is_new_user:
            data["message"] = "User created. Verification email sent — user must verify before logging in."
        else:
            data["message"] = "Existing user added to tenant."

        return Response(data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Change Role (RBAC-15)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["patch"], url_path="role", url_name="change-role")
    def change_role(self, request, pk=None):
        """
        Change a member's role.

        Request:  { "role_id": 7 }

        The organisation is the one the member being edited belongs to. Nothing
        in the body chooses it, so a platform operator needs no ``tenant`` here —
        the row they addressed already said which organisation they meant (#356).
        """
        tenant_user = self.get_object()

        # Cannot change the OWNER's role (must use transfer-ownership)
        if tenant_user.role and tenant_user.role.slug == "owner":
            return Response(
                {"detail": "Cannot change the OWNER's role. Use transfer-ownership instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A member of this organisation, or the platform operator. ``None`` here
        # means the operator, and is what the two guards below are written around.
        requester_tu = self.authority_in(request, tenant_user.tenant)

        # Cannot change your own role
        if tenant_user.user == request.user:
            return Response(
                {"detail": "You cannot change your own role."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cannot change role of someone with >= your priority — except for an
        # operator, who has no priority in this organisation to compare. See
        # ``refuse_if_outranked``.
        err = self.refuse_if_outranked(requester_tu, tenant_user.role, action_verb="change the role of a member with")
        if err:
            return Response({"detail": err}, status=status.HTTP_403_FORBIDDEN)

        serializer = ChangeRoleSerializer(
            data=request.data,
            # The member's organisation, which is also the one the new role must
            # come from four lines below. Reading the caller's own here instead
            # meant validating the assignment against one organisation and
            # performing it in another.
            context={
                "request": request,
                "tenant": tenant_user.tenant,
                "platform_operator": requester_tu is None,
            },
        )
        serializer.is_valid(raise_exception=True)

        role = TenantRole.objects.get(
            id=serializer.validated_data["role_id"],
            tenant=tenant_user.tenant,
        )
        tenant_user.role = role
        tenant_user.updated_by = request.user
        tenant_user.save(update_fields=["role", "updated_by", "updated_at"])

        return Response(MemberSerializer(tenant_user).data)

    # ------------------------------------------------------------------
    # Remove (RBAC-16) — soft delete
    # ------------------------------------------------------------------
    def destroy(self, request, *args, **kwargs):
        """
        Remove a member from the tenant (soft delete — sets is_active=False).
        Cannot remove the OWNER. Cannot remove yourself.

        This action already let a platform operator through, via a bare
        ``if not request.user.is_superuser`` bolted under the membership
        refusal. #356 replaces it with the same ``authority_in`` the other two
        now ask, which is narrower in one respect that matters: a superuser who
        *is* a member of some other organisation used to be waved past this
        guard with their own organisation's priority compared against a member
        of this one. That is #352's refusal, and it belongs here too.
        """
        tenant_user = self.get_object()

        # Cannot remove the OWNER
        if tenant_user.role and tenant_user.role.slug == "owner":
            return Response(
                {"detail": "Cannot remove the OWNER. Use transfer-ownership first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cannot remove yourself
        if tenant_user.user == request.user:
            return Response(
                {"detail": "You cannot remove yourself from the tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A member of this organisation, or the platform operator (``None``).
        requester_tu = self.authority_in(request, tenant_user.tenant)

        err = self.refuse_if_outranked(requester_tu, tenant_user.role, action_verb="remove a member with")
        if err:
            return Response({"detail": err}, status=status.HTTP_403_FORBIDDEN)

        # Soft delete
        tenant_user.is_active = False
        tenant_user.updated_by = request.user
        tenant_user.save(update_fields=["is_active", "updated_by", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Resend Verification (RBAC-17)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="resend-verification", url_name="resend-verification")
    def resend_verification(self, request, pk=None):
        """
        Resend the email verification for a member whose email is still unverified.
        """
        tenant_user = self.get_object()
        user = tenant_user.user

        # Must be an unverified user (is_active=False means email not yet verified)
        if user.is_active:
            return Response(
                {"detail": "This member's email is already verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from users.models import EmailVerificationToken
        from users.services.email_verification import EmailVerificationService

        # Invalidate old tokens and create a new one
        token = EmailVerificationToken.create_for_user(user)

        try:
            EmailVerificationService.send_verification_email(user, token)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Failed to send verification email to %s",
                user.email,
            )
            return Response(
                {"detail": "Verification token created but email sending failed. Try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"detail": "Verification email resent.", "email": user.email},
            status=status.HTTP_200_OK,
        )
