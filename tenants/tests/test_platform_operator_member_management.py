"""A platform operator can put the first human into an organisation (#356).

#353 in a different endpoint, and the last step of the onboarding workflow #345
exists for. ``MemberManagementViewSet`` resolved the caller's own ``TenantUser``
and refused when there wasn't one — then, past the refusal, read the target
organisation off that same row, so there was no way to name a different one
even in principle. A platform superuser holds no ``TenantUser``, so creating the
organisation worked, setting its WhatsApp credentials worked, and adding its
first member sent the operator to Django admin.

The tell was that ``destroy`` already carried an ``if not request.user.is_superuser``
escape and the other two did not: **an operator could remove a member and not
add one.** Somebody hit the wall on delete and patched that call site rather
than the rule. So this file asserts the three actions agree as much as it
asserts that any of them works.

**Consequences, not branches.** Every test goes through the real URL with a real
role and then reads the ``TenantUser`` row back out of the database. Asserting
"``acting_as_platform_operator`` returned True" would pass against a fix that
identified the operator correctly and then wrote the member into the wrong
organisation — which is the specific failure this endpoint was already capable
of, since the organisation used to come from the caller rather than the request.

The one exception is the last test, which reaches past the URL on purpose and
says why.

No network and no email: ``add_member`` only sends a verification mail on the
new-user path, and ``EmailVerificationService`` failing is caught and logged by
``add_member_to_tenant`` either way, so no test here depends on it.

HOW TO RUN:
    DB_NAME=... python -m pytest tenants/tests/test_platform_operator_member_management.py -v
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from users.impersonation import issue_impersonation_token

User = get_user_model()

_mobile_seq = itertools.count(1)

MEMBERS_URL = "/tenants/members/"
ADD_URL = f"{MEMBERS_URL}add/"


def _role_url(tenant_user_pk) -> str:
    return f"{MEMBERS_URL}{tenant_user_pk}/role/"


def _detail_url(tenant_user_pk) -> str:
    return f"{MEMBERS_URL}{tenant_user_pk}/"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _tenant(tag: str = "org"):
    """An organisation. Creating one also creates its five default roles."""
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{tag}-{uuid.uuid4().hex[:6]}", is_active=True)


def _user(**kwargs):
    return User.objects.create_user(
        username=f"memop_{uuid.uuid4().hex[:8]}",
        email=f"memop_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190008{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test credential
        **kwargs,
    )


def _role(tenant, slug: str):
    from tenants.models import TenantRole

    return TenantRole.objects.get(tenant=tenant, slug=slug)


def _member(tenant, user, role_slug: str = "owner"):
    from tenants.models import TenantUser

    return TenantUser.objects.create(tenant=tenant, user=user, role=_role(tenant, role_slug))


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _impersonating(actor, tenant):
    raw, _session = issue_impersonation_token(actor, tenant)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return api


def _new_member_payload(tenant_id=None, *, role_id, email=None):
    """The new-user path: an email nobody holds yet, so a member is created."""
    payload = {
        "email": email or f"newhire_{uuid.uuid4().hex[:8]}@test.com",
        "password": "Str0ng!Pass",
        "first_name": "New",
        "last_name": "Hire",
        "role_id": role_id,
    }
    if tenant_id is not None:
        payload["tenant"] = tenant_id
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 1. The defect, asserted as its consequence
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_platform_operator_adds_the_first_member_to_an_organisation_they_are_not_in():
    """#356 itself: the step that used to require Django admin.

    Before the fix this was 403 "You are not an active member of any tenant."
    The organisation on the created row is asserted, not just the 201: the
    target organisation used to be read off the caller's own membership, so a
    fix that admitted the operator without also letting the body name an
    organisation would have had nowhere to put the member.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    admin_role = _role(org, "admin")
    payload = _new_member_payload(org.id, role_id=admin_role.pk)

    response = _api(operator).post(ADD_URL, payload, format="json")

    assert response.status_code == 201, response.data

    tenant_user = TenantUser.objects.select_related("user", "role", "tenant").get(pk=response.data["id"])
    assert tenant_user.tenant_id == org.id
    assert tenant_user.user.email == payload["email"]
    assert tenant_user.role_id == admin_role.pk
    assert tenant_user.is_active is True
    # The new-user path leaves the account pending email verification. An
    # operator-created member is not a verified one.
    assert tenant_user.user.is_active is False


@pytest.mark.django_db
def test_a_platform_operator_adds_an_existing_user_to_a_named_organisation():
    """The other half of ``add_member``: an account that already exists.

    Worth its own test because it takes a different branch of
    ``add_member_to_tenant`` — no ``User`` is created and no verification token
    is issued — and because moving a person between organisations is the common
    operator errand.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    existing = _user()
    operator = _user(is_superuser=True, is_staff=True)
    manager_role = _role(org, "manager")

    response = _api(operator).post(
        ADD_URL,
        {"email": existing.email, "role_id": manager_role.pk, "tenant": org.id},
        format="json",
    )

    assert response.status_code == 201, response.data
    tenant_user = TenantUser.objects.get(tenant=org, user=existing)
    assert tenant_user.role_id == manager_role.pk
    assert tenant_user.is_active is True


@pytest.mark.django_db
def test_a_platform_operator_changes_a_member_role_in_an_organisation_they_are_not_in():
    """The second of the three actions, and the one with the priority guard.

    An operator has no role priority in this organisation, so the guard that
    stops a member promoting someone above themselves has nothing to compare
    against. ``refuse_if_outranked`` answers that explicitly rather than letting
    ``check_target_priority`` return None for a missing requester, and this is
    the consequence of that answer: the role on the row actually changes.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    member = _member(org, _user(), "viewer")
    manager_role = _role(org, "manager")

    response = _api(operator).patch(_role_url(member.pk), {"role_id": manager_role.pk}, format="json")

    assert response.status_code == 200, response.data
    assert TenantUser.objects.get(pk=member.pk).role_id == manager_role.pk


@pytest.mark.django_db
def test_a_platform_operator_promotes_a_member_above_every_existing_member():
    """Specifically the promotion the priority guard would refuse a member.

    The organisation's own admin cannot make somebody an admin — equal priority.
    The operator can, because their authority is not a priority in this
    organisation at all. This is the assertion that ``refuse_if_outranked``'s
    documented answer is doing something rather than describing an accident.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    member = _member(org, _user(), "agent")
    admin_role = _role(org, "admin")

    response = _api(operator).patch(_role_url(member.pk), {"role_id": admin_role.pk}, format="json")

    assert response.status_code == 200, response.data
    assert TenantUser.objects.get(pk=member.pk).role_id == admin_role.pk


@pytest.mark.django_db
def test_the_three_write_actions_agree_that_the_operator_may_act():
    """The acceptance criterion that is about consistency rather than capability.

    ``destroy`` always allowed this, through an escape the other two lacked.
    Running all three against one organisation in one test is the thing that
    fails loudly if a future change re-splits them.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    api = _api(operator)

    added = api.post(ADD_URL, _new_member_payload(org.id, role_id=_role(org, "viewer").pk), format="json")
    assert added.status_code == 201, added.data
    member_pk = added.data["id"]

    changed = api.patch(_role_url(member_pk), {"role_id": _role(org, "agent").pk}, format="json")
    assert changed.status_code == 200, changed.data

    removed = api.delete(_detail_url(member_pk))
    assert removed.status_code == 204, removed.data

    tenant_user = TenantUser.objects.get(pk=member_pk)
    assert tenant_user.tenant_id == org.id
    assert tenant_user.role.slug == "agent"
    assert tenant_user.is_active is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. What the operator still may not do
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_operator_must_name_the_organisation():
    """No membership to fall back on, so silence is a 400 and not a guess.

    Inventing an organisation for them — the first one, the newest one — would
    put a real person into a real customer's account on a typo.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)

    response = _api(operator).post(ADD_URL, _new_member_payload(role_id=_role(org, "viewer").pk), format="json")

    assert response.status_code == 400, response.data
    assert "tenant" in response.data, response.data
    assert TenantUser.objects.filter(tenant=org).count() == 0


@pytest.mark.django_db
def test_an_operator_naming_an_organisation_that_does_not_exist_creates_nothing():
    """A 400, and — the part worth asserting — no orphaned ``User`` either.

    ``add_member_to_tenant`` creates the account and the membership in one
    transaction, so a refusal that arrived after the account was created would
    leave a half-onboarded person behind.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    payload = _new_member_payload(role_id=_role(org, "viewer").pk)
    payload["tenant"] = 10**9

    response = _api(operator).post(ADD_URL, payload, format="json")

    assert response.status_code == 400, response.data
    assert TenantUser.objects.filter(tenant=org).count() == 0
    assert not User.objects.filter(email=payload["email"]).exists()


@pytest.mark.django_db
def test_an_operator_still_cannot_hand_out_the_OWNER_role():
    """Ownership moves through transfer-ownership and nowhere else.

    Rule 2 of ``validate_role_assignment`` is not a priority comparison, so
    waiving the priority rule for an operator must not have taken this with it.
    An organisation gets exactly one owner and this endpoint is not how it
    changes.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)

    response = _api(operator).post(
        ADD_URL,
        _new_member_payload(org.id, role_id=_role(org, "owner").pk),
        format="json",
    )

    assert response.status_code == 400, response.data
    assert "role_id" in response.data, response.data
    assert TenantUser.objects.filter(tenant=org).count() == 0


@pytest.mark.django_db
def test_an_operator_cannot_borrow_a_role_from_another_organisation():
    """Rule 1 of ``validate_role_assignment``, which the operator path also keeps.

    Roles are per-organisation rows. The operator now chooses the organisation
    *and* the role independently, which is exactly the pairing that could go
    wrong: a role id belonging to a different organisation must not resolve.
    """
    from tenants.models import TenantUser

    target = _tenant("target")
    elsewhere = _tenant("elsewhere")
    operator = _user(is_superuser=True, is_staff=True)

    response = _api(operator).post(
        ADD_URL,
        _new_member_payload(target.id, role_id=_role(elsewhere, "admin").pk),
        format="json",
    )

    assert response.status_code == 400, response.data
    assert "role_id" in response.data, response.data
    assert TenantUser.objects.filter(tenant=target).count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. What the fix must not have widened
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_ordinary_admin_still_adds_members_to_their_own_organisation():
    """The unchanged path, asserted on the row rather than the status code.

    The organisation now travels a different route to ``add_member_to_tenant``
    — through ``_target_tenant_for_add`` rather than straight off
    ``requester_tu`` — so "still 201" is not enough; the member has to still
    land in the admin's own organisation.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    admin = _user()
    _member(org, admin, "admin")
    viewer_role = _role(org, "viewer")

    payload = _new_member_payload(role_id=viewer_role.pk)
    response = _api(admin).post(ADD_URL, payload, format="json")

    assert response.status_code == 201, response.data
    tenant_user = TenantUser.objects.select_related("user").get(pk=response.data["id"])
    assert tenant_user.tenant_id == org.id
    assert tenant_user.role_id == viewer_role.pk
    assert tenant_user.created_by_id == admin.pk


@pytest.mark.django_db
def test_an_ordinary_admin_can_name_their_own_organisation_and_it_is_accepted():
    """Agreement is not an error.

    A client that fills ``tenant`` in from the session sends the caller's own
    organisation on every request. Refusing that would break the ordinary case
    in the name of a control aimed at the foreign one.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    admin = _user()
    _member(org, admin, "admin")

    response = _api(admin).post(
        ADD_URL,
        _new_member_payload(org.id, role_id=_role(org, "viewer").pk),
        format="json",
    )

    assert response.status_code == 201, response.data
    assert TenantUser.objects.get(pk=response.data["id"]).tenant_id == org.id


@pytest.mark.django_db
def test_an_ordinary_admin_naming_another_organisation_is_refused_not_redirected():
    """A member can never name another organisation — the #346 rule, restated here.

    Refused rather than quietly rewritten to their own: substituting would turn
    an attempt to plant a member in somebody else's account into an ordinary
    201 in every log, and would also create the member somewhere the caller
    never asked for.
    """
    from tenants.models import TenantUser

    own = _tenant("own")
    other = _tenant("other")
    admin = _user()
    _member(own, admin, "admin")

    payload = _new_member_payload(other.id, role_id=_role(own, "viewer").pk)
    response = _api(admin).post(ADD_URL, payload, format="json")

    assert response.status_code == 403, response.data
    assert TenantUser.objects.filter(tenant=other).count() == 0
    # Not redirected into their own organisation either.
    assert not TenantUser.objects.filter(tenant=own, user__email=payload["email"]).exists()


@pytest.mark.django_db
def test_the_priority_guard_still_stops_a_member_promoting_past_themselves():
    """``refuse_if_outranked``'s member branch, unchanged.

    The operator branch waives this guard, so this is the test that says the
    waiver is conditional. An admin and an admin are equal priority: neither may
    re-role the other, and the row must still hold the role it started with.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    actor = _user()
    _member(org, actor, "admin")
    target = _member(org, _user(), "admin")

    response = _api(actor).patch(_role_url(target.pk), {"role_id": _role(org, "viewer").pk}, format="json")

    assert response.status_code == 403, response.data
    assert TenantUser.objects.get(pk=target.pk).role.slug == "admin"


@pytest.mark.django_db
def test_a_member_with_no_role_in_the_organisation_gains_nothing_by_holding_none():
    """ "Holds no membership" is not the privilege — being the operator is.

    A signed-up user who belongs to no organisation yet is the common case, and
    they match the shape of an operator in every way except the one that counts.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    nobody = _user()

    response = _api(nobody).post(
        ADD_URL,
        _new_member_payload(org.id, role_id=_role(org, "viewer").pk),
        format="json",
    )

    assert response.status_code == 403, response.data
    assert TenantUser.objects.filter(tenant=org).count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. A superuser who is a member of one organisation (#352)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_superuser_who_owns_one_organisation_cannot_add_a_member_to_another():
    """The escape is for a *tenantless* operator, and only for them.

    Otherwise adding yourself to an organisation to debug it would be a step
    *down* in reach, and — worse — the unconstrained path would follow the
    superuser into every other organisation. #352 decided this for writes; the
    three actions here have to agree with it.
    """
    from tenants.models import TenantUser

    own = _tenant("own")
    victim = _tenant("victim")
    operator = _user(is_superuser=True, is_staff=True)
    _member(own, operator, "owner")

    response = _api(operator).post(
        ADD_URL,
        _new_member_payload(victim.id, role_id=_role(victim, "admin").pk),
        format="json",
    )

    assert response.status_code == 403, response.data
    assert TenantUser.objects.filter(tenant=victim).count() == 0


@pytest.mark.django_db
def test_a_superuser_who_owns_one_organisation_cannot_re_role_a_member_of_another():
    """The same refusal on ``change_role``.

    Reads are not scoped for a superuser, so ``get_object`` finds the other
    organisation's member row. What stops the write is that the caller has no
    membership *in that organisation* — the previous code looked up whatever
    membership they had and compared an owner-of-A priority against a member of
    B, which is a favourable answer to the wrong question.
    """
    from tenants.models import TenantUser

    own = _tenant("own")
    victim = _tenant("victim")
    operator = _user(is_superuser=True, is_staff=True)
    _member(own, operator, "owner")
    target = _member(victim, _user(), "viewer")

    response = _api(operator).patch(_role_url(target.pk), {"role_id": _role(victim, "agent").pk}, format="json")

    assert response.status_code == 403, response.data
    assert TenantUser.objects.get(pk=target.pk).role.slug == "viewer"


@pytest.mark.django_db
def test_a_superuser_who_owns_one_organisation_cannot_remove_a_member_of_another():
    """And on ``destroy`` — the action that used to allow it.

    Its old ``if not request.user.is_superuser`` escape sat under a membership
    refusal, so a superuser holding *any* membership skipped past into the
    priority comparison and out the other side. This is the behaviour change the
    consistency requirement implies, stated as a test rather than left to be
    discovered.
    """
    from tenants.models import TenantUser

    own = _tenant("own")
    victim = _tenant("victim")
    operator = _user(is_superuser=True, is_staff=True)
    _member(own, operator, "owner")
    target = _member(victim, _user(), "viewer")

    response = _api(operator).delete(_detail_url(target.pk))

    assert response.status_code == 403, response.data
    assert TenantUser.objects.get(pk=target.pk).is_active is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. Impersonation stays read-only (#300 / #326 / #344)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_impersonated_session_cannot_add_a_member():
    """An impersonation token is a superuser holding no membership — the exact
    shape of a platform operator — so this is the thing the fix could plausibly
    have handed write access to by accident.

    #300 refuses non-safe methods from a borrowed token at two independent
    layers, so the 403 arrives before any of #356's code runs. The point is that
    it keeps arriving.
    """
    from tenants.models import TenantUser

    org = _tenant("viewed")
    actor = _user(is_superuser=True, is_staff=True)

    response = _impersonating(actor, org).post(
        ADD_URL,
        _new_member_payload(org.id, role_id=_role(org, "admin").pk),
        format="json",
    )

    assert response.status_code == 403, response.data
    assert TenantUser.objects.filter(tenant=org).count() == 0


@pytest.mark.django_db
def test_an_impersonated_session_cannot_change_a_role():
    from tenants.models import TenantUser

    org = _tenant("viewed")
    actor = _user(is_superuser=True, is_staff=True)
    target = _member(org, _user(), "viewer")

    response = _impersonating(actor, org).patch(
        _role_url(target.pk),
        {"role_id": _role(org, "admin").pk},
        format="json",
    )

    assert response.status_code == 403, response.data
    assert TenantUser.objects.get(pk=target.pk).role.slug == "viewer"


@pytest.mark.django_db
def test_an_impersonated_session_cannot_remove_a_member():
    from tenants.models import TenantUser

    org = _tenant("viewed")
    actor = _user(is_superuser=True, is_staff=True)
    target = _member(org, _user(), "viewer")

    response = _impersonating(actor, org).delete(_detail_url(target.pk))

    assert response.status_code == 403, response.data
    assert TenantUser.objects.get(pk=target.pk).is_active is True


@pytest.mark.django_db
def test_impersonation_is_excluded_from_the_operator_branch_itself():
    """The same claim as the three above, one layer down.

    Reaches past the URL deliberately, and it is the only test here that does:
    over HTTP #300 refuses first, so a test that only posted would keep passing
    while this branch rotted, and the day #300's HTTP-level refusal is relaxed
    an impersonated session would inherit the most privileged member-management
    surface in the product. Mirrors
    ``wa/tests/test_platform_operator_app_creation.py``, because it is the same
    helper being asked by a second viewset — which is the whole point of #353
    having put it on the base class.
    """
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    from tenants.viewsets.member_management import MemberManagementViewSet
    from users.impersonation import IMPERSONATED_BY_CLAIM

    org = _tenant("viewed")
    actor = _user(is_superuser=True, is_staff=True)

    request = Request(APIRequestFactory().post(ADD_URL))
    request.user = actor

    view = MemberManagementViewSet()
    view.request = request
    assert view.acting_as_platform_operator() is True

    # Same user, same absent membership — only the borrowed-token claims differ.
    setattr(actor, IMPERSONATED_BY_CLAIM, _user(is_superuser=True).id)
    actor.tenant_id = org.id
    del request._cached_tenant_ids

    assert view.acting_as_platform_operator() is False
