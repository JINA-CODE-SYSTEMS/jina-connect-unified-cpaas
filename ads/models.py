"""``ads/`` models (#196)."""

from __future__ import annotations

import uuid

from django.db import models

from abstract.models import BaseTenantModelForFilterUser


class AdCreative(BaseTenantModelForFilterUser):
    """Image / video creative used by one or more CTWA campaigns."""

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="ad_creatives")

    media_type = models.CharField(
        max_length=10,
        choices=[("image", "Image"), ("video", "Video")],
    )
    media_url = models.TextField(help_text="URL of the uploaded asset (S3 / CDN).")
    caption = models.TextField(blank=True, default="")
    headline = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")

    # Stamped by ads.services.validators at upload time.
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "Ad creative"
        verbose_name_plural = "Ad creatives"


class AudienceTemplate(BaseTenantModelForFilterUser):
    """Saved audience configuration for reuse across campaigns."""

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="audience_templates")
    # Shape mirrors Meta's targeting spec. Stored as JSON so the
    # frontend can round-trip without losing fields we don't model.
    targeting_spec = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Audience template"
        verbose_name_plural = "Audience templates"


class CampaignInsights(BaseTenantModelForFilterUser):
    """Hourly-polled metrics per CTWA campaign per day."""

    filter_by_user_tenant_fk = "campaign__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        "ctwa.CtwaCampaign",
        on_delete=models.CASCADE,
        related_name="insights",
    )
    day = models.DateField()

    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    spend_minor = models.BigIntegerField(default=0)
    cpc_minor = models.BigIntegerField(default=0)
    ctr_bp = models.IntegerField(default=0, help_text="Click-through rate, basis points (10000 = 100%)")

    polled_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Campaign insights"
        verbose_name_plural = "Campaign insights"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "day"],
                name="ads_insights_unique_per_day",
            ),
        ]
        indexes = [
            models.Index(fields=["campaign", "-day"], name="ads_insights_campaign_day_idx"),
        ]
