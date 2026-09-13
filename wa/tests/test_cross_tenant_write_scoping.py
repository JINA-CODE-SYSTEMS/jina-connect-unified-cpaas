"""A write may not choose somebody else's organisation (#346).

The defect: ``tenant`` was a writable field on both ``POST /wa/v2/apps/`` create
serializers and nothing tied it to the caller. An owner of organisation A posted
``tenant: <B>`` and got 201, with the row in B — and could not then see what they
had made, because ``BaseTenantModelViewSet`` scopes *reads*. A ``TenantWAApp``
row is what the whole WhatsApp surface dispatches on, so a planted one routes the
attacker's inbound webhooks into the victim's inbox, carries credentials the
victim never supplied, and bills broadcasts to the victim's wallet.

**These tests assert the consequence, not the field.** Each one posts through the
real URL as a real role and then counts rows in the *other* organisation. A test
that asserted ``tenant`` was in ``read_only_fields`` would pass against a fix that
made it read-only on one of the two create serializers and not the other, and
would say nothing at all about the second route to the same row type found in
the audit (``POST /tenants/tenant-gupshup/``, same ``TenantWAApp`` model) or about
the four other apps that had the same shape.

The audit behind that claim is recorded, and turned into a CI failure, in
``abstract/tests/test_tenant_write_scoping.py``.

No network: ``bsp: GUPSHUP`` is used wherever a create does not need to be META,
so nothing here reaches the Graph preflight at all.

HOW TO RUN:
    DB_NAME=... python -m pytest wa/tests/test_cross_tenant_write_scoping.py -v
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()

_mobile_seq = itertools.count(1)

APPS_URL = "/wa/v2/apps/"
GUPSHUP_APPS_URL = "/tenants/tenant-gupshup/"
TAGS_URL = "/tenants/tenant-tags/"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _tenant(tag: str = "org"):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{tag}-{uuid.uuid4().hex[:6]}", is_active=True)


def _user(**kwargs):
    return User.objects.create_user(
        username=f"wscope_{uuid.uuid4().hex[:8]}",
        email=f"wscope_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190007{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test credential
        **kwargs,
    )


def _member(tenant, user, role_slug: str = "owner"):
    from tenants.models import TenantRole, TenantUser

    role = TenantRole.objects.get(tenant=tenant, slug=role_slug)
    return TenantUser.objects.create(tenant=tenant, user=user, role=role)


def _client_for(tenant, role_slug: str = "owner"):
    """An ``APIClient`` authenticated as a user holding *role_slug* in *tenant*."""
    user = _user()
    _member(tenant, user, role_slug)
    api = APIClient()
    api.force_authenticate(user=user)
    return api, user


def _app_payload(tenant_id=None, **overrides):
    """A create body for a Gupshup app — no Graph identifiers, so no network."""
    payload = {
        "app_name": f"wscope-{uuid.uuid4().hex[:6]}",
        "phone_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "app_id": "1234567890",
        "bsp": "GUPSHUP",
    }
    if tenant_id is not None:
        payload["tenant"] = tenant_id
    payload.update(overrides)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 1. The reported defect, asserted as its consequence
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_owner_of_one_organisation_cannot_create_an_app_in_another():
    """#346 itself: 4xx, and **no row in B afterwards**.

    The row count is the assertion that matters. The attacker cannot see what
    they created — reads are scoped — so a fix that returned 4xx while still
    writing the row would look identical from the client side and would leave the
    whole of the damage in place.
    """
    from wa.models import WAApp

    victim = _tenant("victim")
    attacker = _tenant("attacker")
    api, _user_a = _client_for(attacker, "owner")

    response = api.post(APPS_URL, _app_payload(tenant_id=victim.id), format="json")

    assert 400 <= response.status_code < 500, response.data
    assert WAApp.objects.filter(tenant=victim).count() == 0, (
        "An app landed in the victim's organisation. This is #346: the row that "
        "the whole WhatsApp surface dispatches on, created by somebody outside "
        "the organisation and invisible to everybody inside it."
    )
    # Nor quietly relocated into the attacker's own organisation: a refusal
    # refuses, it does not substitute.
    assert WAApp.objects.filter(tenant=attacker).count() == 0


@pytest.mark.django_db
def test_the_refusal_is_a_403_and_says_why():
    """Not a 400 on a field. The body is well-formed; the caller is not allowed."""
    victim = _tenant("victim")
    attacker = _tenant("attacker")
    api, _u = _client_for(attacker, "owner")

    response = api.post(APPS_URL, _app_payload(tenant_id=victim.id), format="json")

    assert response.status_code == 403, response.data
    assert "organisation" in str(response.data).lower()
    # And it must not confirm whether that organisation exists — the message for
    # a real id and an invented one is the same one (#301's lesson).
    invented = api.post(APPS_URL, _app_payload(tenant_id=victim.id + 10_000), format="json")
    assert invented.status_code == 403
    assert str(invented.data) == str(response.data)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Agreeing, absent, disagreeing
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_submitting_your_own_organisation_is_accepted_without_fuss():
    """Agreement is not an error. #345's documentation tells operators to send it."""
    from wa.models import WAApp

    org = _tenant("own")
    api, _u = _client_for(org, "owner")

    response = api.post(APPS_URL, _app_payload(tenant_id=org.id), format="json")

    assert response.status_code == 201, response.data
    assert WAApp.objects.get(pk=response.data["id"]).tenant_id == org.id


@pytest.mark.django_db
def test_omitting_the_tenant_derives_it_from_the_caller():
    """``tenant`` is ``required=True`` on this serializer, so "omit it" only works
    because the derivation happens before validation rather than at save time."""
    from wa.models import WAApp

    org = _tenant("derived")
    api, _u = _client_for(org, "owner")

    response = api.post(APPS_URL, _app_payload(), format="json")

    assert response.status_code == 201, response.data
    assert WAApp.objects.get(pk=response.data["id"]).tenant_id == org.id


@pytest.mark.django_db
def test_a_user_in_two_organisations_may_name_either_of_their_own():
    """Scoping to "the caller's membership" means all of it, not an arbitrary one.

    A single-tenant rule would have refused this user their own second
    organisation — and the endpoint is how an agency with two client
    organisations onboards the second.
    """
    from wa.models import WAApp

    first = _tenant("first")
    second = _tenant("second")
    user = _user()
    _member(first, user, "owner")
    _member(second, user, "owner")
    api = APIClient()
    api.force_authenticate(user=user)

    for org in (first, second):
        response = api.post(APPS_URL, _app_payload(tenant_id=org.id), format="json")
        assert response.status_code == 201, response.data
        assert WAApp.objects.get(pk=response.data["id"]).tenant_id == org.id

    third = _tenant("third")
    refused = api.post(APPS_URL, _app_payload(tenant_id=third.id), format="json")
    assert refused.status_code == 403
    assert WAApp.objects.filter(tenant=third).count() == 0


@pytest.mark.django_db
def test_an_update_cannot_move_a_row_into_another_organisation():
    """PATCH is the other half of the hole: ``tenant`` is writable on the update
    serializers too, and a row moved out is a row its owner stops being able to
    see."""
    from wa.models import WAApp

    org = _tenant("updater")
    victim = _tenant("victim")
    api, _u = _client_for(org, "owner")

    created = api.post(APPS_URL, _app_payload(tenant_id=org.id), format="json")
    assert created.status_code == 201, created.data
    app_id = created.data["id"]

    response = api.patch(f"{APPS_URL}{app_id}/", {"tenant": victim.id}, format="json")

    assert response.status_code == 403, response.data
    assert WAApp.objects.get(pk=app_id).tenant_id == org.id


@pytest.mark.django_db
def test_an_update_that_omits_the_tenant_leaves_it_alone():
    """The derivation is create-only on purpose: a user who belongs to A and B has
    an arbitrary one of the two resolved as their default, so deriving on PATCH
    would relocate their own rows between their own organisations."""
    from wa.models import WAApp

    first = _tenant("first")
    second = _tenant("second")
    user = _user()
    _member(first, user, "owner")
    _member(second, user, "owner")
    api = APIClient()
    api.force_authenticate(user=user)

    created = api.post(APPS_URL, _app_payload(tenant_id=second.id), format="json")
    assert created.status_code == 201, created.data
    app_id = created.data["id"]

    response = api.patch(f"{APPS_URL}{app_id}/", {"app_name": "renamed"}, format="json")

    assert response.status_code == 200, response.data
    assert WAApp.objects.get(pk=app_id).tenant_id == second.id


# ─────────────────────────────────────────────────────────────────────────────
# 3. The other endpoints the audit found — the point of fixing the base class
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_second_route_to_the_same_row_type_is_closed_too():
    """``POST /tenants/tenant-gupshup/`` writes the *same* ``TenantWAApp`` model
    through a different viewset and a different serializer, and had the identical
    hole. Fixing only the reported endpoint would have left this one open.

    **403 exactly, not "some 4xx".** ``TenantGupshupAppsSerializer`` is
    ``fields = "__all__"`` with three required price fields, so a body this short
    is a 400 for missing fields whatever the tenant says — a "some 4xx" assertion
    here passes with the control removed and proves nothing. Demanding 403 is
    possible because the refusal happens in ``get_serializer``, before
    ``is_valid()``: the caller is told they may not write into that organisation
    rather than which fields they forgot, and a 400 now fails this test.
    """
    from wa.models import WAApp

    victim = _tenant("victim")
    attacker = _tenant("attacker")
    api, _u = _client_for(attacker, "owner")

    response = api.post(
        GUPSHUP_APPS_URL,
        {
            "tenant": victim.id,
            "app_name": f"gup-{uuid.uuid4().hex[:6]}",
            "app_id": "1234567890",
        },
        format="json",
    )

    assert response.status_code == 403, response.data
    assert WAApp.objects.filter(tenant=victim).count() == 0


@pytest.mark.django_db
def test_a_different_app_entirely_is_covered_by_the_same_control():
    """``TenantTags`` is ``fields = "__all__"`` on a model with a tenant column —
    a different app, a different serializer, nobody's idea of a security
    boundary, and the same hole. This is why the control is on the base class."""
    from tenants.models import TenantTags

    victim = _tenant("victim")
    attacker = _tenant("attacker")
    api, _u = _client_for(attacker, "owner")

    response = api.post(TAGS_URL, {"tenant": victim.id, "name": "planted"}, format="json")

    # 403 exactly: this body *is* otherwise valid, so with the control removed it
    # is a 201, and a "some 4xx" assertion could only ever be satisfied by the
    # control itself. Pinning the code anyway keeps it that way.
    assert response.status_code == 403, response.data
    assert TenantTags.objects.filter(tenant=victim).count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. The paths that must keep working
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_platform_operator_may_still_create_for_an_organisation_they_are_not_in():
    """#345's operator workflow. A superuser holding no membership is the one
    caller for whom the body is the only possible source of the organisation, so
    for them it is honoured.

    Note what this does *not* claim: the same operator still cannot create a
    **META** app, because ``WAAppViewSet.get_serializer_class`` reads a
    ``TenantUser`` role priority they do not have and hands them the safe create
    serializer, which has no ``waba_id`` field to satisfy the META requirement.
    That is the known related defect recorded in #346 and is deliberately not
    fixed here; this test pins that the write scoping does not make it worse.
    """
    from wa.models import WAApp

    org = _tenant("client")
    operator = _user(is_superuser=True, is_staff=True)
    api = APIClient()
    api.force_authenticate(user=operator)

    response = api.post(APPS_URL, _app_payload(tenant_id=org.id), format="json")

    assert response.status_code == 201, response.data
    assert WAApp.objects.get(pk=response.data["id"]).tenant_id == org.id


@pytest.mark.django_db
def test_a_superuser_who_does_hold_a_membership_is_scoped_to_it():
    """The unconstrained branch is for a *tenantless* operator. A superuser who is
    also a member of one organisation is acting as that organisation's user, and
    gets the same refusal anybody else would — otherwise "make yourself a member
    to debug something" would quietly be a way past the control."""
    from wa.models import WAApp

    own = _tenant("ownorg")
    victim = _tenant("victim")
    operator = _user(is_superuser=True, is_staff=True)
    _member(own, operator, "owner")
    api = APIClient()
    api.force_authenticate(user=operator)

    response = api.post(APPS_URL, _app_payload(tenant_id=victim.id), format="json")

    assert response.status_code == 403, response.data
    assert WAApp.objects.filter(tenant=victim).count() == 0


@pytest.mark.django_db
def test_an_impersonated_session_cannot_write_at_all():
    """What an impersonated write does *today*, asserted rather than assumed.

    #300 is read-only by design and refuses every non-safe method from a
    borrowed token at two independent layers, so the answer is 403 before the
    write scoping is ever consulted. That is why
    ``permitted_write_tenant_ids``'s impersonation branch is unreachable over
    HTTP — and why it is tested directly below rather than through a request.
    """
    from users.impersonation import issue_impersonation_token
    from wa.models import WAApp

    org = _tenant("viewed")
    operator = _user(is_superuser=True, is_staff=True)
    raw, _session = issue_impersonation_token(operator, org)

    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

    response = api.post(APPS_URL, _app_payload(tenant_id=org.id), format="json")

    assert response.status_code == 403, response.data
    assert WAApp.objects.filter(tenant=org).count() == 0


@pytest.mark.django_db
def test_the_impersonation_branch_confines_a_write_to_the_claimed_organisation():
    """Belt and braces for the branch the read-only refusal makes unreachable.

    If #300's write refusal is ever relaxed, the question becomes which
    organisation a borrowed token may write into. Without this branch the answer
    would be *any*: an impersonation token keeps ``is_superuser`` true and its
    holder has no membership in the organisation being viewed, so it would land
    in the tenantless-operator case above and be unconstrained. Deriving from the
    signed ``tenant_id`` claim puts the write where #344 already confines the
    reads.
    """
    from rest_framework.test import APIRequestFactory

    from users.impersonation import IMPERSONATED_BY_CLAIM
    from wa.viewsets.wa_app import WAAppViewSet

    viewed = _tenant("viewed")
    elsewhere = _tenant("elsewhere")
    operator = _user(is_superuser=True, is_staff=True)

    # The user as ``CustomJWTAuthentication.get_user`` stamps it for a borrowed
    # token: superuser, the viewed organisation in ``tenant_id``, and the real
    # actor in ``impersonated_by``.
    operator.tenant_id = viewed.id
    setattr(operator, IMPERSONATED_BY_CLAIM, operator.pk)

    view = WAAppViewSet()
    request = APIRequestFactory().post(APPS_URL, {}, format="json")
    request.user = operator
    view.request = request
    view.action = "create"

    assert view.permitted_write_tenant_ids() == frozenset({viewed.id})
    assert elsewhere.id not in view.permitted_write_tenant_ids()


@pytest.mark.django_db
def test_a_token_naming_one_organisation_may_write_into_that_one_only():
    """A ``tenant_id`` claim narrows a multi-tenant user's writes the same way it
    already narrows their reads in ``_get_tenant_user``.

    Intersecting with the membership set rather than trusting the claim is the
    part worth pinning: a claim naming an organisation the user has since been
    removed from must yield *nothing*, where trusting it would grant.
    """
    from rest_framework.test import APIRequestFactory

    from wa.viewsets.wa_app import WAAppViewSet

    first = _tenant("first")
    second = _tenant("second")
    left = _tenant("left")
    user = _user()
    _member(first, user, "owner")
    _member(second, user, "owner")

    def permitted_for(claim):
        user.tenant_id = claim
        view = WAAppViewSet()
        request = APIRequestFactory().post(APPS_URL, {}, format="json")
        request.user = user
        view.request = request
        view.action = "create"
        return view.permitted_write_tenant_ids()

    assert permitted_for(None) == frozenset({first.id, second.id})
    assert permitted_for(second.id) == frozenset({second.id})
    # A claim for an organisation they are not a member of grants nothing, rather
    # than granting that organisation.
    assert permitted_for(left.id) == frozenset()


@pytest.mark.django_db
def test_a_model_with_no_tenant_column_of_its_own_is_untouched():
    """Eight of the thirty-nine viewsets serve a model that reaches its tenant
    through a parent and has no column for a body to aim at. The control must be
    a no-op for them, not an error — a base-class hook that assumed the attribute
    would have broken every one of them."""
    from abstract.tenant_scoping import tenant_write_field
    from wa.models import WAMessage, WASubscription

    assert tenant_write_field(WAMessage) is None
    assert tenant_write_field(WASubscription) is None
