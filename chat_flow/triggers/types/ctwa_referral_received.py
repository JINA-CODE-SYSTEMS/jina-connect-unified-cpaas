"""``ctwa_referral_received`` trigger (#188, consumer of #194).

Fires when an inbound WhatsApp message carries a CTWA referral
payload. The substrate ships this trigger so the CTWA ingestion PR
(#194) can land without re-creating the registry plumbing — it just
adds the ``extra`` fields and matches against them via this class.

Config:

  {
    "campaign_ids": ["uuid", ...] | "any"
  }

``event.extra`` fields read (populated by ``wa/tasks.py`` once the
CTWA referral-parsing path lands in #192 / #194):

  ``referral_source_id``  — Meta ad id (required for a CTWA event)
  ``campaign_id``         — resolved CtwaCampaign uuid (#194 ingestion;
                            may be absent if ad_id doesn't match a known
                            campaign — the orphan path)

Match semantics:

  * Non-WA events never match.
  * If ``referral_source_id`` is absent, this isn't a CTWA event → no match.
  * ``"any"`` mode matches every CTWA inbound, including orphans
    (good for "catch all CTWA leads in one fallback flow").
  * Specific-campaign-id mode matches only when ``campaign_id`` is
    resolved AND in the configured list. Orphans don't match a
    specific-ids config — by design, orphan leads route through the
    fallback "any" flow if one exists.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from chat_flow.triggers.base import BaseTrigger, TriggerEvent
from chat_flow.triggers.registry import register_trigger


class CtwaReferralReceivedConfig(BaseModel):
    # Either the literal "any" or a non-empty list of campaign UUIDs (as
    # strings; UUID typing handled at runtime via str-equality so the
    # config remains JSON-friendly for the frontend).
    campaign_ids: Literal["any"] | list[str]


@register_trigger("ctwa_referral_received")
class CtwaReferralReceived(BaseTrigger):
    config_model = CtwaReferralReceivedConfig

    def matches(self, event: TriggerEvent, config: dict) -> bool:
        if event.channel != "wa":
            return False
        if not event.extra.get("referral_source_id"):
            return False

        campaign_ids = config.get("campaign_ids")
        if campaign_ids == "any":
            return True

        if not isinstance(campaign_ids, list) or not campaign_ids:
            return False

        campaign_id = event.extra.get("campaign_id")
        if not campaign_id:
            # Orphan referral (ad_id resolved to no known campaign).
            # Specific-campaign-ids configs don't match orphans on purpose.
            return False
        return str(campaign_id) in {str(x) for x in campaign_ids}


__all__ = ["CtwaReferralReceived", "CtwaReferralReceivedConfig"]
