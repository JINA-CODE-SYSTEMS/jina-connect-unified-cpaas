"""Exchange an operator-set temporary password for a real one (#221).

An operator sets the first password when onboarding a tenant, and the account
cannot obtain a token while ``must_change_password`` stands. So the holder
needs a way in that does not require a token — this is it.

Unauthenticated by necessity, and therefore careful:

* the temporary password is verified before anything changes, so this is not
  a way to reset someone else's account
* the new password goes through Django's configured validators
* the response is identical whether the account exists, the password is
  wrong, or the flag was never set, so it cannot be used to enumerate
  accounts or discover which are pending
"""

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction as db_transaction
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from users.models import User

# One message for every failure mode. Distinguishing them would leak which
# usernames exist and which accounts are awaiting a first login.
_REJECTION = "Those credentials are not valid, or this account is not awaiting a first password."


class SetInitialPasswordSerializer(serializers.Serializer):
    username = serializers.CharField(help_text="Username or email the operator was given.")
    temporary_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)


class SetInitialPasswordViewSet(viewsets.ViewSet):
    """POST /users/set-initial-password/ — replace a temporary password."""

    permission_classes = [AllowAny]
    serializer_class = SetInitialPasswordSerializer

    def create(self, request):
        serializer = SetInitialPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        username = data["username"]
        user = authenticate(request, username=username, password=data["temporary_password"])
        if user is None and "@" in username:
            # The operator may have handed over an email rather than a username.
            match = User.objects.filter(email__iexact=username).first()
            if match:
                user = authenticate(request, username=match.username, password=data["temporary_password"])

        if user is None or not user.must_change_password:
            return Response({"detail": _REJECTION}, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_password(data["new_password"], user=user)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        if user.check_password(data["new_password"]):
            return Response(
                {"new_password": ["The new password must differ from the temporary one."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with db_transaction.atomic():
            user.set_password(data["new_password"])
            user.must_change_password = False
            user.save(update_fields=["password", "must_change_password"])

        return Response(
            {"detail": "Password set. You can sign in now."},
            status=status.HTTP_200_OK,
        )
