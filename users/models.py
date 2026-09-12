import secrets

from django.contrib.auth.hashers import identify_hasher, make_password
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from phonenumber_field.modelfields import PhoneNumberField


def validate_address(value):
    """
    Validate address JSON structure.

    Expected format:
    {
        "country": "United States",
        "city": "Phoenix",
        "state": "Arizona",
        "landmark": "",
        "postal_code": "201301"
    }
    """
    if value is None:
        return  # Allow null values

    if not isinstance(value, dict):
        raise ValidationError("Address must be a JSON object.")

    required_fields = {"country", "city", "state", "landmark", "postal_code"}
    allowed_fields = required_fields

    # Check for unknown fields
    unknown_fields = set(value.keys()) - allowed_fields
    if unknown_fields:
        raise ValidationError(f"Unknown fields in address: {', '.join(unknown_fields)}")

    # Check for required fields
    missing_fields = required_fields - set(value.keys())
    if missing_fields:
        raise ValidationError(f"Missing required fields in address: {', '.join(missing_fields)}")

    # Validate field types (all should be strings)
    for field in required_fields:
        if not isinstance(value.get(field), str):
            raise ValidationError(f"Address field '{field}' must be a string.")


class User(AbstractUser):
    """
    User model extending AbstractUser to include additional fields.
    Attributes:
        mobile (CharField): mobile number of the user.
        gender (CharField): Optional gender of the user, with choices defined in GENDER_CHOICES.
        profile_picture (ImageField): Optional profile picture of the user, stored in "profile_pictures".
        birth_date (DateField): Optional birth date of the user.
    Methods:
        get_full_name() -> str:
            Returns the full name of the user by combining first name and last name.
    """

    # The region is deliberately not pinned here. It comes from
    # settings.PHONENUMBER_DEFAULT_REGION so a white-label deployment can
    # accept local numbers in national format. Keeping it as a field kwarg
    # would bake it into the migration, and any deployment with a different
    # region would then fail `makemigrations --check`.
    mobile = PhoneNumberField(unique=True)
    image = models.ImageField(upload_to="user_images/", blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)

    # Set when an operator creates the account with a temporary password
    # (#221). While it is true the account cannot obtain a token — see
    # JwtUserSerializer — so a temporary password cannot quietly become the
    # permanent one. The holder clears it via /users/set-initial-password/,
    # which takes the temporary password and does not need a token.
    must_change_password = models.BooleanField(
        default=False,
        help_text="The password was set by an operator and must be replaced before the account can be used.",
    )
    address = models.JSONField(
        blank=True,
        null=True,
        validators=[validate_address],
        help_text="JSON object with fields: country, city, state, landmark, postal_code",
    )

    # Required fields for creating superuser via command line
    REQUIRED_FIELDS = ["email", "mobile"]

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        abstract = False

    def save(self, *args, **kwargs):
        if self.password:
            try:
                # 🔐 If already hashed, this will succeed
                identify_hasher(self.password)
            except ValueError:
                # 🚀 If not hashed yet, hash it safely
                self.password = make_password(self.password)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.username

    @property
    def full_name(self) -> str:
        """
        Returns the full name of the user by combining first name and last name.
        Returns:
            str: The full name of the user.
        """
        return f"{self.first_name} {self.last_name}"

    @property
    def tenant(self):
        """Return the tenant associated with the user, if any."""
        if hasattr(self, "user_tenants"):
            if self.user_tenants.exists():
                return self.user_tenants.first().tenant
        return None


class EmailVerificationToken(models.Model):
    """
    Model to store email verification tokens for user registration.
    Tokens expire after 24 hours.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="verification_tokens")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Email Verification Token"
        verbose_name_plural = "Email Verification Tokens"

    def __str__(self):
        return f"Verification token for {self.user.email}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(48)
        if not self.expires_at:
            # Token expires in 24 hours
            self.expires_at = timezone.now() + timezone.timedelta(hours=24)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        """Check if token has expired."""
        return timezone.now() > self.expires_at

    @property
    def is_valid(self):
        """Check if token is valid (not expired and not used)."""
        return not self.is_expired and not self.is_used

    @classmethod
    def create_for_user(cls, user):
        """Create a new verification token for a user, invalidating any existing ones."""
        # Mark existing tokens as used
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        # Create new token
        return cls.objects.create(user=user)

    def verify(self):
        """Mark token as used and activate the user."""
        if not self.is_valid:
            return False

        self.is_used = True
        self.save()

        # Activate user
        self.user.is_active = True
        self.user.save()

        return True


class ImpersonationSession(models.Model):
    """Audit record of one "view as organisation" session (#300).

    Lives in ``users`` rather than ``tenants`` because the subject of the record
    is a *platform* user's action, not an organisation's data. Every tenant
    model in ``tenants`` inherits the tenant-scoped manager
    (``filter_by_user_tenant``) and is reachable through tenant-scoped
    endpoints; an organisation being able to enumerate which of our staff
    looked at it is a different feature with a different threat model, and a
    record the organisation can reach is a record it can be given reason to
    dispute. The FK points across app boundaries by name so ``users`` still
    does not import ``tenants``.

    Each row is the *authority* for a session, not a note about it:
    ``users.impersonation.live_session_for`` refuses a token whose row is
    missing, ended or expired. So a token cannot exist without an audit row
    naming the real user, and exiting a session takes the token away for real
    rather than only in the client.

    ``actor`` and ``tenant`` are both SET_NULL with the name kept alongside:
    the audit trail has to outlive the deletion of an employee account or an
    organisation, and "who" is the part of it that matters.
    """

    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="impersonation_sessions",
        help_text="The real platform user who started the session.",
    )
    actor_username = models.CharField(
        max_length=150,
        help_text="Username of the real user at the time the session started — kept if the account is deleted.",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="impersonation_sessions",
        help_text="The organisation that was viewed.",
    )
    tenant_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name of the organisation at the time the session started.",
    )
    token_jti = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="JTI of the issued access token. One row per token, so a session is never ambiguous.",
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(help_text="When the issued token expires on its own.")
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the session was exited, if it was exited before expiry.",
    )

    class Meta:
        verbose_name = "Impersonation Session"
        verbose_name_plural = "Impersonation Sessions"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.actor_username} → {self.tenant_name} at {self.started_at:%Y-%m-%d %H:%M}"

    @classmethod
    def start(cls, *, actor, tenant, token_jti, expires_at):
        """Record a session, snapshotting the names that must survive deletion."""
        return cls.objects.create(
            actor=actor,
            actor_username=actor.username,
            tenant=tenant,
            tenant_name=tenant.name,
            token_jti=token_jti,
            expires_at=expires_at,
        )

    @property
    def is_live(self):
        """Whether the session may still be used: not exited, not yet expired."""
        return self.ended_at is None and timezone.now() < self.expires_at

    def end(self):
        """Close the session. Idempotent — the first end is the one recorded."""
        if self.ended_at is None:
            self.ended_at = timezone.now()
            self.save(update_fields=["ended_at"])
        return self.ended_at


class PasswordResetToken(models.Model):
    """
    Model to store password reset tokens.
    Tokens expire after 1 hour for security.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Password Reset Token"
        verbose_name_plural = "Password Reset Tokens"

    def __str__(self):
        return f"Password reset token for {self.user.email}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(48)
        if not self.expires_at:
            # Token expires in 1 hour (shorter for security)
            self.expires_at = timezone.now() + timezone.timedelta(hours=1)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        """Check if token has expired."""
        return timezone.now() > self.expires_at

    @property
    def is_valid(self):
        """Check if token is valid (not expired and not used)."""
        return not self.is_expired and not self.is_used

    @classmethod
    def create_for_user(cls, user):
        """Create a new password reset token for a user, invalidating any existing ones."""
        # Mark existing tokens as used
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        # Create new token
        return cls.objects.create(user=user)

    def use_token(self):
        """Mark token as used."""
        if not self.is_valid:
            return False

        self.is_used = True
        self.save()
        return True
