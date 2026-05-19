from __future__ import annotations

from django.db import models


class CtwaCampaignStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_REVIEW = "in_review", "In review (Meta)"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    DISAPPROVED = "disapproved", "Disapproved by Meta"
    ARCHIVED = "archived", "Archived"
    EXTERNAL = "external", "Shadow campaign (created from orphan reconciliation)"


class CtwaBudgetType(models.TextChoices):
    DAILY = "daily", "Daily"
    LIFETIME = "lifetime", "Lifetime"


class CtwaLeadStatus(models.TextChoices):
    NEW = "new", "New"
    ENGAGED = "engaged", "Engaged"
    QUALIFIED = "qualified", "Qualified"
    DISQUALIFIED = "disqualified", "Disqualified"
    CONVERTED = "converted", "Converted"


class QualificationSignal(models.TextChoices):
    """When to fire the Meta Conversions API ``Lead`` event.

    NEVER ``first_message`` — that's the most common CAPI-implementation
    mistake. Firing on first message inflates the Lead count and trains
    Meta optimization on chatty users instead of buyers. ``flow_node``
    is the safe default; tenants can opt into ``agent_claim`` (B2B-ish)
    or ``message_count`` (heuristic).
    """

    FLOW_NODE = "flow_node", "Flow node sets qualified=True"
    AGENT_CLAIM = "agent_claim", "Agent claims conversation"
    MESSAGE_COUNT = "message_count", "After N inbound messages"
