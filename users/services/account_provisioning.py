"""The one way an operator creates an account for somebody else (#358).

Every "invite by email" in the product has the same two branches: the address
already belongs to a ``User``, or it does not and an account has to be created
unverified and mailed a verification link. ``add_member_to_tenant`` grew that
logic first, for organisation members. #358 needs it again for platform
administrators, and the ticket is explicit that it must **reuse** the existing
account-and-verification path rather than stand up a second one.

So the shared parts live here and both callers ask for them, instead of the
platform side copying eighty lines that would then drift. That drift is not
hypothetical in this codebase: #353 and #356 were both one question answered in
two places, and the answers diverged. An invite path that diverged would be
worse than a role check that diverged — one copy would send the verification
mail and the other would quietly leave an account that can never be logged in
to, or leave the password rule weaker on the newer surface.

What is deliberately *not* here: the decision of what the new account is then
entitled to. A member gets a ``TenantUser`` row, a platform administrator gets
``is_superuser``, and neither of those belongs to account creation.
"""

import logging
import re

from rest_framework import serializers

from users.models import EmailVerificationToken, User

logger = logging.getLogger(__name__)


def validate_password_strength(value: str) -> str:
    """The password rule applied to every operator-set password.

    Raises ``rest_framework.serializers.ValidationError`` so it can be used
    directly as a serializer field validator, which is where both callers use
    it. Lifted out of ``AddMemberSerializer`` unchanged, messages included:
    the point of moving it is that the platform-administrator invite cannot end
    up with a *weaker* rule than the member invite by being written separately,
    and a rule that is a copy is a rule that can weaken.
    """
    if len(value) < 8:
        raise serializers.ValidationError("Password must be at least 8 characters.")
    if not re.search(r"[A-Z]", value):
        raise serializers.ValidationError("Password must contain at least one uppercase letter.")
    if not re.search(r"[0-9]", value):
        raise serializers.ValidationError("Password must contain at least one digit.")
    if not re.search(r"[^A-Za-z0-9]", value):
        raise serializers.ValidationError("Password must contain at least one special character.")
    return value


def find_user_by_email(email: str):
    """The existing account for ``email``, or ``None``.

    Case-insensitive, because an invite is typed by a human and ``User.email``
    is not normalised on the way in for accounts created before it was.
    """
    return User.objects.filter(email__iexact=email).first()


def create_pending_user(*, email, password, first_name, last_name=""):
    """Create an account that cannot yet be used, and return it.

    ``is_active=False`` is the whole point: the account exists so the grant or
    membership has something to attach to, and the holder of the address proves
    they hold it before anything can be done with it. Nothing here sends the
    mail — see ``send_account_verification`` — because the two callers create
    different rows in between and both want them created before an email goes
    out promising the invitee something.

    Raises ``ValueError`` for the two fields that only matter on this branch, so
    a caller that reached here without validating gets a refusal rather than an
    account with no password. Messages are ``add_member_to_tenant``'s, verbatim.
    """
    if not password:
        raise ValueError("Password is required for new users.")
    if not first_name:
        raise ValueError("First name is required for new users.")

    return User.objects.create(
        username=email,  # Use email as username
        email=email,
        first_name=first_name,
        last_name=last_name or "",
        password=password,  # User.save() auto-hashes via identify_hasher
        is_active=False,  # Pending email verification
        # ``mobile`` is deliberately not passed, and that is correct rather
        # than the bug it used to be. It is unique, so "no number known" must
        # be NULL — two NULLs do not collide in Postgres, two empty strings do,
        # which is why the *second* account ever created this way used to die
        # on ``users_user_mobile_key`` (#360). Since that made the column
        # nullable, Django's default for an omitted value resolves to None;
        # passing ``mobile=None`` here would say the same thing twice. What
        # makes it true is the model, not this call.
    )


def send_account_verification(user):
    """Issue a fresh verification token for ``user`` and mail it.

    Swallows a send failure, deliberately and as ``add_member_to_tenant``
    already did: the account and whatever it was created for are already
    committed, an SMTP outage is not the invitee's problem to discover as a 500,
    and ``resend-verification`` exists for exactly this. The token is created
    either way, so a resend is not required to make the invite work — only to
    deliver it again.

    Returns the token so a caller can tell whether one was issued.
    """
    token = EmailVerificationToken.create_for_user(user)
    try:
        from users.services.email_verification import EmailVerificationService

        EmailVerificationService.send_verification_email(user, token)
    except Exception:
        logger.exception("Failed to send verification email to %s", user.email)
    return token
