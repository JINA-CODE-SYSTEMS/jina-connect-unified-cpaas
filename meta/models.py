"""``MetaBusinessConnection`` (#191).

Per-tenant connection to a Meta Business Account + ad account. Token
is stored encrypted-at-rest via the same ``EncryptedTextField`` the
voice channel uses for SIP/Twilio credentials.
"""

from __future__ import annotations

import uuid

from django.db import models
from encrypted_model_fields.fields import EncryptedTextField

from abstract.models import BaseTenantModelForFilterUser


class MetaBusinessConnection(BaseTenantModelForFilterUser):
    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="meta_connections")

    meta_business_id = models.CharField(max_length=64)
    meta_ad_account_id = models.CharField(max_length=64)
    page_id = models.CharField(max_length=64, blank=True, default="")
    instagram_actor_id = models.CharField(max_length=64, blank=True, default="")

    # Encrypted at rest. Reuses the existing project pattern from voice.
    system_user_token = EncryptedTextField()

    scopes = models.JSONField(default=list, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    refreshed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    # Set True by the token-refresh worker on 190-series Meta errors;
    # surfaced as a banner in the campaign-create wizard.
    needs_reauth = models.BooleanField(default=False)

    # WABA-link detection state cached from the last successful
    # ``GET /me/businesses/{biz_id}/owned_whatsapp_business_accounts``
    # call. ``[]`` until first refresh; the wizard refreshes on demand.
    linked_waba_ids = models.JSONField(default=list, blank=True)
    linked_wabas_refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Meta business connection"
        verbose_name_plural = "Meta business connections"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "meta_ad_account_id"],
                name="meta_unique_ad_account_per_tenant",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "needs_reauth"]),
        ]

    def __str__(self) -> str:
        return f"MetaBusinessConnection({self.id})"

    @property
    def is_waba_linked(self) -> bool:
        """True iff at least one WABA owned by the tenant's business
        is linked to this connection's ad account."""
        return bool(self.linked_waba_ids)
