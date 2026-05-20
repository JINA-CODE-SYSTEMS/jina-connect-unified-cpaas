"""``AttributionEvent`` (#197)."""

from __future__ import annotations

import uuid

from django.db import models

from abstract.models import BaseTenantModelForFilterUser


class CapiStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    # IN_FLIGHT marks rows claimed by ``flush_capi_queue`` between the
    # claim phase and the ``_send_one`` phase. Other workers see this
    # status as "not due" so the row isn't double-claimed. The status
    # is transient — every claimed row flips to SENT / FAILED /
    # DEAD_LETTERED before the flush returns. A worker crash mid-batch
    # leaves rows stuck in IN_FLIGHT; a sweeper Celery beat (#201
    # third review Blocker #1) reclaims them after a stuck threshold.
    IN_FLIGHT = "in_flight", "In flight (claimed by a flusher worker)"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed (retryable)"
    SKIPPED = "skipped", "Skipped (consent / disabled)"
    DEAD_LETTERED = "dead_lettered", "Dead-lettered after retries"


class AttributionEvent(BaseTenantModelForFilterUser):
    """One row per attributable event for a CTWA lead.

    Created by signals + flow nodes; consumed by the async CAPI flusher
    (:func:`attribution.tasks.flush_capi_queue`).
    """

    filter_by_user_tenant_fk = "lead__tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead = models.ForeignKey(
        "ctwa.CtwaLead",
        on_delete=models.CASCADE,
        related_name="attribution_events",
    )
    event_name = models.CharField(
        max_length=40,
        help_text="Meta standard event name (Lead, Purchase, Custom).",
    )
    event_time = models.DateTimeField()
    event_value_minor = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")

    # Monotonic per (lead, event_name). Handles re-qualification
    # without colliding on event_id.
    sequence = models.BigIntegerField()
    event_id = models.CharField(max_length=128, unique=True)

    # ``fbc`` synthesised at push time per Meta spec —
    # ``fb.1.{unix_ms}.{ctwa_clid}`` when ctwa_clid is present.
    fbc = models.CharField(max_length=200, blank=True, default="")

    capi_status = models.CharField(
        max_length=20,
        choices=CapiStatus.choices,
        default=CapiStatus.PENDING,
    )
    capi_response = models.JSONField(null=True, blank=True)
    capi_attempts = models.SmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    emq_score = models.SmallIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "Attribution event"
        verbose_name_plural = "Attribution events"
        constraints = [
            models.UniqueConstraint(
                fields=["lead", "event_name", "sequence"],
                name="attribution_unique_event_per_lead",
            ),
        ]
        indexes = [
            models.Index(fields=["capi_status", "next_retry_at"], name="attr_evt_status_retry_idx"),
            models.Index(fields=["lead", "event_name"], name="attr_evt_lead_name_idx"),
        ]

    def __str__(self) -> str:
        return f"AttributionEvent({self.event_id})"
