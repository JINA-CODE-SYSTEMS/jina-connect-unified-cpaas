from django.db import models

from abstract.models import BaseTenantModelForFilterUser
from tenants.models import Tenant


class NotificationType(models.TextChoices):
    TEMPLATE_APPROVED = "template_approved", "Template Approved"
    TEMPLATE_REJECTED = "template_rejected", "Template Rejected"
    TEMPLATE_SUBMITTED = "template_submitted", "Template Submitted"
    BROADCAST_SCHEDULED = "broadcast_scheduled", "Broadcast Scheduled"
    BROADCAST_COMPLETED = "broadcast_completed", "Broadcast Completed"
    BROADCAST_FAILED = "broadcast_failed", "Broadcast Failed"
    CONTACT_ADDED = "contact_added", "Contact Added"
    CONTACT_IMPORTED = "contact_imported", "Contacts Imported"
    AUTOMATION_UPDATED = "automation_updated", "Automation Updated"
    AUTOMATION_FAILED = "automation_failed", "Automation Failed"
    WALLET_RECHARGED = "wallet_recharged", "Wallet Recharged"
    LOW_BALANCE = "low_balance", "Low Balance Warning"


class Notification(BaseTenantModelForFilterUser):
    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="notifications")
    notification_type = models.CharField(max_length=30, choices=NotificationType.choices, db_index=True)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True, default="")
    is_read = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    # Override inherited fields not needed for notifications
    name = None
    description = None

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.notification_type}: {self.title}"
