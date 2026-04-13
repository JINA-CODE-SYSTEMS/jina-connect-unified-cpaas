from typing import Union

from django.contrib.auth import get_user_model
from django.db import models

from abstract.models import BaseTenantModelForFilterUser
from contacts.models import TenantContact
from team_inbox.managers import MessageEventIdsManager, MessagesManager
from team_inbox.validators import validate_message_content
from tenants.models import Tenant
from wa.models import WAMessage

User = get_user_model()


class MessagePlatformChoices(models.TextChoices):
    WHATSAPP = "WHATSAPP", "WhatsApp"
    TELEGRAM = "TELEGRAM", "Telegram"
    SMS = "SMS", "SMS"
    VOICE = "VOICE", "Voice"
    # Canonical source: jina_connect.platform_choices.PlatformChoices


class MessageDirectionChoices(models.TextChoices):
    INCOMING = "INCOMING", "Incoming"
    OUTGOING = "OUTGOING", "Outgoing"


class AuthorChoices(models.TextChoices):
    USER = "USER", "User"
    CONTACT = "CONTACT", "Contact"
    BOT = "BOT", "Bot"


class MessageEventIds(models.Model):
    """
    Model to store mapping between message and event IDs.
    Provides a shared sequential numbering for timeline ordering.
    """

    numbering = models.BigAutoField(primary_key=True, auto_created=True, verbose_name="ID")

    objects = MessageEventIdsManager()

    class Meta:
        verbose_name = "Message/Event ID"
        verbose_name_plural = "Message/Event IDs"

    def __str__(self):
        return str(self.numbering)


# Create your models here.
class Messages(BaseTenantModelForFilterUser):
    filter_by_user_tenant_fk = "tenant__tenant_users__user"
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="messages")
    message_id = models.ForeignKey(
        MessageEventIds, on_delete=models.CASCADE, related_name="messages", null=True, blank=True
    )
    content = models.JSONField(validators=[validate_message_content])
    timestamp = models.DateTimeField(auto_now_add=True)
    direction = models.CharField(max_length=10, choices=MessageDirectionChoices.choices)
    platform = models.CharField(max_length=10, choices=MessagePlatformChoices.choices)
    author = models.CharField(max_length=10, choices=AuthorChoices.choices)
    created_by = None  # Overridden as @property below
    updated_by = None  # this is not required
    contact = models.ForeignKey(TenantContact, on_delete=models.CASCADE, related_name="messages", null=True, blank=True)
    tenant_user = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # Read status - True if any team member has read the message
    # Only relevant for INCOMING messages
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    read_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages_read",
        help_text="First team member who read this message",
    )
    # FK to outgoing message for tracking outgoing message status
    outgoing_message = models.ForeignKey(
        WAMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_inbox_messages",
        help_text="Link to WAMessage for outgoing message status tracking",
    )
    # External message ID (gs_id from Gupshup) for looking up status from BroadcastMessage
    # Used when outgoing_message is not set (i.e., for broadcast template messages)
    external_message_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        help_text="External message ID (gs_id) for status tracking via BroadcastMessage lookup",
    )
    name = None

    # Custom manager with annotation methods
    objects = MessagesManager()

    @property
    def created_by(self):  # noqa: F811
        if self.author == AuthorChoices.USER:
            return self.tenant_user.get_full_name() if self.tenant_user else "Unknown User"
        elif self.author == AuthorChoices.CONTACT:
            if self.contact:
                name = f"{self.contact.first_name or ''} {self.contact.last_name or ''}".strip()
                return name or str(self.contact.phone)
            return "Unknown Contact"
        return None

    @property
    def expires_at(self) -> Union[int, None]:
        # For WhatsApp messages, set expiration to 24 hours from timestamp
        # (WhatsApp session window policy).
        if self.platform == MessagePlatformChoices.WHATSAPP:
            return int((self.timestamp.timestamp()) + 24 * 60 * 60)
        # Telegram and other platforms have no session-window expiry.
        return None

    @property
    def _broadcast_message(self):
        """
        Lazy lookup of BroadcastMessage by external_message_id.
        Cached to avoid repeated DB queries.
        """
        if not hasattr(self, "_cached_broadcast_message"):
            self._cached_broadcast_message = None
            if self.external_message_id and not self.outgoing_message:
                from broadcast.models import BroadcastMessage

                self._cached_broadcast_message = BroadcastMessage.objects.filter(
                    message_id=self.external_message_id
                ).first()
        return self._cached_broadcast_message

    @property
    def outgoing_status(self) -> Union[str, None]:
        """
        Returns the status of the outgoing message.
        Checks GupshupOutgoingMessages first, then BroadcastMessage by external_message_id.
        Only relevant for OUTGOING messages.
        """
        if self.direction == MessageDirectionChoices.OUTGOING:
            if self.outgoing_message:
                return self.outgoing_message.status
            if self._broadcast_message:
                return self._broadcast_message.status
        return None

    @property
    def outgoing_sent_at(self):
        """Timestamp when outgoing message was sent to Gupshup API."""
        if self.direction == MessageDirectionChoices.OUTGOING:
            if self.outgoing_message:
                return self.outgoing_message.sent_at
            if self._broadcast_message:
                return self._broadcast_message.sent_at
        return None

    @property
    def outgoing_delivered_at(self):
        """Timestamp when outgoing message was delivered to recipient."""
        if self.direction == MessageDirectionChoices.OUTGOING:
            if self.outgoing_message:
                return self.outgoing_message.delivered_at
            if self._broadcast_message:
                return self._broadcast_message.delivered_at
        return None

    @property
    def outgoing_read_at(self):
        """Timestamp when outgoing message was read by recipient."""
        if self.direction == MessageDirectionChoices.OUTGOING:
            if self.outgoing_message:
                return self.outgoing_message.read_at
            if self._broadcast_message:
                return self._broadcast_message.read_at
        return None

    @property
    def outgoing_failed_at(self):
        """Timestamp when outgoing message failed to send."""
        if self.direction == MessageDirectionChoices.OUTGOING:
            if self.outgoing_message:
                return self.outgoing_message.failed_at
            if self._broadcast_message:
                return self._broadcast_message.failed_at
        return None

    def __str__(self):
        return f"Message {self.message_id_id} at {self.timestamp}"

    def save(self, *args, **kwargs):
        """
        Override save to auto-mark messages as read when:
        1. The message is INCOMING
        2. The contact is assigned to a BOT
        """
        # Only for new incoming messages
        if not self.pk and self.direction == MessageDirectionChoices.INCOMING:
            # Check if contact is assigned to a bot
            if self.contact and self.contact.is_assigned_to_bot:
                self.is_read = True

        super().save(*args, **kwargs)


# ============ Event Model ============


class EventTypeChoices(models.TextChoices):
    """
    Extensible event types for team inbox.
    Add new event types here as needed.
    """

    # Assignment events
    TICKET_ASSIGNED = "TICKET_ASSIGNED", "Ticket Assigned"
    TICKET_UNASSIGNED = "TICKET_UNASSIGNED", "Ticket Unassigned"
    TICKET_REASSIGNED = "TICKET_REASSIGNED", "Ticket Reassigned"

    # Ticket status events
    TICKET_OPENED = "TICKET_OPENED", "Ticket Opened"
    TICKET_CLOSED = "TICKET_CLOSED", "Ticket Closed"
    TICKET_REOPENED = "TICKET_REOPENED", "Ticket Reopened"

    # System events
    SYSTEM = "SYSTEM", "System Event"

    # Custom/generic
    CUSTOM = "CUSTOM", "Custom Event"


class ActorTypeChoices(models.TextChoices):
    """Actor type - can be a user, bot, or chatflow."""

    USER = "USER", "User"
    BOT = "BOT", "Bot"
    CHATFLOW = "CHATFLOW", "ChatFlow"


class Event(BaseTenantModelForFilterUser):
    """
    Flexible event model for team inbox timeline.

    Stores events like ticket assignments, closures, notes, etc.
    Uses JSONField for type-specific data to allow easy extension.

    Actor fields use type + ID pattern to support both users and bots:
    - actor_type: 'USER' or 'BOT'
    - actor_id: User ID or Bot model ID

    Example event_data by type:
    - TICKET_ASSIGNED: {"instructions": "Please follow up with customer"}
    - TICKET_CLOSED: {"reason": "Issue resolved", "resolution_notes": "..."}
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    # Core fields
    event_id = models.ForeignKey(
        MessageEventIds, on_delete=models.CASCADE, related_name="events", null=True, blank=True
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="events")
    contact = models.ForeignKey(TenantContact, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=30, choices=EventTypeChoices.choices, db_index=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    # Note/Instructions - included with assignments
    note = models.TextField(blank=True, null=True, help_text="Note or instructions for the assignment")

    # ============ Actor fields (supports User or Bot) ============
    # Created by
    created_by_type = models.CharField(max_length=10, choices=ActorTypeChoices.choices, default=ActorTypeChoices.USER)
    created_by_id = models.PositiveIntegerField(null=True, blank=True)
    created_by_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events_created",
        help_text="User who created the event (if actor is USER)",
    )

    # Assigned by
    assigned_by_type = models.CharField(max_length=10, choices=ActorTypeChoices.choices, null=True, blank=True)
    assigned_by_id = models.PositiveIntegerField(null=True, blank=True)
    assigned_by_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events_assigned_by",
        help_text="User who assigned (if actor is USER)",
    )

    # Assigned to
    assigned_to_type = models.CharField(max_length=10, choices=ActorTypeChoices.choices, null=True, blank=True)
    assigned_to_id = models.PositiveIntegerField(null=True, blank=True)
    assigned_to_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events_assigned_to",
        help_text="User assigned to (if actor is USER)",
    )

    # Display customization
    icon = models.CharField(max_length=50, blank=True, null=True, help_text="Icon name or emoji")
    color_background = models.CharField(max_length=20, blank=True, null=True, help_text="Background color hex code")
    color_text = models.CharField(max_length=20, blank=True, null=True, help_text="Text color hex code")

    # Flexible data storage for type-specific fields
    event_data = models.JSONField(
        default=dict, blank=True, help_text="Type-specific event data (reason, resolution_notes, etc.)"
    )

    # Disable inherited fields not needed
    created_by = None
    updated_by = None

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["tenant", "contact", "-timestamp"]),
            models.Index(fields=["event_type", "-timestamp"]),
        ]

    def __str__(self):
        """
        String representation of the event.
        Django automatically uses this in admin and shell.
        Django automatically creates a method called get_<field_name>_display() that returns the human-readable label from the choices.
        """
        return f"{self.get_event_type_display()} - {self.event_id.pk if self.event_id else 'N/A'} at {self.timestamp}"

    # ============ Convenience properties for actor names ============

    def _get_actor_name(self, actor_type, actor_id, user_fk):
        """Get display name for an actor (user or bot)."""
        if actor_type == ActorTypeChoices.USER and user_fk:
            return user_fk.get_full_name() or user_fk.email
        elif actor_type == ActorTypeChoices.BOT:
            # TODO: Fetch bot name from Bot model when available
            return f"Bot #{actor_id}" if actor_id else "Bot"
        elif actor_type == ActorTypeChoices.CHATFLOW:
            # Fetch ChatFlow name from database
            if actor_id:
                try:
                    from chat_flow.models import ChatFlow

                    chatflow = ChatFlow.objects.filter(id=actor_id).values("name").first()
                    if chatflow:
                        return chatflow["name"]
                except Exception:
                    pass
            return f"ChatFlow #{actor_id}" if actor_id else "ChatFlow"

    @property
    def created_by_name(self):
        """Get display name of who created the event."""
        return self._get_actor_name(self.created_by_type, self.created_by_id, self.created_by_user)

    @property
    def assigned_by_name(self):
        """Get display name of who assigned."""
        return self._get_actor_name(self.assigned_by_type, self.assigned_by_id, self.assigned_by_user)

    @property
    def assigned_to_name(self):
        """Get display name of who was assigned to."""
        return self._get_actor_name(self.assigned_to_type, self.assigned_to_id, self.assigned_to_user)

    @property
    def reason(self):
        """Get reason (e.g., reason for closure) from event_data."""
        return self.event_data.get("reason")

    # ============ Factory methods for creating common event types ============

    @classmethod
    def _set_actor(cls, event_kwargs, prefix, actor, actor_type=None):
        """
        Helper to set actor fields.

        Args:
            event_kwargs: dict to populate
            prefix: 'created_by', 'assigned_by', or 'assigned_to'
            actor: User instance, or None for bot
            actor_type: ActorTypeChoices value (auto-detected if actor is User)
        """
        if actor is None and actor_type is None:
            return

        if actor_type == ActorTypeChoices.BOT:
            event_kwargs[f"{prefix}_type"] = ActorTypeChoices.BOT
            event_kwargs[f"{prefix}_id"] = actor  # actor is bot_id for bots
        elif actor is not None:
            # Assume it's a User instance
            event_kwargs[f"{prefix}_type"] = ActorTypeChoices.USER
            event_kwargs[f"{prefix}_id"] = actor.id
            event_kwargs[f"{prefix}_user"] = actor

    @classmethod
    def create_assignment_event(
        cls,
        tenant,
        contact,
        assigned_by,
        assigned_to,
        note=None,
        assigned_by_type=ActorTypeChoices.USER,
        assigned_to_type=ActorTypeChoices.USER,
        created_by=None,
        created_by_type=None,
    ):
        """
        Create a ticket assignment event with optional note.

        Args:
            tenant: Tenant instance
            contact: TenantContact instance
            assigned_by: User instance or bot_id (if assigned_by_type is BOT)
            assigned_to: User instance or bot_id (if assigned_to_type is BOT)
            note: Optional note/instructions for the assignment
            assigned_by_type: ActorTypeChoices (USER or BOT)
            assigned_to_type: ActorTypeChoices (USER or BOT)
            created_by: Who created the event (defaults to assigned_by)
            created_by_type: ActorTypeChoices for creator
        """
        event_kwargs = {
            "tenant": tenant,
            "contact": contact,
            "event_type": EventTypeChoices.TICKET_ASSIGNED,
            "note": note,
            "icon": "👤",
            "color_background": "#E3F2FD",
            "color_text": "#1565C0",
        }

        # Set assigned_by
        if assigned_by_type == ActorTypeChoices.BOT:
            cls._set_actor(event_kwargs, "assigned_by", assigned_by, ActorTypeChoices.BOT)
        else:
            cls._set_actor(event_kwargs, "assigned_by", assigned_by)

        # Set assigned_to
        if assigned_to_type == ActorTypeChoices.BOT:
            cls._set_actor(event_kwargs, "assigned_to", assigned_to, ActorTypeChoices.BOT)
        else:
            cls._set_actor(event_kwargs, "assigned_to", assigned_to)

        # Set created_by (defaults to assigned_by)
        if created_by is not None:
            if created_by_type == ActorTypeChoices.BOT:
                cls._set_actor(event_kwargs, "created_by", created_by, ActorTypeChoices.BOT)
            else:
                cls._set_actor(event_kwargs, "created_by", created_by)
        else:
            # Copy from assigned_by
            event_kwargs["created_by_type"] = event_kwargs.get("assigned_by_type")
            event_kwargs["created_by_id"] = event_kwargs.get("assigned_by_id")
            event_kwargs["created_by_user"] = event_kwargs.get("assigned_by_user")

        # Update the contact's assignment
        from django.utils import timezone

        from contacts.models import AssigneeTypeChoices

        if assigned_to_type == ActorTypeChoices.BOT:
            contact.assigned_to_type = AssigneeTypeChoices.BOT
            contact.assigned_to_id = assigned_to  # bot_id
            contact.assigned_to_user = None
        else:
            contact.assigned_to_type = AssigneeTypeChoices.USER
            contact.assigned_to_id = assigned_to.id
            contact.assigned_to_user = assigned_to
        contact.assigned_at = timezone.now()
        contact.save(update_fields=["assigned_to_type", "assigned_to_id", "assigned_to_user", "assigned_at"])

        # If assigned to bot, mark all unread incoming messages as read
        if assigned_to_type == ActorTypeChoices.BOT:
            Messages.objects.filter(contact=contact, direction=MessageDirectionChoices.INCOMING, is_read=False).update(
                is_read=True
            )

        return cls.objects.create(**event_kwargs)

    @classmethod
    def create_ticket_closed_event(
        cls,
        tenant,
        contact,
        closed_by,
        closed_by_type=ActorTypeChoices.USER,
        reason=None,
        resolution_notes=None,
        note=None,
    ):
        """
        Create a ticket closed event.

        Args:
            tenant: Tenant instance
            contact: TenantContact instance
            closed_by: User instance or bot_id
            closed_by_type: ActorTypeChoices (USER or BOT)
            reason: Reason for closure
            resolution_notes: Additional resolution notes
            note: Optional note
        """
        event_data = {}
        if reason:
            event_data["reason"] = reason
        if resolution_notes:
            event_data["resolution_notes"] = resolution_notes

        event_kwargs = {
            "tenant": tenant,
            "contact": contact,
            "event_type": EventTypeChoices.TICKET_CLOSED,
            "note": note,
            "event_data": event_data,
            "icon": "✅",
            "color_background": "#E8F5E9",
            "color_text": "#2E7D32",
        }

        if closed_by_type == ActorTypeChoices.BOT:
            cls._set_actor(event_kwargs, "created_by", closed_by, ActorTypeChoices.BOT)
        else:
            cls._set_actor(event_kwargs, "created_by", closed_by)

        return cls.objects.create(**event_kwargs)

    @classmethod
    def create_ticket_opened_event(
        cls,
        tenant,
        contact,
        opened_by=None,
        opened_by_type=ActorTypeChoices.USER,
    ):
        """
        Create a ticket opened event.

        Args:
            tenant: Tenant instance
            contact: TenantContact instance
            opened_by: User instance or bot_id (optional)
            opened_by_type: ActorTypeChoices (USER or BOT)
        """
        event_kwargs = {
            "tenant": tenant,
            "contact": contact,
            "event_type": EventTypeChoices.TICKET_OPENED,
            "icon": "📬",
            "color_background": "#FFF3E0",
            "color_text": "#E65100",
        }

        if opened_by is not None:
            if opened_by_type == ActorTypeChoices.BOT:
                cls._set_actor(event_kwargs, "created_by", opened_by, ActorTypeChoices.BOT)
            else:
                cls._set_actor(event_kwargs, "created_by", opened_by)

        return cls.objects.create(**event_kwargs)
