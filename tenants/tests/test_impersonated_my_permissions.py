"""What ``GET /tenants/my-permissions/`` answers a caller who holds no row (#363).

The endpoint decided what a caller may do by reading their ``TenantUser``. A
platform admin inside a "view as organisation" session (#300) holds none in the
organisation they are viewing — that is the premise of the feature — so the
endpoint 404'd, the web client recorded "no role", every ``usePermission``
resolved false, and the organisation UI rendered only its one ungated nav item.
The third endpoint to make this mistake, after #353 and #356.

**The map is asserted against the API, not against itself.** A test that only
read the response would pass against a fix that reported anything at all:
all-true would "fix" the sidebar and lie, all-false would 200 and leave the UI
exactly as blank as the bug did. So the two tests that matter take a permission
the response reports and then make the request it describes — a reported ``true``
has to be a request that succeeds, and a reported ``false`` has to be a 403 with
the database unchanged afterwards. That is the property the web client will be
built on top of.

Two organisations throughout, because "the session can read" and "the session
can read *this* organisation" are different claims and #326 is about the
difference.

No network and no email: nothing here creates a WhatsApp app or invites a member
on a path that sends mail, except the one operator test that goes through
``add_member``, whose verification mail is caught and logged by
``add_member_to_tenant`` either way.

HOW TO RUN:
    DB_NAME=... python -m pytest tenants/tests/test_impersonated_my_permissions.py -v
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tenants.permissions import ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS
from users.impersonation import issue_impersonation_token

User = get_user_model()

_mobile_seq = itertools.count(1)

PERMISSIONS_URL = "/tenants/my-permissions/"
TENANTS_URL = "/tenants/"
MEMBERS_URL = "/tenants/members/"
ADD_MEMBER_URL = f"{MEMBERS_URL}add/"

# Every permission whose action is a read, derived the same way the endpoint
# derives it. Spelled out here rather than imported from ``tenants.permissions``
# so the test states the rule independently: importing ``is_non_mutating`` would
# make this assert that the function equals itself.
VIEW_PERMISSIONS = frozenset(p for p in ALL_PERMISSIONS if p.rsplit(".", 1)[-1] == "view")
MUTATING_PERMISSIONS = frozenset(ALL_PERMISSIONS) - VIEW_PERMISSIONS


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _tenant(tag: str = "org"):
    """An organisation. Creating one also seeds its five default roles."""
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{tag}-{uuid.uuid4().hex[:6]}", is_active=True)


def _user(**kwargs):
    return User.objects.create_user(
        username=f"perms_{uuid.uuid4().hex[:8]}",
        email=f"perms_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190010{next(_mobile_seq):05d}",
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
    """A real, signed, audited impersonation token — not a stand-in for one.

    The read-only refusals this file asserts live on the token's claims and on
    the ``ImpersonationSession`` row, so a hand-built client would be asserting
    against a session the product cannot issue.
    """
    raw, _session = issue_impersonation_token(actor, tenant)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return api


def _ids_in(response):
    data = response.data
    rows = data["results"] if isinstance(data, dict) and "results" in data else data
    return {row["id"] for row in rows}


# ─────────────────────────────────────────────────────────────────────────────
# 1. The defect, asserted as its consequence
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_impersonated_session_is_told_what_every_nav_item_gates_on():
    """#363 itself. This was a 404, and the sidebar rendered one item.

    Asserts the whole key set, not a sample: the client looks each permission up
    by name and treats a key it cannot find as a denial, so a response missing
    half the registry would hide half the UI just as effectively as the 404 did.
    """
    org = _tenant("client")
    actor = _user(is_superuser=True, is_staff=True)

    response = _impersonating(actor, org).get(PERMISSIONS_URL)

    assert response.status_code == 200, response.data
    perms = response.data["permissions"]
    assert set(perms) == set(ALL_PERMISSIONS)
    for perm in sorted(VIEW_PERMISSIONS):
        assert perms[perm] is True, f"an impersonated session must be able to read '{perm}'"


@pytest.mark.django_db
def test_an_impersonated_session_is_told_it_may_not_act():
    """The other half, and the half that must never drift.

    Every non-``view`` key false — including the four that happen to only read
    (``contact.export``, ``broadcast.charge_breakdown`` and the two recording
    keys). The rule errs towards false on purpose: a greyed-out button on a read
    the session could have made costs an operator one click, while a button that
    promises a write the API refuses costs them a support call.
    """
    org = _tenant("client")
    actor = _user(is_superuser=True, is_staff=True)

    perms = _impersonating(actor, org).get(PERMISSIONS_URL).data["permissions"]

    for perm in sorted(MUTATING_PERMISSIONS):
        assert perms[perm] is False, f"an impersonated session must not be told it may '{perm}'"


@pytest.mark.django_db
def test_every_read_the_session_is_promised_is_a_read_it_can_make():
    """A reported ``true`` has to correspond to a request that actually works.

    Two of them, through the real URLs, with a second organisation present so
    that "can read" is distinguished from "can read this organisation" — #326's
    scoping is what makes the promise worth anything.
    """
    org = _tenant("viewed")
    other = _tenant("other")
    _member(org, _user(), "owner")
    actor = _user(is_superuser=True, is_staff=True)
    api = _impersonating(actor, org)

    perms = api.get(PERMISSIONS_URL).data["permissions"]
    assert perms["tenant.view"] is True
    assert perms["users.view"] is True

    tenants = api.get(TENANTS_URL)
    assert tenants.status_code == 200, tenants.data
    assert _ids_in(tenants) == {org.id}, "the promised read must also be scoped to the viewed organisation"
    assert other.id not in _ids_in(tenants)

    members = api.get(MEMBERS_URL)
    assert members.status_code == 200, members.data


@pytest.mark.django_db
def test_every_action_the_session_is_refused_the_api_also_refuses():
    """The inverse, and the assertion that the map is not a polite fiction.

    Two reported ``false`` keys, each followed by the request it describes: a
    403 and — the part worth asserting — nothing changed in the database. If a
    later change reported these true to "make the UI work", this fails on the
    mismatch rather than waiting for an operator to discover it.
    """
    from tenants.models import TenantUser

    org = _tenant("viewed")
    original_name = org.name
    actor = _user(is_superuser=True, is_staff=True)
    api = _impersonating(actor, org)

    perms = api.get(PERMISSIONS_URL).data["permissions"]
    assert perms["tenant.edit"] is False
    assert perms["users.invite"] is False

    edited = api.patch(f"{TENANTS_URL}{org.id}/", {"name": "Renamed By Operator"}, format="json")
    assert edited.status_code == 403, edited.data
    org.refresh_from_db()
    assert org.name == original_name

    invited = api.post(
        ADD_MEMBER_URL,
        {
            "email": f"newhire_{uuid.uuid4().hex[:8]}@test.com",
            "password": "Str0ng!Pass",
            "first_name": "New",
            "last_name": "Hire",
            "role_id": _role(org, "viewer").pk,
            "tenant": org.id,
        },
        format="json",
    )
    assert invited.status_code == 403, invited.data
    assert TenantUser.objects.filter(tenant=org).count() == 0


@pytest.mark.django_db
def test_the_impersonated_role_is_a_descriptor_and_not_a_role_row():
    """``role`` is answered, and answered without inventing a ``TenantRole``.

    The client reads ``role`` to label the session and compares ``priority``
    against its own thresholds, so ``null`` would leave it with nothing. The
    assertions that matter are the negative ones: ``id`` is null and no row with
    this slug exists, so a client that tries to resolve or edit the role finds
    nothing rather than somebody else's row, and the organisation's own five
    roles are untouched.
    """
    from tenants.models import TenantRole

    org = _tenant("client")
    actor = _user(is_superuser=True, is_staff=True)
    roles_before = TenantRole.objects.count()

    role = _impersonating(actor, org).get(PERMISSIONS_URL).data["role"]

    assert role["id"] is None
    assert role["slug"] == "impersonated-read-only"
    assert role["name"] == "Viewing as organisation (read-only)"
    # Below VIEWER's 20: the session may do less than any seeded role.
    assert role["priority"] == 0
    assert role["is_system"] is False
    assert role["is_custom"] is False
    assert not TenantRole.objects.filter(slug="impersonated-read-only").exists()
    assert TenantRole.objects.count() == roles_before


# ─────────────────────────────────────────────────────────────────────────────
# 2. A platform operator who is not impersonating
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_platform_operator_is_told_they_may_do_everything():
    """The deliberate answer for a superuser who belongs to no organisation.

    ``TenantRolePermission`` returns True for a superuser before it looks at any
    role, so every key is true because every key *is* true for them. A 404 was
    the other defensible answer and is what shipped by accident; the test pins
    the decision so a later reader finds it stated rather than inferred.
    """
    operator = _user(is_superuser=True, is_staff=True)

    response = _api(operator).get(PERMISSIONS_URL)

    assert response.status_code == 200, response.data
    perms = response.data["permissions"]
    assert set(perms) == set(ALL_PERMISSIONS)
    assert all(perms.values())
    role = response.data["role"]
    assert role["id"] is None
    assert role["slug"] == "platform-operator"
    assert role["priority"] == 100


@pytest.mark.django_db
def test_what_the_operator_is_promised_the_api_delivers():
    """The same consequence check as for impersonation, in the other direction.

    ``users.invite`` is reported true, so the invite has to work — this is the
    #356 capability, and reporting it from this endpoint has to agree with the
    endpoint that performs it.
    """
    from tenants.models import TenantUser

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    api = _api(operator)

    assert api.get(PERMISSIONS_URL).data["permissions"]["users.invite"] is True

    payload = {
        "email": f"newhire_{uuid.uuid4().hex[:8]}@test.com",
        "password": "Str0ng!Pass",
        "first_name": "New",
        "last_name": "Hire",
        "role_id": _role(org, "viewer").pk,
        "tenant": org.id,
    }
    added = api.post(ADD_MEMBER_URL, payload, format="json")

    assert added.status_code == 201, added.data
    assert TenantUser.objects.get(pk=added.data["id"]).tenant_id == org.id


# ─────────────────────────────────────────────────────────────────────────────
# 3. What must not have changed
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["owner", "admin", "manager", "agent", "viewer"])
def test_an_ordinary_member_gets_exactly_their_role_and_nothing_else(slug):
    """The case the whole fix must not regress, on all five seeded roles.

    Asserted against ``DEFAULT_ROLE_PERMISSIONS`` rather than against "not the
    read-only map", because the failure to watch for is a new branch swallowing
    a member — and a member who fell into the impersonation branch would look
    plausible for VIEWER and wrong for everyone else.
    """
    org = _tenant("client")
    user = _user()
    membership = _member(org, user, slug)

    response = _api(user).get(PERMISSIONS_URL)

    assert response.status_code == 200, response.data
    assert response.data["role"]["id"] == membership.role_id
    assert response.data["role"]["slug"] == slug
    assert response.data["role"]["is_system"] is True
    assert response.data["role"]["is_custom"] is False
    perms = response.data["permissions"]
    for perm in ALL_PERMISSIONS:
        expected = DEFAULT_ROLE_PERMISSIONS[slug].get(perm, False)
        assert perms[perm] is expected, f"{slug} '{perm}' should be {expected}"


@pytest.mark.django_db
def test_a_superuser_who_is_a_member_still_gets_their_real_role():
    """#352, restated here: holding a membership is what decides, not the flag.

    They are judged by the organisation they joined, so the map is that role's
    map — which understates what ``TenantRolePermission``'s superuser bypass
    would let them do. That understatement is the price of #352's rule and is
    preferred to its alternative: a superuser who adds themselves to one
    organisation to debug it would otherwise be handed an all-true map in it.
    """
    org = _tenant("client")
    superuser = _user(is_superuser=True, is_staff=True)
    membership = _member(org, superuser, "viewer")

    response = _api(superuser).get(PERMISSIONS_URL)

    assert response.status_code == 200, response.data
    assert response.data["role"]["id"] == membership.role_id
    assert response.data["role"]["slug"] == "viewer"
    assert response.data["permissions"]["contact.create"] is False


@pytest.mark.django_db
def test_a_user_with_no_membership_and_no_platform_rights_still_gets_404():
    """ "Holds no membership" is not the privilege — being the operator is.

    A signed-up user who belongs to nothing yet matches an operator in every way
    except the one that counts, and the 404 is the honest answer for them: there
    is no organisation whose permissions could be reported.
    """
    nobody = _user()

    response = _api(nobody).get(PERMISSIONS_URL)

    assert response.status_code == 404, response.data


@pytest.mark.django_db
def test_an_unauthenticated_caller_is_still_refused():
    """The new branches all run after authentication; this says so."""
    response = APIClient().get(PERMISSIONS_URL)

    assert response.status_code == 401, response.data
