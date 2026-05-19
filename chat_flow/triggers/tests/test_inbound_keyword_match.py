"""inbound_keyword_match trigger tests for #188."""

from __future__ import annotations

from chat_flow.triggers.base import TriggerEvent
from chat_flow.triggers.types.inbound_keyword_match import InboundKeywordMatch


def _event(**overrides) -> TriggerEvent:
    base = {
        "tenant_id": 1,
        "channel": "wa",
        "contact_id": 100,
        "inbound_row_id": "msg-1",
        "inbound_row_model": "wa.WAMessage",
        "body_text": "Hello, I want to talk to Sales please",
        "received_at": "2026-05-18T12:00:00+00:00",
        "extra": {},
    }
    base.update(overrides)
    return TriggerEvent(**base)


class TestInboundKeywordMatch:
    def test_case_insensitive_default(self):
        t = InboundKeywordMatch()
        assert t.matches(_event(), {"keywords": ["sales"]}) is True

    def test_case_sensitive_no_match(self):
        t = InboundKeywordMatch()
        assert t.matches(_event(), {"keywords": ["sales"], "case_sensitive": True}) is False

    def test_case_sensitive_exact_match(self):
        t = InboundKeywordMatch()
        assert (
            t.matches(
                _event(body_text="lowercase sales here"),
                {"keywords": ["sales"], "case_sensitive": True},
            )
            is True
        )

    def test_channel_filter_skip(self):
        t = InboundKeywordMatch()
        assert t.matches(_event(channel="sms"), {"keywords": ["sales"], "channel": "wa"}) is False

    def test_channel_filter_any(self):
        t = InboundKeywordMatch()
        # "any" sentinel should accept every channel.
        assert t.matches(_event(channel="sms"), {"keywords": ["sales"], "channel": "any"}) is True

    def test_empty_body_no_match(self):
        t = InboundKeywordMatch()
        assert t.matches(_event(body_text=None), {"keywords": ["sales"]}) is False
        assert t.matches(_event(body_text=""), {"keywords": ["sales"]}) is False

    def test_multiple_keywords_any_match(self):
        t = InboundKeywordMatch()
        assert (
            t.matches(
                _event(body_text="I need help with billing"),
                {"keywords": ["sales", "billing", "support"]},
            )
            is True
        )

    def test_substring_match(self):
        t = InboundKeywordMatch()
        assert t.matches(_event(body_text="Salesperson speaking"), {"keywords": ["sales"]}) is True
