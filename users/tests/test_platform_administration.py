"""Platform administration has an API, an audit trail and a floor (#358).

Before this, the only ways to make a platform administrator were
``manage.py createsuperuser`` and the Django admin. Both hand over every
organisation's data at once, neither records who did it, and there was no way to
take it back short of the same shell. #358 is the endpoint for it — and the
ticket is explicit that an invite button which "silently grants the maximum and
calls it done" would be worse than nothing, so what is asserted here is as much
the audit and the refusals as the capability.

**Consequences, not branches.** Every test goes through the real URL with a real
caller and then reads the database back. Asserting
``acting_as_platform_operator() is False`` would pass against a permission class
that identified the caller correctly and then let the write through anyway; the
tests below check whether ``is_superuser`` actually changed on the row, whether
the ``User`` and its ``TenantUser`` rows survived a revocation, and whether an
audit row exists naming the right two people.

Three of these pin things that have bitten this codebase before:

* the last administrator cannot be revoked — the one failure with no recovery
  except a shell on the production host;
* an impersonated session reaches none of it, **including the read**, because
  #300 only refuses writes and a borrowed token keeps ``is_superuser`` true;
* revoking touches no ``TenantUser``, because the route is a ``DELETE``
  addressed by user id and the obvious reading of that is the destructive one.

No network and no email: ``send_account_verification`` catches and logs a send
failure, so the new-account path does not depend on a mail server being
reachable. The ``EmailVerificationToken`` row it writes first is what the tests
assert on instead.

HOW TO RUN:
    DB_NAME=... python -m pytest users/tests/test_platform_administration.py -v
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from users.impersonation import issue_impersonation_token
from users.models import EmailVerificationToken, PlatformAdminChange

User = get_user_model()

_mobile_seq = itertools.count(1)

ADMINS_URL = "/users/platform-admins/"
INVITE_URL = f"{ADMINS_URL}invite/"


def _detail_url(user_pk) -> str:
    return f"{ADMINS_URL}{user_pk}/"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _user(**kwargs):
    return User.objects.create_user(
        username=f"platadm_{uuid.uuid4().hex[:8]}",
        email=f"platadm_{uuid.uuid4().hex[:8]}@test.com",
        mobile=f"+9190007{next(_mobile_seq):05d}",
        password="testpass123",  # noqa: S106 — throwaway test credential
        **kwargs,
    )


def _administrator(**kwargs):
    """A platform administrator as ``createsuperuser`` leaves one: no audit row."""
    return _user(is_superuser=True, is_staff=True, **kwargs)


def _tenant(tag: str = "org"):
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{tag}-{uuid.uuid4().hex[:6]}", is_active=True)


def _member(tenant, user, role_slug: str = "owner"):
    from tenants.models import TenantRole, TenantUser

    role = TenantRole.objects.get(tenant=tenant, slug=role_slug)
    return TenantUser.objects.create(tenant=tenant, user=user, role=role)


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _impersonating(actor, tenant):
    raw, _session = issue_impersonation_token(actor, tenant)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return api


def _bearer(user, *, is_superuser=True):
    """A real JWT for ``user``, carrying the superuser claim as login would.

    Used only by the stale-token test. Everywhere else ``force_authenticate``
    is enough and says less about how authentication works.
    """
    token = AccessToken.for_user(user)
    token["username"] = user.username
    token["is_superuser"] = is_superuser
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


def _invite_payload(email=None):
    """The new-account branch: an address nobody holds yet."""
    return {
        "email": email or f"newadmin_{uuid.uuid4().hex[:8]}@test.com",
        "password": "Str0ng!Pass",
        "first_name": "New",
        "last_name": "Admin",
    }


def _rows(response):
    """The list payload, paginated or not."""
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


# ─────────────────────────────────────────────────────────────────────────────
# 1. The capability the ticket asks for
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_inviting_a_new_email_creates_an_unverified_account_and_grants_it():
    """The new-account branch, asserted on the row rather than the 201.

    Three things have to be true together, and the middle one is the safety of
    the whole design: the account exists, it is **not yet usable** because the
    address is unverified, and the grant is on it. An invite that created a
    usable account would hand platform access to whoever typed the address, not
    to whoever holds it.
    """
    inviter = _administrator()
    payload = _invite_payload()

    response = _api(inviter).post(INVITE_URL, payload, format="json")

    assert response.status_code == 201, response.data

    invited = User.objects.get(email=payload["email"])
    assert invited.is_superuser is True
    # Cannot obtain a token until the verification link is clicked, so the
    # grant is inert until the address is proven.
    assert invited.is_active is False
    assert EmailVerificationToken.objects.filter(user=invited, is_used=False).exists()
    assert response.data["id"] == invited.pk


@pytest.mark.django_db
def test_inviting_an_existing_user_grants_without_creating_a_second_account():
    """The other branch of the shared account path.

    A person who already has an account keeps it — same id, same password, same
    verified state — and simply gains the grant. Creating a second account for
    an address that already has one is how a support engineer ends up unable to
    log in to the rights they were given.
    """
    inviter = _administrator()
    existing = _user()

    response = _api(inviter).post(INVITE_URL, {"email": existing.email}, format="json")

    assert response.status_code == 201, response.data
    existing.refresh_from_db()
    assert existing.is_superuser is True
    assert existing.is_active is True
    assert User.objects.filter(email__iexact=existing.email).count() == 1
    # No account was created, so nothing should have been mailed a verification.
    assert not EmailVerificationToken.objects.filter(user=existing).exists()


@pytest.mark.django_db
def test_a_grant_is_recorded_with_who_granted_it_to_whom():
    """The audit trail, which is the half of #358 that is not a CRUD screen.

    Impersonating an organisation already writes an ``ImpersonationSession``
    row. Granting somebody the ability to read and write every organisation must
    not be the less traceable of the two.
    """
    inviter = _administrator()
    existing = _user()

    _api(inviter).post(INVITE_URL, {"email": existing.email}, format="json")

    change = PlatformAdminChange.objects.get(subject=existing, action=PlatformAdminChange.ACTION_GRANTED)
    assert change.actor_id == inviter.pk
    assert change.actor_username == inviter.username
    assert change.subject_username == existing.username
    assert change.subject_email == existing.email
    assert change.changed_at is not None


@pytest.mark.django_db
def test_the_list_says_who_granted_each_administrator_and_when():
    """ "Who they are, when granted, by whom" — read back through the endpoint.

    Asserted against the *list* rather than the audit table because the list is
    what the ticket asks for and what the sidebar will render; the table being
    right is no use if the endpoint does not surface it.
    """
    inviter = _administrator()
    existing = _user()
    _api(inviter).post(INVITE_URL, {"email": existing.email}, format="json")

    response = _api(inviter).get(ADMINS_URL)

    assert response.status_code == 200, response.data
    rows = {row["id"]: row for row in _rows(response)}
    assert set(rows) == {inviter.pk, existing.pk}

    granted = rows[existing.pk]
    assert granted["granted_at"] is not None
    assert granted["granted_by"] == {"id": inviter.pk, "username": inviter.username}
    assert granted["email"] == existing.email


@pytest.mark.django_db
def test_an_administrator_made_in_the_shell_is_listed_with_no_grant_recorded():
    """Null is the honest answer for the administrators that predate #358.

    Every existing superuser was made with ``createsuperuser`` and there is no
    record of who did it. The list must still show them — they hold the rights —
    and must not invent a granter or reuse ``date_joined`` as the grant date.
    """
    shell_made = _administrator()

    response = _api(shell_made).get(ADMINS_URL)

    assert response.status_code == 200, response.data
    row = next(r for r in _rows(response) if r["id"] == shell_made.pk)
    assert row["granted_at"] is None
    assert row["granted_by"] is None


@pytest.mark.django_db
def test_revoking_removes_the_grant_and_records_who_removed_it():
    """The rights are gone, the person is listed no more, and the row says who.

    Both halves are asserted through the API: a revoke that cleared the flag but
    left the person on the list would be a control that is invisible to the only
    screen anyone will look at.
    """
    actor = _administrator()
    target = _administrator()

    response = _api(actor).delete(_detail_url(target.pk))

    assert response.status_code == 200, response.data
    target.refresh_from_db()
    assert target.is_superuser is False

    change = PlatformAdminChange.objects.get(subject=target, action=PlatformAdminChange.ACTION_REVOKED)
    assert change.actor_id == actor.pk

    listed = [row["id"] for row in _rows(_api(actor).get(ADMINS_URL))]
    assert target.pk not in listed
    assert actor.pk in listed


@pytest.mark.django_db
def test_revoking_leaves_the_account_and_every_organisation_membership_intact():
    """The refusal the route's shape invites: ``DELETE`` on a user id.

    Platform administration and organisation membership are independent. The
    person losing platform rights may be the owner of their own organisation,
    and destroying that to clear a boolean would delete a customer's data. The
    membership is checked field by field rather than by existence, because
    "still there but deactivated" would be the same outage for them.
    """
    from tenants.models import TenantUser

    actor = _administrator()
    target = _administrator()
    org = _tenant("client")
    membership = _member(org, target, "owner")

    response = _api(actor).delete(_detail_url(target.pk))

    assert response.status_code == 200, response.data
    assert User.objects.filter(pk=target.pk).exists()

    membership.refresh_from_db()
    assert membership.is_active is True
    assert membership.tenant_id == org.pk
    assert membership.role.slug == "owner"
    assert TenantUser.objects.filter(user=target).count() == 1


@pytest.mark.django_db
def test_a_revoked_person_can_be_granted_again_and_the_history_keeps_both():
    """Rights come back, and the audit trail is not overwritten by the new grant.

    This is why the audit table is an append-only event log rather than one
    mutable row per administrator: the first grant and its revocation are things
    that happened, and somebody may have to account for them long after the
    second grant.
    """
    actor = _administrator()
    target = _administrator()

    assert _api(actor).delete(_detail_url(target.pk)).status_code == 200
    regrant = _api(actor).post(INVITE_URL, {"email": target.email}, format="json")

    assert regrant.status_code == 201, regrant.data
    target.refresh_from_db()
    assert target.is_superuser is True

    history = list(PlatformAdminChange.objects.filter(subject=target).order_by("id").values_list("action", flat=True))
    assert history == [PlatformAdminChange.ACTION_REVOKED, PlatformAdminChange.ACTION_GRANTED]


# ─────────────────────────────────────────────────────────────────────────────
# 2. The floor: the platform can never be left without an administrator
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_last_administrator_cannot_revoke_themselves():
    """The one failure with no recovery except a shell on the production host.

    Nothing in this API can create a platform administrator without an existing
    one, so an empty set is terminal. The flag is asserted still set afterwards,
    not just the 400: a refusal that arrived after the ``save()`` would be a
    lockout with a polite error message.
    """
    only_one = _administrator()

    response = _api(only_one).delete(_detail_url(only_one.pk))

    assert response.status_code == 400, response.data
    only_one.refresh_from_db()
    assert only_one.is_superuser is True
    assert not PlatformAdminChange.objects.filter(action=PlatformAdminChange.ACTION_REVOKED).exists()


@pytest.mark.django_db
def test_the_last_usable_administrator_cannot_be_revoked_even_when_another_row_exists():
    """An unverified administrator is not a fallback.

    An account with ``is_active=False`` cannot obtain a token at all, so leaving
    it as the sole remaining administrator is the same lockout as leaving none —
    reached through a loophole rather than head-on. Counting only usable
    administrators is what closes it, and this is the test that says the
    ``is_active`` in that count is deliberate.
    """
    active = _administrator()
    _administrator(is_active=False)

    response = _api(active).delete(_detail_url(active.pk))

    assert response.status_code == 400, response.data
    active.refresh_from_db()
    assert active.is_superuser is True


@pytest.mark.django_db
def test_an_administrator_may_revoke_themselves_while_another_one_remains():
    """The floor is a floor, not a ban on standing down.

    Somebody leaving the team revokes their own access, and that is the ordinary
    case. Pinned so a fix for the rule above cannot become "nobody may ever
    revoke themselves", which would send that person back to the shell.
    """
    leaving = _administrator()
    staying = _administrator()

    response = _api(leaving).delete(_detail_url(leaving.pk))

    assert response.status_code == 200, response.data
    leaving.refresh_from_db()
    staying.refresh_from_db()
    assert leaving.is_superuser is False
    assert staying.is_superuser is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. Only a platform administrator may grant platform administration
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_organisation_owner_cannot_list_platform_administrators():
    """No tenant role reaches this at any priority — OWNER is the highest there is.

    ``TenantRolePermission`` is not among this viewset's permission classes at
    all, so there is no permission key an organisation could be granted that
    would open it. Listing is refused as well as writing: the roster of who can
    read every customer's data is not something one customer's owner should be
    able to enumerate.
    """
    org = _tenant("client")
    owner = _user()
    _member(org, owner, "owner")
    _administrator()

    response = _api(owner).get(ADMINS_URL)

    assert response.status_code == 403, response.data


@pytest.mark.django_db
def test_an_organisation_owner_cannot_grant_platform_administration():
    """The escalation this endpoint would be if the guard were a tenant role.

    Asserted on the database, not the status code: the owner must not have been
    refused *after* an account was created for the address they named.
    """
    org = _tenant("client")
    owner = _user()
    _member(org, owner, "owner")
    payload = _invite_payload()

    response = _api(owner).post(INVITE_URL, payload, format="json")

    assert response.status_code == 403, response.data
    assert not User.objects.filter(email=payload["email"]).exists()
    assert not PlatformAdminChange.objects.exists()


@pytest.mark.django_db
def test_an_organisation_owner_cannot_revoke_a_platform_administrator():
    """The other direction: a customer must not be able to unseat the platform."""
    org = _tenant("client")
    owner = _user()
    _member(org, owner, "owner")
    administrator = _administrator()

    response = _api(owner).delete(_detail_url(administrator.pk))

    assert response.status_code == 403, response.data
    administrator.refresh_from_db()
    assert administrator.is_superuser is True


@pytest.mark.django_db
def test_a_user_with_no_organisation_and_no_rights_reaches_none_of_it():
    """ "Holds no membership" is not the privilege — being an administrator is.

    A freshly signed-up user belongs to no organisation, which is the same shape
    as a platform operator in every respect except the one that counts. #356
    needed this test for the member endpoint; it needs it more here.
    """
    nobody = _user()
    _administrator()

    assert _api(nobody).get(ADMINS_URL).status_code == 403
    assert _api(nobody).post(INVITE_URL, _invite_payload(), format="json").status_code == 403


@pytest.mark.django_db
def test_an_anonymous_caller_reaches_none_of_it():
    """No credential at all: 401, not a 403 and certainly not a list."""
    _administrator()
    anonymous = APIClient()

    assert anonymous.get(ADMINS_URL).status_code == 401
    assert anonymous.post(INVITE_URL, _invite_payload(), format="json").status_code == 401


@pytest.mark.django_db
def test_a_superuser_who_belongs_to_an_organisation_is_refused_here():
    """#352's rule, applied to the platform surface — and a real trade-off.

    Holding a membership means acting as that organisation's user, which is why
    ``acting_as_platform_operator`` answers False for them. The consequence is
    that an operator who added themselves to a customer's organisation to debug
    it must remove themselves again before they can grant platform access. That
    is the same trade #353 and #356 already make; the alternative is this file
    re-deriving "who is a platform operator" and drifting from them, which is
    the defect both of those tickets were.
    """
    org = _tenant("client")
    embedded = _administrator()
    _member(org, embedded, "owner")
    other = _administrator()

    assert _api(embedded).get(ADMINS_URL).status_code == 403
    assert _api(embedded).delete(_detail_url(other.pk)).status_code == 403
    other.refresh_from_db()
    assert other.is_superuser is True


@pytest.mark.django_db
def test_a_revoked_administrator_cannot_grant_themselves_back_with_the_token_they_still_hold():
    """Revocation has to bite before the token expires, or it does not bite.

    ``CustomJWTAuthentication`` stamps ``is_superuser`` from the token claim and
    an ordinary access token lives ninety days, so without a database read the
    very first thing a revoked administrator could do is re-grant themselves and
    make the revocation permanent in the audit trail only. This is the one test
    that goes through a real JWT rather than ``force_authenticate``, because the
    stale claim is the whole point of it.
    """
    revoked = _administrator()
    api = _bearer(revoked, is_superuser=True)

    revoked.is_superuser = False
    revoked.save(update_fields=["is_superuser"])

    response = api.post(INVITE_URL, {"email": revoked.email}, format="json")

    assert response.status_code == 403, response.data
    revoked.refresh_from_db()
    assert revoked.is_superuser is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Impersonation stays read-only — and here, not even read (#300 / #326 / #344)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_impersonated_session_cannot_list_platform_administrators():
    """The one #300 does *not* already cover, which is why it is first.

    #300 refuses non-safe methods from a borrowed token; a ``GET`` is safe and
    sails through it. What stops this is ``acting_as_platform_operator``, which
    answers False while impersonating — so a read-only session opened to help
    one customer cannot enumerate everyone who can read all of them.
    """
    org = _tenant("viewed")
    actor = _administrator()

    response = _impersonating(actor, org).get(ADMINS_URL)

    assert response.status_code == 403, response.data


@pytest.mark.django_db
def test_an_impersonated_session_cannot_grant_platform_administration():
    """A borrowed token keeps ``is_superuser`` true and holds no membership —
    the exact shape of a platform operator, which is what makes this the
    endpoint most likely to be handed away by accident.

    #300 refuses the write at two independent layers before any of this file's
    code runs. The point is that it keeps arriving, and that nothing was
    created on the way to the 403.
    """
    org = _tenant("viewed")
    actor = _administrator()
    payload = _invite_payload()

    response = _impersonating(actor, org).post(INVITE_URL, payload, format="json")

    assert response.status_code == 403, response.data
    assert not User.objects.filter(email=payload["email"]).exists()
    assert not PlatformAdminChange.objects.exists()


@pytest.mark.django_db
def test_an_impersonated_session_cannot_revoke_platform_administration():
    """And the destructive direction: a borrowed token must not unseat anyone."""
    org = _tenant("viewed")
    actor = _administrator()
    target = _administrator()

    response = _impersonating(actor, org).delete(_detail_url(target.pk))

    assert response.status_code == 403, response.data
    target.refresh_from_db()
    assert target.is_superuser is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. Ordinary refusals
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_granting_to_someone_who_already_has_it_is_a_conflict():
    """A repeated invite must not be indistinguishable from the first one.

    Answering 201 would tell an operator they had just granted access when the
    person already had it — and would write a second grant row implying somebody
    took an action they did not take. ``add_member`` answers a repeated invite
    409 for the same reason.
    """
    actor = _administrator()
    already = _administrator()

    response = _api(actor).post(INVITE_URL, {"email": already.email}, format="json")

    assert response.status_code == 409, response.data
    assert not PlatformAdminChange.objects.filter(subject=already).exists()


@pytest.mark.django_db
def test_an_invite_for_an_unknown_address_needs_a_password_and_a_name():
    """The new-account branch cannot be taken with half a body.

    Asserted with the row count as well as the 400: a refusal that arrived after
    ``User.objects.create`` would leave an account nobody can log in to and
    nobody knows about.
    """
    actor = _administrator()

    response = _api(actor).post(INVITE_URL, {"email": "nobody@test.com"}, format="json")

    assert response.status_code == 400, response.data
    assert "password" in response.data, response.data
    assert not User.objects.filter(email="nobody@test.com").exists()


@pytest.mark.django_db
def test_a_weak_password_is_refused_by_the_same_rule_the_member_invite_uses():
    """The shared password rule, asserted on the newer surface.

    It was moved out of ``AddMemberSerializer`` so there is one copy rather than
    two; this is the test that the platform invite is actually using it and did
    not end up with the weaker rule that a second copy eventually becomes.
    """
    actor = _administrator()
    payload = _invite_payload()
    payload["password"] = "weak"  # noqa: S105 — deliberately below the rule

    response = _api(actor).post(INVITE_URL, payload, format="json")

    assert response.status_code == 400, response.data
    assert "password" in response.data, response.data
    assert not User.objects.filter(email=payload["email"]).exists()


@pytest.mark.django_db
def test_revoking_someone_who_is_not_an_administrator_is_a_404():
    """The collection is administrators, so a non-administrator is not in it.

    A 404 rather than a 400 also means this endpoint cannot be used to ask
    whether an arbitrary user id exists.
    """
    actor = _administrator()
    ordinary = _user()

    response = _api(actor).delete(_detail_url(ordinary.pk))

    assert response.status_code == 404, response.data
    ordinary.refresh_from_db()
    assert ordinary.is_superuser is False


@pytest.mark.django_db
def test_the_list_holds_only_administrators():
    """An ordinary user is not on the roster.

    Trivial to state and worth pinning: the queryset is ``User``, so a filter
    dropped from it turns the platform-administrator screen into a directory of
    every account in the product, readable by anyone who can open it.
    """
    actor = _administrator()
    ordinary = _user()

    response = _api(actor).get(ADMINS_URL)

    assert response.status_code == 200, response.data
    listed = [row["id"] for row in _rows(response)]
    assert listed == [actor.pk]
    assert ordinary.pk not in listed
