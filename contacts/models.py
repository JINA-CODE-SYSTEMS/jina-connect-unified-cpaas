from django.contrib.auth import get_user_model
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

from abstract.models import BaseTenantModelForFilterUser
from tenants.models import Tenant


class LeadStatusChoices(models.TextChoices):
    NEW = "NEW", "New"
    CONTACTED = "CONTACTED", "Contacted"
    QUALIFIED = "QUALIFIED", "Qualified"
    UNQUALIFIED = "UNQUALIFIED", "Unqualified"
    CONVERTED = "CONVERTED", "Converted"


class PreferredChannelChoices(models.TextChoices):
    SMS = "SMS", "SMS"
    VOICE = "VOICE", "Voice"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    RCS = "RCS", "RCS"


User = get_user_model()


# Create your models here.
class ContactSource(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    TELEGRAM = "TELEGRAM", "Telegram"
    VOICE = "VOICE", "Voice"
    SMS = "SMS", "SMS"
    RCS = "RCS", "RCS"
    # Canonical source: jina_connect.platform_choices.PlatformChoices


class AssigneeTypeChoices(models.TextChoices):
    """Who the contact/conversation is assigned to."""

    USER = "USER", "User"
    BOT = "BOT", "Bot"
    CHATFLOW = "CHATFLOW", "ChatFlow"
    UNASSIGNED = "UNASSIGNED", "Unassigned"


class TicketStatusChoices(models.TextChoices):
    """Status of the conversation/ticket."""

    OPEN = "OPEN", "Open"
    CLOSED = "CLOSED", "Closed"


class TenantContact(BaseTenantModelForFilterUser):
    filter_by_user_tenant_fk = "tenant__tenant_users__user"
    phone = PhoneNumberField(help_text="Contact phone number")
    first_name = models.CharField(max_length=255, help_text="Contact first name", blank=True)
    last_name = models.CharField(max_length=255, help_text="Contact last name", blank=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="contacts")
    tag = models.CharField(max_length=255, help_text="Tag for the contact", blank=True)
    source = models.CharField(max_length=20, choices=ContactSource.choices, default=ContactSource.MANUAL)

    # --- Telegram identity fields ---
    telegram_chat_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Telegram chat ID for this contact",
    )
    telegram_username = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Telegram @username (without @)",
    )

    # --- Voice / Lead fields ---
    lead_status = models.CharField(
        max_length=20, choices=LeadStatusChoices.choices, null=True, blank=True, help_text="Lead qualification status"
    )
    lead_score = models.PositiveSmallIntegerField(default=0, help_text="Lead score (0-100)")
    last_called_at = models.DateTimeField(null=True, blank=True, help_text="When this contact was last called")
    total_calls = models.PositiveIntegerField(default=0, help_text="Total number of calls with this contact")
    dnc = models.BooleanField(default=False, help_text="Do Not Call flag")
    preferred_channel = models.CharField(
        max_length=10,
        choices=PreferredChannelChoices.choices,
        null=True,
        blank=True,
        help_text="Preferred communication channel",
    )

    # Assignment tracking - who is handling this conversation
    assigned_to_type = models.CharField(
        max_length=15, choices=AssigneeTypeChoices.choices, default=AssigneeTypeChoices.UNASSIGNED, db_index=True
    )
    assigned_to_id = models.PositiveIntegerField(
        null=True, blank=True, help_text="User ID or Bot ID depending on assigned_to_type"
    )
    assigned_to_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_contacts",
        help_text="User assigned to this contact (if assigned_to_type is USER)",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)

    # Assigned by tracking - who made the assignment
    assigned_by_type = models.CharField(
        max_length=15,
        choices=AssigneeTypeChoices.choices,
        null=True,
        blank=True,
        help_text="Type of actor who made the assignment",
    )
    assigned_by_id = models.PositiveIntegerField(
        null=True, blank=True, help_text="User ID, Bot ID, or ChatFlow ID depending on assigned_by_type"
    )
    assigned_by_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments_made",
        help_text="User who made the assignment (if assigned_by_type is USER)",
    )

    # Assignment note - instructions from assigner to assignee
    assignment_note = models.TextField(
        blank=True, null=True, help_text="Note or instructions from assigner to assignee"
    )

    # RCS capability (#110)
    rcs_capable = models.BooleanField(null=True, help_text="Whether this contact supports RCS messaging")
    rcs_checked_at = models.DateTimeField(null=True, blank=True, help_text="When RCS capability was last checked")

    # Ticket/conversation status
    status = models.CharField(
        max_length=10,
        choices=TicketStatusChoices.choices,
        default=TicketStatusChoices.OPEN,
        db_index=True,
        help_text="Whether this conversation is open or closed",
    )

    RESERVED_VARS = [
        {"key": "first_name", "description": "First name of the contact"},
        {"key": "last_name", "description": "Last name of the contact"},
        {"key": "full_name", "description": "Full name of the contact"},
        {"key": "phone", "description": "Phone number of the contact"},
    ]


class ImportJob(models.Model):
    """Tracks bulk contact import jobs (CSV/XLSX) processed asynchronously (#118)."""

    class Status(models.TextChoices):
        PENDING = "PENDING"
        PROCESSING = "PROCESSING"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="import_jobs")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=512, help_text="Path in default storage")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    skip_duplicates = models.BooleanField(default=True)
    default_tag = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Import {self.pk} — {self.file_name} ({self.status})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def is_assigned_to_bot(self) -> bool:
        """Check if this contact is currently assigned to a bot."""
        return self.assigned_to_type == AssigneeTypeChoices.BOT

    @property
    def is_assigned_to_chatflow(self) -> bool:
        """Check if this contact is currently assigned to a chatflow."""
        return self.assigned_to_type == AssigneeTypeChoices.CHATFLOW

    @property
    def is_unassigned(self) -> bool:
        """Check if this contact is unassigned."""
        return self.assigned_to_type == AssigneeTypeChoices.UNASSIGNED

    @property
    def assigned_to_name(self) -> str:
        """Get display name of who the contact is assigned to."""
        if self.assigned_to_type == AssigneeTypeChoices.USER and self.assigned_to_user:
            return self.assigned_to_user.get_full_name() or self.assigned_to_user.email
        elif self.assigned_to_type == AssigneeTypeChoices.BOT:
            # TODO: Fetch bot name from Bot model when available
            return f"Bot #{self.assigned_to_id}" if self.assigned_to_id else "Bot"
        elif self.assigned_to_type == AssigneeTypeChoices.CHATFLOW:
            # TODO: Fetch chatflow name from ChatFlow model when available
            return f"ChatFlow #{self.assigned_to_id}" if self.assigned_to_id else "ChatFlow"
        return "Unassigned"

    @property
    def assigned_by_name(self) -> str:
        """Get display name of who made the assignment."""
        if not self.assigned_by_type:
            return None
        if self.assigned_by_type == AssigneeTypeChoices.USER and self.assigned_by_user:
            return self.assigned_by_user.get_full_name() or self.assigned_by_user.email
        elif self.assigned_by_type == AssigneeTypeChoices.BOT:
            return f"Bot #{self.assigned_by_id}" if self.assigned_by_id else "Bot"
        elif self.assigned_by_type == AssigneeTypeChoices.CHATFLOW:
            return f"ChatFlow #{self.assigned_by_id}" if self.assigned_by_id else "ChatFlow"
        return None

    class Meta:
        verbose_name = "Tenant Contact"
        verbose_name_plural = "Tenant Contacts"
        ordering = ["-created_at"]
