"""The token says who is signed in (#header reads "User" for everyone).

The web app takes the name in its header from a `name` claim on the access
token. No such claim was ever written, so every operator and every tenant user
saw the literal fallback — "User", with the initial "U" — whoever they were.

The fallback chain is the point, not the happy path: most accounts in this
product are created by an operator through an invite, which sets an email and
often no first or last name at all. A test that only covers "user with a full
name" would have passed against the broken code for the accounts that matter.
"""

import uuid

import pytest

from users.models import User
from users.serializers import JwtUserSerializer


def _claims(user):
    return JwtUserSerializer(context={}).get_token(user)


def _user(**over):
    suffix = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        username=over.pop("username", f"user-{suffix}"),
        email=over.pop("email", f"user-{suffix}@example.com"),
        **over,
    )


@pytest.mark.django_db()
def test_a_full_name_is_what_the_header_shows():
    user = _user(first_name="Grace", last_name="Hopper")

    assert _claims(user)["name"] == "Grace Hopper"


@pytest.mark.django_db()
def test_no_name_falls_back_to_the_username_not_a_placeholder():
    """The common case: an invited account with an address and nothing else.

    The username is at least *this person*, and here it is usually their email
    address. "User" answers a different question than the one an operator
    glancing at the header is asking.
    """
    user = _user(username="admin", first_name="", last_name="")

    assert _claims(user)["name"] == "admin"


@pytest.mark.django_db()
def test_a_first_name_alone_does_not_arrive_with_a_dangling_space():
    """`get_full_name` joins on a space, so a missing surname leaves one behind.

    It would be invisible in the header and wrong in the initials.
    """
    user = _user(first_name="Grace", last_name="")

    name = _claims(user)["name"]
    assert name == "Grace"
    assert name == name.strip()


@pytest.mark.django_db()
def test_the_address_travels_too():
    user = _user(email="grace@example.com")

    assert _claims(user)["email"] == "grace@example.com"


@pytest.mark.django_db()
def test_an_account_with_no_address_still_produces_a_usable_token():
    """Older accounts predate the address being required; None must not ship."""
    user = _user()
    user.email = ""
    user.save(update_fields=["email"])

    claims = _claims(user)
    assert claims["email"] == ""
    assert claims["name"] == user.username
