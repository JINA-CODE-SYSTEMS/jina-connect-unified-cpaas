"""``inbound_keyword_match`` trigger (#188).

Fires when an inbound message's body contains any of the configured
keywords. The worked example shipping alongside the substrate —
production-ready and reusable for non-CTWA features (FAQ routing,
support hand-offs, intent capture).

Config:

  {
    "keywords":       ["sales", "support"],     # required, non-empty
    "channel":        "wa" | "sms" | ... | null, # optional channel filter
    "case_sensitive": false                      # optional, default false
  }

Matches when:

  * ``event.body_text`` is non-empty, AND
  * ``event.channel`` matches ``channel`` (if configured), AND
  * Any configured keyword appears as a substring (case-insensitive
    by default).

Reads no ``event.extra`` fields — works for every channel that
populates ``body_text``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from chat_flow.triggers.base import BaseTrigger, Channel, TriggerEvent
from chat_flow.triggers.registry import register_trigger


class InboundKeywordMatchConfig(BaseModel):
    keywords: list[str] = Field(..., min_length=1)
    channel: Channel | Literal["any"] | None = None
    case_sensitive: bool = False


@register_trigger("inbound_keyword_match")
class InboundKeywordMatch(BaseTrigger):
    config_model = InboundKeywordMatchConfig

    def matches(self, event: TriggerEvent, config: dict) -> bool:
        text = event.body_text or ""
        if not text:
            return False

        chan = config.get("channel")
        if chan and chan != "any" and chan != event.channel:
            return False

        keywords = config.get("keywords") or []
        if not keywords:
            return False

        if not config.get("case_sensitive", False):
            text = text.lower()
            keywords = [k.lower() for k in keywords]

        return any(k in text for k in keywords)


__all__ = ["InboundKeywordMatch", "InboundKeywordMatchConfig"]
