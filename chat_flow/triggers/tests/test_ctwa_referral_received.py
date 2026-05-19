"""ctwa_referral_received trigger tests for #188."""

from __future__ import annotations

from chat_flow.triggers.base import TriggerEvent
from chat_flow.triggers.types.ctwa_referral_received import CtwaReferralReceived


def _event(**overrides) -> TriggerEvent:
    base = {
        "tenant_id": 1,
        "channel": "wa",
        "contact_id": 100,
        "inbound_row_id": "msg-ctwa-1",
        "inbound_row_model": "wa.WAMessage",
        "body_text": "Saw your ad on Facebook",
        "received_at": "2026-05-18T12:00:00+00:00",
        "extra": {
            "referral_source_id": "ad-meta-12345",
            "campaign_id": "00000000-0000-0000-0000-000000000001",
        },
    }
    base.update(overrides)
    return TriggerEvent(**base)


class TestCtwaReferralReceived:
    def test_any_mode_matches_known_campaign(self):
        t = CtwaReferralReceived()
        assert t.matches(_event(), {"campaign_ids": "any"}) is True

    def test_any_mode_matches_orphan(self):
        # Orphan: no resolved campaign_id, but referral_source_id is set.
        t = CtwaReferralReceived()
        orphan_event = _event(
            extra={"referral_source_id": "ad-meta-99999"}  # no campaign_id
        )
        assert t.matches(orphan_event, {"campaign_ids": "any"}) is True

    def test_specific_campaign_matches(self):
        t = CtwaReferralReceived()
        assert (
            t.matches(
                _event(),
                {"campaign_ids": ["00000000-0000-0000-0000-000000000001"]},
            )
            is True
        )

    def test_specific_campaign_no_match(self):
        t = CtwaReferralReceived()
        assert (
            t.matches(
                _event(),
                {"campaign_ids": ["00000000-0000-0000-0000-000000009999"]},
            )
            is False
        )

    def test_specific_campaign_orphan_does_not_match(self):
        # By design: orphans only flow to the "any" fallback flow, not
        # specific-id flows.
        t = CtwaReferralReceived()
        orphan_event = _event(extra={"referral_source_id": "ad-meta-99999"})
        assert (
            t.matches(
                orphan_event,
                {"campaign_ids": ["00000000-0000-0000-0000-000000000001"]},
            )
            is False
        )

    def test_non_wa_channel_rejected(self):
        t = CtwaReferralReceived()
        assert t.matches(_event(channel="sms"), {"campaign_ids": "any"}) is False

    def test_no_referral_no_match(self):
        t = CtwaReferralReceived()
        plain_event = _event(extra={})
        assert t.matches(plain_event, {"campaign_ids": "any"}) is False
