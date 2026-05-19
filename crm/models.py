"""``crm/`` models (#198)."""

from __future__ import annotations

import uuid

from django.db import models
from encrypted_model_fields.fields import EncryptedTextField

from abstract.models import BaseTenantModelForFilterUser
from crm.constants import CrmProvider


class CrmConnection(BaseTenantModelForFilterUser):
    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="crm_connections")

    provider = models.CharField(max_length=20, choices=CrmProvider.choices)

    # Credentials — encrypted at rest. Different providers use
    # different fields; the connector decides which to read.
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    api_key = EncryptedTextField(blank=True, default="")
    webhook_secret = EncryptedTextField(blank=True, default="")
    webhook_url = models.URLField(blank=True, default="", max_length=512)

    expires_at = models.DateTimeField(null=True, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name = "CRM connection"
        verbose_name_plural = "CRM connections"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "provider"],
                name="crm_unique_provider_per_tenant",
            ),
        ]


class CrmEntityMapping(BaseTenantModelForFilterUser):
    """Maps Jina-side ``CtwaLead.qualification_status`` values to the
    tenant's CRM-side status / stage values."""

    filter_by_user_tenant_fk = "connection__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(CrmConnection, on_delete=models.CASCADE, related_name="mappings")
    jina_value = models.CharField(max_length=64)
    crm_value = models.CharField(max_length=128)

    class Meta:
        verbose_name = "CRM entity mapping"
        verbose_name_plural = "CRM entity mappings"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "jina_value"],
                name="crm_mapping_unique_jina_value",
            ),
        ]


class CrmSyncEvent(BaseTenantModelForFilterUser):
    """Audit log entry for one inbound CRM webhook OR one outbound push.

    Salesforce specifically needs this — its webhook retries are noisy
    and support cases will ask "did we receive that event?".
    """

    filter_by_user_tenant_fk = "connection__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(CrmConnection, on_delete=models.CASCADE, related_name="sync_events")
    direction = models.CharField(
        max_length=10,
        choices=[("outbound", "Outbound (Jina → CRM)"), ("inbound", "Inbound (CRM → Jina)")],
    )
    external_event_id = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False)
    skip_reason = models.CharField(max_length=128, blank=True, default="")
    lead = models.ForeignKey(
        "ctwa.CtwaLead",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crm_sync_events",
    )

    class Meta:
        verbose_name = "CRM sync event"
        verbose_name_plural = "CRM sync events"
        indexes = [
            models.Index(fields=["connection", "external_event_id"], name="crm_sync_conn_extid_idx"),
            models.Index(fields=["direction", "processed"], name="crm_sync_dir_processed_idx"),
        ]
