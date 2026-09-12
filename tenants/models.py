import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac
from djmoney.models.fields import MoneyField
from simple_history.models import HistoricalRecords

from abstract.models import BaseEntity, BaseModelWithOwner, BaseTenantModelForFilterUser, BaseWallet
from abstract.validator import validate_phone_with_series
from jina_connect.platform_choices import PlatformChoices  # canonical source
from tenants.managers import TenantManager
from users.models import User


class IndustryChoices(models.TextChoices):
    """Industry choices for tenant onboarding"""

    ECOMMERCE = "ecommerce", "E-commerce & Retail"
    HEALTHCARE = "healthcare", "Healthcare & Medical"
    EDUCATION = "education", "Education & E-learning"
    FINANCE = "finance", "Finance & Banking"
    REAL_ESTATE = "real_estate", "Real Estate"
    TRAVEL = "travel", "Travel & Hospitality"
    FOOD = "food", "Food & Restaurant"
    AUTOMOTIVE = "automotive", "Automotive"
    LOGISTICS = "logistics", "Logistics & Transportation"
    ENTERTAINMENT = "entertainment", "Entertainment & Media"
    TECHNOLOGY = "technology", "Technology & Software"
    MANUFACTURING = "manufacturing", "Manufacturing"
    CONSULTING = "consulting", "Consulting & Professional Services"
    NONPROFIT = "nonprofit", "Non-profit & NGO"
    GOVERNMENT = "government", "Government & Public Sector"
    INSURANCE = "insurance", "Insurance"
    TELECOM = "telecom", "Telecommunications"
    AGRICULTURE = "agriculture", "Agriculture"
    ENERGY = "energy", "Energy & Utilities"
    FASHION = "fashion", "Fashion & Apparel"
    SPORTS = "sports", "Sports & Fitness"
    BEAUTY = "beauty", "Beauty & Personal Care"
    LEGAL = "legal", "Legal Services"
    HR = "hr", "HR & Recruitment"
    MARKETING = "marketing", "Marketing & Advertising"
    OTHER = "other", "Other"


# Create your models here.
class Tenant(BaseEntity, BaseWallet, BaseTenantModelForFilterUser):
    """
    Tenant model represents a tenant with attributes such as name and description.
    """

    # Location fields (ISO 3166 codes)
    # Set when the account is archived and its customer data purged. The row
    # itself must survive: it is the billing record for the Active Customer
    # Account report (partner agreement Cl. 4.2), which is pro-rated by the
    # days an account existed. A hard delete destroys that evidence.
    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When this account was archived. Null means the account is live.",
    )

    country = models.CharField(
        max_length=2, blank=True, null=True, help_text="ISO 3166-1 alpha-2 country code (e.g., IN, US)"
    )
    state = models.CharField(
        max_length=10, blank=True, null=True, help_text="ISO 3166-2 subdivision code (e.g., MH, CA)"
    )

    # Industry classification
    industry = models.CharField(
        max_length=50, choices=IndustryChoices.choices, blank=True, null=True, help_text="Industry/sector of the tenant"
    )

    filter_by_user_tenant_fk = "tenant_users__user"

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"

    objects: TenantManager = TenantManager()

    @property
    def products(self):
        """
        Returns a list of products using this tenant.
        """
        # check if tenant_gupshup apps exist
        products = []
        if self.wa_apps.exists():
            products.append("WhatsApp")
        return products

    @property
    def contact_count(self):
        """
        Returns the count of contacts associated with this tenant.
        """
        if hasattr(self, "contacts"):
            return self.contacts.count()
        return 0

    @property
    def is_archived(self):
        """True once the account has been archived."""
        return self.archived_at is not None

    def archive(self, when=None):
        """Mark the account archived.

        Stamps archived_at so the Active Customer Account report can pro-rate
        the month the account went away. Purging the account's customer data is
        a separate concern; this row is retained deliberately as the billing
        record. Re-archiving keeps the original timestamp.
        """
        if self.archived_at is None:
            self.archived_at = when or timezone.now()
            self.save(update_fields=["archived_at", "updated_at"])
        return self.archived_at


class BSPChoices(models.TextChoices):
    """Enum for Business Solution Providers (BSPs)"""

    GUPSHUP = "GUPSHUP", "Gupshup"
    META = "META", "Meta"
    TWILIO = "TWILIO", "Twilio"
    MESSAGEBIRD = "MESSAGEBIRD", "MessageBird"
    WATI = "WATI", "WATI"
    AISENSY = "AISENSY", "Aisensy"
    INTERAKT = "INTERAKT", "Interakt"
    YELLOW_AI = "YELLOW_AI", "Yellow.ai"
    GOOGLE_RBM = "GOOGLE_RBM", "Google RBM"
    META_RCS = "META_RCS", "Meta RCS"


class TenantWAApp(BaseTenantModelForFilterUser):
    """
    Model to store Gupshup app details for a tenant.
    Attributes:
        tenant (ForeignKey): Reference to the Tenant model.
        app_name (CharField): Name of the Gupshup app.
        app_id (CharField): Gupshup app ID.
        app_secret (CharField): Gupshup app secret.
        meta_app_id (CharField): META App ID, when bsp=META.
        wa_number (CharField): WhatsApp number associated with the app.
        authentication_message_price (MoneyField): Price for authentication messages.
        marketing_message_price (MoneyField): Price for marketing messages.
        utility_message_price (MoneyField): Price for utility messages.
        esf_url (URLField): Embedded Signup Flow URL for WhatsApp onboarding.
        esf_url_expires_at (DateTimeField): Expiration time for ESF URL (valid for 4 days).
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="wa_apps")
    app_name = models.CharField(max_length=1024)
    app_id = models.CharField(max_length=1024)
    app_secret = models.CharField(max_length=1024)
    wa_number = models.CharField(
        max_length=15,
        validators=[validate_phone_with_series],
        help_text="Enter in international format, e.g., +14155552671. Do not include spaces or dashes.",
    )
    # Declared in USD because a MoneyField's default_currency is fixed at
    # class-definition time and frozen into the migration — it cannot follow a
    # per-deployment setting. A new app is given its currency at creation
    # instead, exactly as BaseWallet does for wallets (#228, #263).
    authentication_message_price = MoneyField(decimal_places=2, max_digits=15, default=0.1, default_currency="USD")
    marketing_message_price = MoneyField(decimal_places=2, max_digits=15, default=0.1, default_currency="USD")
    utility_message_price = MoneyField(decimal_places=2, max_digits=15, default=0.1, default_currency="USD")

    # Open-session (CSW) rate multiplier — configurable per tenant-BSP.
    # 0.00 = free (Meta doesn't charge for CSW replies — most BSPs)
    # 0.50 = half price, 1.00 = full price (some BSPs still charge)
    open_session_rate_multiplier = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=0,
        help_text=(
            "Fraction of normal rate charged for open-session (CSW) messages. "
            "0.00 = free, 1.00 = full price. Configurable per BSP."
        ),
    )

    # ESF (Embedded Signup Flow) fields
    esf_url = models.URLField(
        max_length=2048, null=True, blank=True, help_text="Embedded Signup Flow URL for WhatsApp onboarding"
    )
    esf_url_expires_at = models.DateTimeField(
        null=True, blank=True, help_text="ESF URL expiration time (valid for 4 days)"
    )

    # BSP Support
    # Defaults to META, matching ``wa.adapters.DEFAULT_BSP``. It defaulted to
    # GUPSHUP, which meant an app created by admin, fixture, data migration or
    # the legacy tenant-gupshup endpoint without an explicit bsp sent every
    # broadcast to partner.gupshup.io and hard-failed on missing credentials —
    # while the adapter factory, reading the same column, returned META
    # Direct (#265). Change this and DEFAULT_BSP together; a test asserts they
    # agree.
    bsp = models.CharField(max_length=20, choices=BSPChoices.choices, default=BSPChoices.META)

    # META identifiers (used when bsp=META)
    waba_id = models.CharField(max_length=100, blank=True, null=True, help_text="WhatsApp Business Account ID")
    phone_number_id = models.CharField(max_length=100, blank=True, null=True, help_text="Phone Number ID from META")
    # ``app_id`` above is the *Gupshup* app ID by definition, but the META
    # Resumable Upload API needs the META App ID and had nowhere else to read
    # it from, so one column carried two providers' identifiers (#275). Nullable
    # and read second so apps configured before this field keep working.
    meta_app_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="META App ID — used for the Resumable Upload API. Falls back to app_id when unset.",
    )

    # Generic BSP credentials (JSON blob for tokens, secrets, etc.)
    bsp_credentials = models.JSONField(blank=True, null=True, help_text="BSP credentials (access tokens, etc.)")

    # Verification & quota
    is_verified = models.BooleanField(default=False, help_text="Phone number verified with META")
    daily_limit = models.IntegerField(default=1000, help_text="Daily message limit")
    messages_sent_today = models.IntegerField(default=0)
    tier = models.CharField(max_length=50, blank=True, null=True, help_text="META messaging tier")

    # Commerce Manager — set to True when Meta Commerce Manager is connected.
    # Controls whether CATALOG and PRODUCT template types are available.
    is_commerce_manager_enabled = models.BooleanField(
        default=False,
        help_text="Whether Meta Commerce Manager is enabled for this WhatsApp Business Account. "
        "When False, CATALOG and PRODUCT template types are disabled.",
    )

    # ── Payment Gateway Configuration ──────────────────────────────────
    is_payment_enabled = models.BooleanField(
        default=False,
        help_text="Whether WhatsApp Payments is enabled for this WABA.",
    )
    payment_gateway = models.CharField(
        max_length=20,
        choices=[
            ("razorpay", "Razorpay"),
            ("payu", "PayU"),
            ("billdesk", "BillDesk"),
            ("zaakpay", "Zaakpay"),
        ],
        blank=True,
        null=True,
    )
    payment_configuration_name = models.CharField(
        max_length=60,
        blank=True,
        null=True,
        help_text="Must match WhatsApp Business Manager config exactly.",
    )
    payment_credentials = models.JSONField(
        blank=True,
        null=True,
        help_text="Payment gateway credentials (API keys, secrets, etc.)",
    )
    auto_send_order_status_on_payment = models.BooleanField(
        default=True,
        help_text="Auto-send order_status 'processing' on payment capture.",
    )

    name = None

    @property
    def phone_number(self):
        """Alias for wa_number — used by v2 adapters/serializers."""
        return self.wa_number

    def __str__(self):
        return f"{self.app_name} ({self.wa_number})"

    @property
    def is_esf_url_valid(self):
        """Check if the ESF URL is still valid (not expired)."""
        if not self.esf_url or not self.esf_url_expires_at:
            return False
        return timezone.now() < self.esf_url_expires_at

    @property
    def is_waba_active(self):
        """
        Check if WABA is active by looking at WABAInfo.account_status.
        Returns True if account_status is 'ACTIVE'.
        """
        try:
            if hasattr(self, "waba_info") and self.waba_info:
                return self.waba_info.account_status == "ACTIVE"
        except WABAInfo.DoesNotExist:
            pass
        return False

    # ── Price currency ───────────────────────────────────────────────────
    # Mirrors BaseWallet._stamp_platform_currency. The three prices are
    # charged against a tenant wallet, so they have to be denominated in the
    # same currency as that wallet or the number means nothing (#263).
    _PLATFORM_STAMPED_PRICE_FIELDS = (
        "authentication_message_price",
        "marketing_message_price",
        "utility_message_price",
    )
    _DECLARED_PRICE_CURRENCY = "USD"

    def _stamp_platform_currency(self):
        """Give a new app's prices the deployment's currency.

        The caller wins: a price passed in some other currency sets the
        currency for all three, with amounts carried across unchanged. That is
        safe at creation, where every amount is a nominal default — it is not
        a conversion and must never become one.
        """
        from django.conf import settings
        from djmoney.money import Money

        target = str(getattr(settings, "PLATFORM_DEFAULT_CURRENCY", self._DECLARED_PRICE_CURRENCY))

        for name in self._PLATFORM_STAMPED_PRICE_FIELDS:
            value = getattr(self, name, None)
            if value is not None and str(value.currency) != self._DECLARED_PRICE_CURRENCY:
                target = str(value.currency)
                break

        for name in self._PLATFORM_STAMPED_PRICE_FIELDS:
            value = getattr(self, name, None)
            if value is not None and str(value.currency) != target:
                setattr(self, name, Money(value.amount, target))

    def save(self, *args, **kwargs):
        if self._state.adding:
            self._stamp_platform_currency()
        super().save(*args, **kwargs)


class TenantVoiceApp(BaseTenantModelForFilterUser):
    """Per-tenant voice channel enablement and defaults.

    Mirrors the ``TenantWAApp`` pattern: one row per tenant, gated by
    ``is_enabled``. Default outbound/inbound configs point at
    ``voice.VoiceProviderConfig`` rows; if either is null the tenant
    has no working default for that direction yet.

    The voice channel is feature-flagged at the tenant level via
    ``is_enabled``. All voice URLs, MCP tools, and admin actions check
    this flag before doing anything.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"
    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="voice_app",
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text="Master switch for the voice channel for this tenant.",
    )
    default_outbound_config = models.ForeignKey(
        "voice.VoiceProviderConfig",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenants_as_default_outbound",
    )
    default_inbound_config = models.ForeignKey(
        "voice.VoiceProviderConfig",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenants_as_default_inbound",
    )
    recording_retention_days = models.IntegerField(
        default=90,
        help_text="Days a recording is kept before retention sweep deletes it.",
    )
    recording_requires_consent = models.BooleanField(
        default=False,
        help_text=(
            "When set, adapters refuse to record a call unless a "
            "``RecordingConsent`` row with ``consent_given=True`` exists "
            "for the contact (#171)."
        ),
    )

    class Meta:
        verbose_name = "Tenant voice app"
        verbose_name_plural = "Tenant voice apps"

    def __str__(self) -> str:
        return f"{self.tenant_id}:voice(enabled={self.is_enabled})"


class WABAInfo(BaseTenantModelForFilterUser):
    """
    Model to store WhatsApp Business Account (WABA) information from API.
    Maintains one-to-one relationship with TenantWAApp and tracks historical changes.
    """

    filter_by_user_tenant_fk = "wa_app__tenant__tenant_users__user"

    class AccountStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        BANNED = "BANNED", "Banned"

    class DockerStatus(models.TextChoices):
        CONNECTED = "CONNECTED", "Connected"
        DISCONNECTED = "DISCONNECTED", "Disconnected"
        FLAGGED = "FLAGGED", "Flagged"
        PENDING = "PENDING", "Pending"
        RESTRICTED = "RESTRICTED", "Restricted"
        UNKNOWN = "UNKNOWN", "Unknown"

    class MessagingLimit(models.TextChoices):
        TIER_50 = "TIER_50", "Tier 50"
        TIER_250 = "TIER_250", "Tier 250"
        TIER_1K = "TIER_1K", "Tier 1K"
        TIER_10K = "TIER_10K", "Tier 10K"
        TIER_100K = "TIER_100K", "Tier 100K"
        TIER_NOT_SET = "TIER_NOT_SET", "Tier Not Set"
        TIER_UNLIMITED = "TIER_UNLIMITED", "Tier Unlimited"

        @classmethod
        def get_limit(cls, tier_name: str) -> int:
            """
            Convert tier name to numeric limit.

            Args:
                tier_name: The tier string (e.g., 'TIER_1K')

            Returns:
                int: The numeric limit (e.g., 1000). Returns 50 for unknown/not-set.
            """
            limits = {
                cls.TIER_50: 50,
                cls.TIER_250: 250,
                cls.TIER_1K: 1000,
                cls.TIER_10K: 10000,
                cls.TIER_100K: 100000,
                cls.TIER_UNLIMITED: float("inf"),
                cls.TIER_NOT_SET: 50,  # Conservative default - new accounts start at TIER_50
            }
            return limits.get(tier_name, 50)  # Default to 50 if unknown

    class MMLiteStatus(models.TextChoices):
        INELIGIBLE = "INELIGIBLE", "Ineligible"
        ELIGIBLE = "ELIGIBLE", "Eligible"
        ONBOARDED = "ONBOARDED", "Onboarded"

    class OwnershipType(models.TextChoices):
        CLIENT_OWNED = "CLIENT_OWNED", "Client Owned"
        ON_BEHALF_OF = "ON_BEHALF_OF", "On Behalf Of"
        SELF = "SELF", "Self"

    class PhoneQuality(models.TextChoices):
        GREEN = "GREEN", "Green"
        YELLOW = "YELLOW", "Yellow"
        RED = "RED", "Red"
        UNKNOWN = "UNKNOWN", "Unknown"

    class Throughput(models.TextChoices):
        HIGH = "HIGH", "High"
        STANDARD = "STANDARD", "Standard"
        NOT_APPLICABLE = "NOT_APPLICABLE", "Not Applicable"

    class CanSendMessage(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        LIMITED = "LIMITED", "Limited"
        BLOCKED = "BLOCKED", "Blocked"

    # One-to-one relationship with TenantWAApp
    wa_app = models.OneToOneField(
        TenantWAApp,
        on_delete=models.CASCADE,
        related_name="waba_info",
        db_column="gupshup_app_id",
        help_text="Associated WA app",
    )

    # WABA Information fields
    account_status = models.CharField(max_length=20, choices=AccountStatus.choices, null=True, blank=True)
    docker_status = models.CharField(max_length=20, choices=DockerStatus.choices, null=True, blank=True)
    messaging_limit = models.CharField(max_length=20, choices=MessagingLimit.choices, null=True, blank=True)
    mm_lite_status = models.CharField(max_length=20, choices=MMLiteStatus.choices, null=True, blank=True)
    ownership_type = models.CharField(max_length=20, choices=OwnershipType.choices, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    phone_quality = models.CharField(max_length=20, choices=PhoneQuality.choices, null=True, blank=True)
    throughput = models.CharField(max_length=20, choices=Throughput.choices, null=True, blank=True)
    verified_name = models.CharField(max_length=255, null=True, blank=True)
    waba_id = models.CharField(max_length=100, null=True, blank=True)
    can_send_message = models.CharField(max_length=20, choices=CanSendMessage.choices, null=True, blank=True)

    # Store errors and additional info as JSON
    errors = models.JSONField(
        default=list,
        blank=True,
        help_text="List of error objects with error_code, error_description, and possible_solution",
    )
    additional_info = models.JSONField(default=list, blank=True, help_text="List of additional information messages")

    # Metadata
    last_synced_at = models.DateTimeField(auto_now=True, help_text="Last time WABA info was synced")
    last_sync_error = models.JSONField(
        null=True,
        blank=True,
        help_text="Stores the last API error response if sync failed (status, message, error code)",
    )

    # Track history of changes
    history = HistoricalRecords()

    name = None

    class Meta:
        verbose_name = "WABA Information"
        verbose_name_plural = "WABA Information"

    def __str__(self):
        return f"WABA Info: {self.wa_app.app_name} - {self.account_status}"

    #: Fields ``update_from_adapter_data`` is willing to write. Anything else in
    #: the payload is ignored rather than set, so a provider cannot reach
    #: arbitrary columns.
    _ADAPTER_WRITABLE_FIELDS = (
        "account_status",
        "docker_status",
        "messaging_limit",
        "mm_lite_status",
        "ownership_type",
        "phone",
        "phone_quality",
        "throughput",
        "verified_name",
        "waba_id",
        "can_send_message",
        "errors",
        "additional_info",
    )

    @classmethod
    def update_from_adapter_data(cls, wa_app, data: dict):
        """
        Write normalised WABA state from a BSP adapter's ``fetch_waba_info``.

        The provider-agnostic counterpart to
        :meth:`update_from_api_response`, which parses Gupshup's camelCase
        envelope and is therefore unusable on Meta Direct — the reason
        ``messaging_limit`` stayed NULL there and every broadcast was capped at
        the conservative 50-recipient fallback (#267).

        Only keys actually present are written. A provider that does not report
        a field leaves the stored value alone, which matters because the two
        providers report overlapping but different sets: ``docker_status`` is a
        Gupshup concept and has no Meta equivalent, so a Meta sync must not
        blank it.

        Args:
            wa_app: TenantWAApp instance
            data: normalised ``{WABAInfo field: value}`` mapping

        Returns:
            tuple: (WABAInfo instance, None)
        """
        waba_info, _ = cls.objects.get_or_create(wa_app=wa_app)

        for field in cls._ADAPTER_WRITABLE_FIELDS:
            if field in data:
                setattr(waba_info, field, data[field])

        waba_info.last_sync_error = None
        waba_info.save()
        return waba_info, None

    @classmethod
    def update_from_api_response(cls, wa_app, api_response):
        """
        Create or update WABA info from API response.

        Args:
            wa_app: TenantWAApp instance
            api_response: Dictionary containing WABA info from API

        Returns:
            tuple: (WABAInfo instance or None, error_dict or None)
        """
        # Get or create WABA info
        waba_info, created = cls.objects.get_or_create(wa_app=wa_app)

        # Handle error responses
        if api_response.get("status") == "error":
            error_data = {
                "status": "error",
                "message": api_response.get("message", "Unknown error"),
                "timestamp": None,  # Will be set by last_synced_at auto_now
            }
            waba_info.last_sync_error = error_data
            waba_info.save()
            return None, error_data

        # Handle success response
        if api_response.get("status") == "success":
            waba_data = api_response.get("wabaInfo", {})

            # Update fields
            waba_info.account_status = waba_data.get("accountStatus")
            waba_info.docker_status = waba_data.get("dockerStatus")
            waba_info.messaging_limit = waba_data.get("messagingLimit")
            waba_info.mm_lite_status = waba_data.get("mmLiteStatus")
            waba_info.ownership_type = waba_data.get("ownershipType")
            waba_info.phone = waba_data.get("phone")
            waba_info.phone_quality = waba_data.get("phoneQuality")
            waba_info.throughput = waba_data.get("throughput")
            waba_info.verified_name = waba_data.get("verifiedName")
            waba_info.waba_id = waba_data.get("wabaId")
            waba_info.can_send_message = waba_data.get("canSendMessage")
            waba_info.errors = waba_data.get("errors", [])
            waba_info.additional_info = waba_data.get("additionalInfo", [])

            # Clear last sync error on successful update
            waba_info.last_sync_error = None
            waba_info.save()

            return waba_info, None

        # Unknown response format
        return None, {"status": "error", "message": "Unknown response format"}


class DefaultRoleSlugs(models.TextChoices):
    """Slugs for the 5 system-default roles."""

    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    AGENT = "agent", "Agent"
    VIEWER = "viewer", "Viewer"


class TenantRole(BaseTenantModelForFilterUser):
    """
    A role scoped to a single tenant.
    The 5 default roles (Owner, Admin, Manager, Agent, Viewer) are seeded
    automatically for every tenant; tenants may also create custom roles.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="roles")
    # `name` is inherited from BaseModel (CharField max_length=100)
    slug = models.SlugField(max_length=100)
    priority = models.PositiveIntegerField(
        default=20,
        validators=[MaxValueValidator(100)],
        help_text="0-100, higher = more powerful",
    )
    is_system = models.BooleanField(
        default=False,
        help_text="True for the 5 default roles, False for custom roles",
    )
    is_editable = models.BooleanField(
        default=True,
        help_text="False only for OWNER role (permissions are locked)",
    )
    history = HistoricalRecords()

    class Meta:
        unique_together = ("tenant", "slug")
        ordering = ["-priority", "name"]

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"


class RolePermission(models.Model):
    """Maps a TenantRole to a specific permission key, e.g. 'broadcast.create'."""

    role = models.ForeignKey(TenantRole, on_delete=models.CASCADE, related_name="permissions")
    permission = models.CharField(max_length=100, db_index=True)
    allowed = models.BooleanField(default=False)
    history = HistoricalRecords()

    class Meta:
        unique_together = ("role", "permission")

    def __str__(self):
        return f"{self.role.slug}:{self.permission} = {self.allowed}"


class TenantUser(BaseTenantModelForFilterUser):
    """
    Model to associate users with tenants.
    Attributes:
        tenant (ForeignKey): Reference to the Tenant model.
        user (ForeignKey): Reference to the User model.
        role (ForeignKey): Reference to TenantRole (required, protected from deletion).
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="tenant_users")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_tenants")
    role = models.ForeignKey(
        TenantRole,
        on_delete=models.PROTECT,
        related_name="members",
        help_text="RBAC role for this user within the tenant",
    )
    name = None
    history = HistoricalRecords()

    class Meta:
        unique_together = ("tenant", "user")

    def __str__(self):
        return f"{self.user.username} - {self.tenant.name}"


class TenantAccessKey(BaseModelWithOwner):
    """
    Model to store access keys for a tenant.

    Attributes:
        tenant (ForeignKey): Reference to the Tenant model.
        key_hash (CharField): Keyed digest of the access key — the stored form.
        key_prefix (CharField): Leading characters of the key, for identification.
        revoked_at (DateTimeField): When the key was revoked, if it was.
    """

    # The raw key is no longer stored (#301). It is a bearer credential — whoever
    # holds it names a tenant to the token endpoint and to every MCP tool call —
    # so a database dump, backup or read replica must not be enough to replay
    # one. Only the digest is kept, which is why ``issue`` and ``rotate`` return
    # the plaintext: that return value is the one and only chance to read it.
    #
    # Keyed HMAC rather than a bare SHA-256 because operators have chosen short,
    # guessable access keys in the past; an unkeyed digest of a guessable key is
    # itself guessable from a stolen dump, which would undo the point.
    HASH_SALT = "tenants.TenantAccessKey.key"

    # Long enough to tell two of a tenant's keys apart during a rotation,
    # short enough to be worthless on its own.
    PREFIX_LENGTH = 8

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="access_keys")
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text="HMAC-SHA256 digest of the access key. The key itself is never stored.",
    )
    key_prefix = models.CharField(
        max_length=12,
        blank=True,
        default="",
        help_text="First characters of the key, so a key can be named in logs and admin without revealing it.",
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this key was revoked. Set together with is_active=False by revoke().",
    )
    name = None

    # ── Hashing ───────────────────────────────────────────────────────
    @classmethod
    def hash_key(cls, raw_key: str) -> str:
        """Digest ``raw_key`` the way a stored key was digested."""
        return salted_hmac(cls.HASH_SALT, raw_key, algorithm="sha256").hexdigest()

    @classmethod
    def hash_candidates(cls, raw_key: str) -> list:
        """Every digest ``raw_key`` could be stored under.

        SECRET_KEY_FALLBACKS exists so SECRET_KEY can be rotated without
        invalidating everything derived from it. Keying the digest on
        SECRET_KEY without honouring the fallbacks would turn a routine
        SECRET_KEY rotation into a silent revocation of every tenant's key
        at once — the kind of outage nobody connects to the rotation.
        """
        digests = [cls.hash_key(raw_key)]
        for secret in getattr(settings, "SECRET_KEY_FALLBACKS", []):
            digests.append(salted_hmac(cls.HASH_SALT, raw_key, secret=secret, algorithm="sha256").hexdigest())
        return digests

    @staticmethod
    def generate_key() -> str:
        """A fresh access key. Prefixed so a leaked string is recognisable."""
        return f"jc_{secrets.token_urlsafe(32)}"

    # ── Issue / resolve / rotate / revoke ─────────────────────────────
    @classmethod
    def issue(cls, tenant, **kwargs):
        """Create a key for ``tenant``; return ``(instance, raw_key)``.

        The caller must hand ``raw_key`` to its holder immediately — nothing
        can recover it afterwards.
        """
        raw_key = cls.generate_key()
        instance = cls.objects.create(
            tenant=tenant,
            key_hash=cls.hash_key(raw_key),
            key_prefix=raw_key[: cls.PREFIX_LENGTH],
            **kwargs,
        )
        return instance, raw_key

    @classmethod
    def resolve(cls, raw_key: str):
        """The live key matching ``raw_key``, or None.

        Filters on both flags: ``is_active`` is what BaseModel-aware code
        already reads and ``revoked_at`` is the record of when, so a row that
        somehow carries one without the other still stops authenticating.
        """
        return (
            cls.objects.select_related("tenant")
            .filter(key_hash__in=cls.hash_candidates(raw_key), is_active=True, revoked_at__isnull=True)
            .first()
        )

    def rotate(self) -> str:
        """Replace this key's secret in place; return the new plaintext.

        In-place rotation cuts the old secret off the moment it commits. For a
        handover with overlap, ``issue`` a second key for the tenant, move the
        holder across, then ``revoke`` the first — a tenant may hold several.
        """
        raw_key = self.generate_key()
        self.key_hash = self.hash_key(raw_key)
        self.key_prefix = raw_key[: self.PREFIX_LENGTH]
        self.save(update_fields=["key_hash", "key_prefix", "updated_at"])
        return raw_key

    def revoke(self):
        """Stop this key authenticating, and record when."""
        self.is_active = False
        self.revoked_at = timezone.now()
        self.save(update_fields=["is_active", "revoked_at", "updated_at"])

    def __str__(self):
        return f"{self.tenant.name}: {self.key_prefix}..."


class TenantMedia(BaseTenantModelForFilterUser):
    filter_by_user_tenant_fk = "tenant__tenant_users__user"
    name = None
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="media")
    media = models.FileField(upload_to="tenant_media/")
    platform = models.CharField(max_length=50, choices=PlatformChoices.choices, default=PlatformChoices.WHATSAPP)
    wa_handle_id = models.JSONField(
        null=True, blank=True, help_text="Handle ID returned by WhatsApp (Gupshup) after media upload."
    )
    media_id = models.TextField(
        null=True, blank=True, help_text="Media ID returned by WhatsApp (Gupshup) after media upload."
    )
    auto_convert = models.BooleanField(
        default=False, help_text="If enabled, media will be auto-converted to WhatsApp compatible format upon upload."
    )
    card_index = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Zero-based index of the card in carousel template (0, 1, 2, ...). Null for non-card media.",
    )

    def __str__(self):
        return f"{self.tenant.name} - {self.media.name}"

    @property
    def handle_id(self):
        """
        Get the WhatsApp handleId.
        wa_handle_id is stored as JSON, typically {"handleId": "some-id"}
        """
        if self.wa_handle_id:
            if isinstance(self.wa_handle_id, dict):
                return self.wa_handle_id.get("handleId") or self.wa_handle_id.get("handle_id")
            return self.wa_handle_id
        return None


class TenantTags(BaseTenantModelForFilterUser):
    """
    Model to store tags for a tenant.
    Attributes:
        tenant (ForeignKey): Reference to the Tenant model.
        tag (CharField): The tag name.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="tags")

    class Meta:
        unique_together = ("tenant", "name")

    def __str__(self):
        return f"{self.tenant.name}: {self.name}"


class BrandingSettings(models.Model):
    """
    Singleton model to store branding for the application.
    Only admin users can modify these settings.

    Text:
    - Product name: shown in page titles and transactional copy

    Colour:
    - Primary colour: hex triplet the UI derives its brand ramp from

    Assets:
    - Favicon: PNG image, 583x583 px
    - Primary Logo: SVG, 854x262 px (aspect ratio ~3.26:1)
    - Secondary Logo: SVG, 532x380 px (aspect ratio ~1.4:1)
    """

    # Favicon - PNG 583x583px
    favicon = models.ImageField(
        upload_to="branding/", null=True, blank=True, help_text="Favicon PNG image (583x583 px)"
    )
    favicon_url = models.URLField(
        max_length=500, null=True, blank=True, help_text="External URL for favicon (used if favicon file not uploaded)"
    )

    # Primary Logo - SVG 854x262px (aspect ratio 3.26:1) - typically horizontal logo
    primary_logo = models.FileField(
        upload_to="branding/", null=True, blank=True, help_text="Primary logo SVG (854x262 px, aspect ratio 3.26:1)"
    )
    primary_logo_url = models.URLField(max_length=500, null=True, blank=True, help_text="External URL for primary logo")

    # Secondary Logo - SVG 532x380px (aspect ratio 1.4:1) - typically square-ish logo
    secondary_logo = models.FileField(
        upload_to="branding/", null=True, blank=True, help_text="Secondary logo SVG (532x380 px, aspect ratio 1.4:1)"
    )
    secondary_logo_url = models.URLField(
        max_length=500, null=True, blank=True, help_text="External URL for secondary logo"
    )

    # Product name shown in page titles, transactional copy and payment descriptors.
    # Blank falls back to settings.DEFAULT_PRODUCT_NAME.
    product_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Product name shown in the UI (e.g. page titles). Blank uses the deployment default.",
    )

    # Primary brand colour as a hex triplet. The UI derives its full brand
    # ramp from this; blank falls back to settings.DEFAULT_BRAND_COLOR.
    primary_color = models.CharField(
        max_length=7,
        blank=True,
        default="",
        validators=[
            RegexValidator(
                regex=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                message="Enter a hex colour such as #465fff.",
            )
        ],
        help_text="Primary brand colour, e.g. #465fff. Blank uses the deployment default.",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Branding Settings"
        verbose_name_plural = "Branding Settings"

    def __str__(self):
        return "Branding Settings"

    def save(self, *args, **kwargs):
        """Ensure only one instance exists (singleton pattern)."""
        if not self.pk and BrandingSettings.objects.exists():
            # Update existing instance instead of creating new one
            existing = BrandingSettings.objects.first()
            self.pk = existing.pk
            # Adopting a pk turns this into an UPDATE, and auto_now_add only
            # populates created_at on INSERT. Without carrying the original
            # value across, the UPDATE writes NULL into a NOT NULL column.
            self.created_at = existing.created_at
        super().save(*args, **kwargs)

    @classmethod
    def get_instance(cls):
        """Get or create the singleton instance."""
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance

    @property
    def effective_favicon_url(self):
        """Return file URL if uploaded, otherwise external URL."""
        if self.favicon:
            return self.favicon.url
        return self.favicon_url

    @property
    def effective_primary_logo_url(self):
        """Return file URL if uploaded, otherwise external URL."""
        if self.primary_logo:
            return self.primary_logo.url
        return self.primary_logo_url

    @property
    def effective_secondary_logo_url(self):
        """Return file URL if uploaded, otherwise external URL."""
        if self.secondary_logo:
            return self.secondary_logo.url
        return self.secondary_logo_url

    @property
    def effective_product_name(self):
        """Return the configured product name, otherwise the deployment default."""
        return self.product_name or settings.DEFAULT_PRODUCT_NAME

    @property
    def effective_primary_color(self):
        """Return the configured brand colour, otherwise the deployment default."""
        return self.primary_color or settings.DEFAULT_BRAND_COLOR


class SentPartnerReport(models.Model):
    """One row per partner report actually delivered (#230).

    The contractual reports are emailed on a schedule. A schedule can fire
    twice — on this deployment it did, for months, because django-crontab
    entries were installed in two crontabs (jain-t/jina-connect#612). A
    partner receiving the same statement twice is a credibility problem, and
    "we fixed the crontab" is not a guarantee.

    So delivery is recorded, and a second attempt for the same period is a
    no-op unless explicitly forced.
    """

    KIND_ACTIVE_ACCOUNTS = "active_accounts"
    KIND_AVAILABILITY = "availability"
    KIND_CHOICES = (
        (KIND_ACTIVE_ACCOUNTS, "Active Customer Account report"),
        (KIND_AVAILABILITY, "Service availability report"),
    )

    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()

    recipients = models.TextField(help_text="Who it went to, as sent.")
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "year", "month"], name="unique_partner_report_per_period"),
        ]
        ordering = ["-year", "-month"]
        verbose_name = "Sent partner report"
        verbose_name_plural = "Sent partner reports"

    def __str__(self):
        return f"{self.get_kind_display()} {self.year}-{self.month:02d}"
