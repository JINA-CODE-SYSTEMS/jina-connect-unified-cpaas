"""CTWA models (#194).

Two row types:

* :class:`CtwaCampaign` — first-class campaign object. Owns its own
  state machine (draft → in_review → active → paused → ...) keyed off
  Meta's response to the Marketing API publish call.
* :class:`CtwaLead` — one row per CTWA-originated conversation. Linked
  to a :class:`wa.WaConversation` (which carries service-window state)
  and optionally to a :class:`CtwaCampaign` (null when the ad_id on
  the referral payload doesn't match any local campaign — the
  "orphan" path: still create the lead, still fire the flow, surface
  the orphan in a separate dashboard tab).
"""

from __future__ import annotations

import uuid

from django.db import models

from abstract.models import BaseTenantModelForFilterUser
from ctwa.constants import (
    CtwaBudgetType,
    CtwaCampaignStatus,
    CtwaLeadStatus,
    QualificationSignal,
)


class CtwaCampaign(BaseTenantModelForFilterUser):
    """A CTWA campaign as represented in Jina Connect.

    Publishing this row to Meta (via ``ads/`` app) creates the actual
    Campaign + AdSet + AdCreative + Ad on Meta's side and stamps their
    IDs back onto the row.
    """

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="ctwa_campaigns")

    # Connection FKs are nullable until #191 ships ``MetaBusinessConnection``
    # and #196 ships ``AdCreative`` — the models exist in this PR but the
    # full OAuth + creative-upload flows are stubbed.
    meta_connection = models.ForeignKey(
        "meta.MetaBusinessConnection",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="campaigns",
    )
    tenant_wa_app = models.ForeignKey(
        "tenants.TenantWAApp",
        on_delete=models.PROTECT,
        related_name="ctwa_campaigns",
    )
    creative = models.ForeignKey(
        "ads.AdCreative",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="campaigns",
    )

    # Meta IDs — null until the campaign is published to Meta. Indexed
    # so inbound webhooks can resolve ``meta_ad_id → campaign`` in O(1).
    meta_campaign_id = models.CharField(max_length=64, blank=True, default="")
    meta_adset_id = models.CharField(max_length=64, blank=True, default="")
    meta_ad_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    prefilled_message = models.TextField(
        help_text="Text WhatsApp auto-fills when a user taps the ad. Validated "
        "against Meta's denylist + length limit by ads/services/validators.py.",
    )

    status = models.CharField(
        max_length=20,
        choices=CtwaCampaignStatus.choices,
        default=CtwaCampaignStatus.DRAFT,
    )
    budget_type = models.CharField(max_length=20, choices=CtwaBudgetType.choices, default=CtwaBudgetType.DAILY)
    budget_amount_minor = models.BigIntegerField(default=0, help_text="In smallest currency unit (paise/cents).")
    currency = models.CharField(max_length=3, default="USD")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    # Wiring to flow + team. Both nullable — a campaign without a flow
    # falls through to the agent team; a campaign without a team falls
    # back to the tenant's default routing.
    flow = models.ForeignKey(
        "chat_flow.ChatFlow",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ctwa_campaigns",
    )
    # Inbox routing: a free-form team identifier — the inbox layer
    # resolves it however it wants today (most tenants don't have a
    # first-class Team model yet). Future: convert to FK when
    # team_inbox grows a real Team model.
    agent_team_key = models.CharField(max_length=64, blank=True, default="")

    qualification_signal = models.CharField(
        max_length=20,
        choices=QualificationSignal.choices,
        default=QualificationSignal.FLOW_NODE,
        help_text=(
            "When to fire the Meta Conversions API Lead event. NEVER on "
            "first inbound message — that's the #1 CAPI mistake. Default "
            "flow_node means a flow explicitly marks the lead qualified."
        ),
    )

    class Meta:
        verbose_name = "CTWA campaign"
        verbose_name_plural = "CTWA campaigns"
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["meta_ad_id"]),
        ]

    def __str__(self) -> str:
        return f"CtwaCampaign({self.id}, {self.status})"


class CtwaLead(BaseTenantModelForFilterUser):
    """One row per CTWA-originated conversation."""

    filter_by_user_tenant_fk = "tenant__tenant_users__user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="ctwa_leads")
    contact = models.ForeignKey("contacts.TenantContact", on_delete=models.CASCADE, related_name="ctwa_leads")
    conversation = models.ForeignKey(
        "wa.WaConversation",
        on_delete=models.PROTECT,
        related_name="ctwa_leads",
    )

    # Nullable: ``flagged_orphan_campaign=True`` when ``meta_ad_id``
    # doesn't match any local campaign at ingestion time. Reconciled
    # weekly by ``ctwa.tasks.reconcile_orphans``.
    campaign = models.ForeignKey(
        CtwaCampaign,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="leads",
    )
    flagged_orphan_campaign = models.BooleanField(default=False)

    meta_ad_id = models.CharField(max_length=64, db_index=True)
    meta_adset_id = models.CharField(max_length=64, blank=True, default="")
    meta_campaign_id = models.CharField(max_length=64, blank=True, default="")
    ctwa_clid = models.CharField(max_length=128, blank=True, default="", db_index=True)
    source_url = models.TextField(blank=True, default="")
    source_type = models.CharField(max_length=20, blank=True, default="")
    headline = models.TextField(blank=True, default="")
    body = models.TextField(blank=True, default="")
    media_type = models.CharField(max_length=20, blank=True, default="")
    media_url = models.TextField(blank=True, default="")
    thumbnail_url = models.TextField(blank=True, default="")

    first_message_at = models.DateTimeField()

    qualification_status = models.CharField(
        max_length=20,
        choices=CtwaLeadStatus.choices,
        default=CtwaLeadStatus.NEW,
    )
    agent = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_ctwa_leads",
    )
    crm_external_id = models.CharField(max_length=128, blank=True, default="")
    # Idempotency key for inbound CRM webhooks (#198). The last outbound
    # push stores its event id here; an inbound webhook carrying the
    # same id is dropped (it's our own push echoing back).
    last_crm_external_event_id = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        verbose_name = "CTWA lead"
        verbose_name_plural = "CTWA leads"
        indexes = [
            models.Index(fields=["tenant", "campaign", "qualification_status"]),
            models.Index(fields=["meta_ad_id"]),
            models.Index(fields=["ctwa_clid"]),
            models.Index(fields=["flagged_orphan_campaign", "tenant"]),
        ]

    def __str__(self) -> str:
        return f"CtwaLead({self.id}, {self.qualification_status})"
