"""A platform operator can finish the job they were allowed to start (#353).

The defect was two decisions disagreeing about one person. ``#346``/``#352``
concluded that a superuser holding no ``TenantUser`` row may name any
organisation in a create body — that is the whole platform-operator workflow
#345 exists for. ``WAAppViewSet.get_serializer_class`` then read a role priority
off the ``TenantUser`` they do not have, found ``None``, and concluded "below
manager", handing them ``WAAppSafeCreateSerializer`` — which carries no
``waba_id``, no ``phone_number_id`` and no credential field at all. So the
operator could choose the organisation and then say nothing about it: the
create failed **400 on ``bsp``**, with a message telling them to ask an owner
or admin, for a caller more privileged than either.

"No membership" is not a low role. It means not scoped to an organisation. The
fix names that state once, in ``acting_as_platform_operator``, and has both
decisions ask it.

**Consequences, not serializer classes.** #353's acceptance says so explicitly
and it is not pedantry: asserting ``get_serializer_class() is WAAppCreateSerializer``
would pass against a fix that selected the right class and still refused the
write for some other reason, and would say nothing about whether the
credentials actually landed on the row. Every test below posts through the real
URL and then reads the database.

No network: no create here sends ``verify_with_meta``, so the Graph preflight is
never reached.

HOW TO RUN:
    DB_NAME=... python -m pytest wa/tests/test_platform_operator_app_creation.py -v
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

APPS_URL = "/wa/v2/apps/"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _tenant(tag: str = "org"):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{tag}-{uuid.uuid4().hex[:6]}", is_active=True)


def _user(**kwargs):
    return User.objects.create_user(
        username=f"platop_{uuid.uuid4().hex[:8]}",
        email=f"platop_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190009{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test credential
        **kwargs,
    )


def _member(tenant, user, role_slug: str = "owner"):
    from tenants.models import TenantRole, TenantUser

    role = TenantRole.objects.get(tenant=tenant, slug=role_slug)
    return TenantUser.objects.create(tenant=tenant, user=user, role=role)


def _custom_role_member(tenant, user, *, priority: int):
    """A role that holds ``wa_app.manage`` at an arbitrary priority.

    The default roles cannot express this: only owner (100) and admin (80) hold
    ``wa_app.manage`` and both clear #251's threshold, so the below-80 branch is
    only reachable through a custom role. That is the branch this file must
    leave untouched.
    """
    from tenants.models import RolePermission, TenantRole, TenantUser

    role = TenantRole.objects.create(
        tenant=tenant,
        name=f"Custom{priority}",
        slug=f"custom-{priority}-{uuid.uuid4().hex[:6]}",
        priority=priority,
        is_system=False,
    )
    for permission in ("wa_app.view", "wa_app.manage", "tenant.view"):
        RolePermission.objects.create(role=role, permission=permission, allowed=True)

    return TenantUser.objects.create(tenant=tenant, user=user, role=role)


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _bearer(tag: str) -> str:
    """A stand-in for the client's META access token."""
    return f"EAAG-synthetic-bearer-{tag}"


def _hmac_key(tag: str) -> str:
    """A stand-in for the client's META app secret."""
    return f"synthetic-app-hmac-{tag}"


def _meta_payload(tenant_id, **overrides):
    """The four handover values plus what the serializer requires around them."""
    payload = {
        "tenant": tenant_id,
        "app_name": f"platop-{uuid.uuid4().hex[:6]}",
        "phone_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        # Required even for META — it is the *Gupshup* app id. Pinned as a
        # documented trap in ``test_app_onboarding_contract.py``.
        "app_id": "1234567890",
        "bsp": "META",
        "waba_id": "1234567890",
        "phone_number_id": "9876543210",
        "meta_app_id": "1122334455",
        "bsp_access_token": _bearer("platop"),
        "meta_app_secret": _hmac_key("platop"),
    }
    payload.update(overrides)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 1. The defect, asserted as its consequence
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_platform_operator_creates_a_meta_app_for_an_organisation_they_are_not_in():
    """#353 itself, and #345's whole premise: no Django admin needed.

    Before the fix this was 400 with ``bsp`` naming the error and telling the
    caller to ask an owner or admin — for a caller who had bypassed RBAC
    entirely. The row, its organisation and its credentials are all asserted:
    a fix that returned 201 while dropping ``waba_id`` on the floor would leave
    an app that silently sends nothing and receives nothing, which is the exact
    failure mode the preflight exists to catch weeks earlier.
    """
    from wa.models import WAApp

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)

    response = _api(operator).post(APPS_URL, _meta_payload(org.id), format="json")

    assert response.status_code == 201, response.data

    app = WAApp.objects.get(pk=response.data["id"])
    assert app.tenant_id == org.id
    assert app.bsp == "META"
    assert app.waba_id == "1234567890"
    assert app.phone_number_id == "9876543210"
    assert app.meta_app_id == "1122334455"
    # The two that make the app actually work: the token it sends with and the
    # secret it verifies inbound signatures against.
    assert app.bsp_access_token == _bearer("platop")
    assert app.meta_app_secret == _hmac_key("platop")


@pytest.mark.django_db
def test_the_operator_can_rotate_a_credential_without_deleting_the_app():
    """Editing is the other half of the operator workflow, through the same branch.

    ``partial_update`` picks its serializer from the same expression as
    ``create``, so an operator who could create but not edit would have to delete
    and re-create to rotate a leaked token — taking the app's message, template
    and conversation history with it. Asserted on the stored value, because the
    field is write-only and the response cannot show it.
    """
    from wa.models import WAApp

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    api = _api(operator)

    created = api.post(APPS_URL, _meta_payload(org.id), format="json")
    assert created.status_code == 201, created.data
    app_id = created.data["id"]

    rotated = api.patch(
        f"{APPS_URL}{app_id}/",
        {"bsp_access_token": _bearer("rotated")},
        format="json",
    )

    assert rotated.status_code == 200, rotated.data
    assert WAApp.objects.get(pk=app_id).bsp_access_token == _bearer("rotated")
    # Write-only in both directions: rotating must not start echoing it back.
    assert "bsp_access_token" not in rotated.data


# ─────────────────────────────────────────────────────────────────────────────
# 2. What the fix must not have widened
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_role_below_priority_80_still_cannot_set_bsp_identifiers():
    """#251's line, which this must leave exactly where it was.

    The fix is an ``or`` against a state ordinary members can never be in, so
    this is the test that says so rather than assuming it.
    """
    org = _tenant("client")
    member = _user()
    _custom_role_member(org, member, priority=60)

    response = _api(member).post(APPS_URL, _meta_payload(org.id), format="json")

    assert response.status_code == 400, response.data
    assert "bsp" in response.data, response.data
    assert "waba_id" not in response.data


@pytest.mark.django_db
def test_a_superuser_who_is_a_member_is_judged_by_that_membership():
    """The escape is for a *tenantless* operator, and only for them.

    A superuser who is also a member of some organisation is acting as that
    organisation's user. Were the branch written as "superuser" rather than
    "superuser with no membership", adding yourself to an org to debug it would
    be a step *up* in field surface, and #352's write scoping — which tests
    membership before the token claim for exactly this reason — would disagree
    with this file about the same request.
    """
    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    _custom_role_member(org, operator, priority=60)

    response = _api(operator).post(APPS_URL, _meta_payload(org.id), format="json")

    assert response.status_code == 400, response.data
    assert "bsp" in response.data, response.data


@pytest.mark.django_db
def test_a_superuser_member_of_one_organisation_still_cannot_write_into_another():
    """#352's refusal, unchanged. The two controls answer different questions —
    *which fields* and *which organisation* — and neither may be widened by the
    other being relaxed."""
    from wa.models import WAApp

    own = _tenant("own")
    victim = _tenant("victim")
    operator = _user(is_superuser=True, is_staff=True)
    _member(own, operator, "owner")

    response = _api(operator).post(APPS_URL, _meta_payload(victim.id), format="json")

    assert response.status_code == 403, response.data
    assert WAApp.objects.filter(tenant=victim).count() == 0


@pytest.mark.django_db
def test_an_impersonated_session_still_cannot_create_an_app():
    """An impersonation token is a superuser holding no membership — the exact
    shape of a platform operator — so this is the one thing the fix could
    plausibly have handed write access to by accident.

    #300 refuses non-safe methods from a borrowed token at two independent
    layers, so the 403 here arrives before any serializer is chosen. The
    companion assertion below pins the branch itself, so that relaxing #300 one
    day cannot quietly promote impersonation to the most privileged field
    surface in the product.
    """
    from wa.models import WAApp

    org = _tenant("viewed")
    operator = _user(is_superuser=True, is_staff=True)
    raw, _session = issue_impersonation_token(operator, org)

    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

    response = api.post(APPS_URL, _meta_payload(org.id), format="json")

    assert response.status_code == 403, response.data
    assert WAApp.objects.filter(tenant=org).count() == 0


@pytest.mark.django_db
def test_impersonation_is_excluded_from_the_operator_branch_itself():
    """The same claim as above, one layer down, where it would still hold if
    #300's HTTP-level refusal were ever relaxed.

    Reaches past the URL deliberately: over HTTP this branch is unreachable, so
    a test that only posted would keep passing while the branch rotted.
    """
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    from users.impersonation import IMPERSONATED_BY_CLAIM
    from wa.viewsets.wa_app import WAAppViewSet

    org = _tenant("viewed")
    operator = _user(is_superuser=True, is_staff=True)

    request = Request(APIRequestFactory().post(APPS_URL))
    request.user = operator

    view = WAAppViewSet()
    view.request = request
    assert view.acting_as_platform_operator() is True

    # Same user, same absent membership — only the borrowed-token claims differ.
    setattr(operator, IMPERSONATED_BY_CLAIM, _user(is_superuser=True).id)
    operator.tenant_id = org.id
    del request._cached_tenant_ids

    assert view.acting_as_platform_operator() is False


@pytest.mark.django_db
def test_a_membershipless_user_who_is_not_a_superuser_gains_nothing():
    """ "Holds no membership" is not itself the privilege — being the platform
    operator is. A signed-up user who belongs to no organisation yet is the
    common case, not an operator."""
    from wa.models import WAApp

    org = _tenant("client")
    nobody = _user()

    response = _api(nobody).post(APPS_URL, _meta_payload(org.id), format="json")

    assert response.status_code == 403, response.data
    assert WAApp.objects.filter(tenant=org).count() == 0
