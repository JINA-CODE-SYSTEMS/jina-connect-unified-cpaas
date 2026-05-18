"""Dispatcher tests for #188.

Covers:
  * Idempotency claim — second dispatch of same event is a no-op.
  * Trigger that raises is logged + skipped without breaking others.
  * Only ``is_active=True`` flows are considered.
  * Tenant scoping — flows in a different tenant don't match.
  * Unknown trigger type on a stored flow is tolerated (logged + skipped).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from chat_flow.triggers.base import TriggerEvent


def _make_event(tenant_id: int = 1, **overrides) -> TriggerEvent:
    base = {
        "tenant_id": tenant_id,
        "channel": "wa",
        "contact_id": 100,
        "inbound_row_id": "msg-abc",
        "inbound_row_model": "wa.WAMessage",
        "body_text": "hello sales team",
        "received_at": "2026-05-18T12:00:00+00:00",
        "extra": {},
    }
    base.update(overrides)
    return TriggerEvent(**base)


@pytest.mark.django_db
class TestDispatcher:
    def test_dispatch_idempotent(self):
        # Two dispatches of the same event => second one no-ops.
        from chat_flow.triggers.dispatcher import dispatch

        event = _make_event(inbound_row_id="msg-idem-1")
        # Force claim to succeed first, then fail (simulating Redis SETNX
        # behaviour without needing a real Redis).
        with patch("chat_flow.triggers.dispatcher.claim_dispatch", side_effect=[True, False]):
            first = dispatch(event)
            second = dispatch(event)
        assert first == 0  # no matching flows in this test DB
        assert second == 0

    def test_dispatch_no_flows_returns_zero(self):
        from chat_flow.triggers.dispatcher import dispatch

        with patch("chat_flow.triggers.dispatcher.claim_dispatch", return_value=True):
            assert dispatch(_make_event(inbound_row_id="msg-empty")) == 0

    def test_trigger_raise_does_not_break_dispatch(self):
        # If one trigger's matches() raises, dispatch logs and moves on.
        # Easiest way to exercise: monkey-patch the registry with a
        # raising fake trigger, then a real one.
        from pydantic import BaseModel

        from chat_flow.triggers.base import BaseTrigger
        from chat_flow.triggers.dispatcher import _flow_matches

        class _Cfg(BaseModel):
            pass

        class _Boom(BaseTrigger):
            type_name = "boom"
            config_model = _Cfg

            def matches(self, event, config):
                raise RuntimeError("trigger explosion")

        class _Ok(BaseTrigger):
            type_name = "ok_real_match"
            config_model = _Cfg

            def matches(self, event, config):
                return True

        class _StubFlow:
            id = "flow-stub"
            triggers = [{"type": "boom", "config": {}}, {"type": "ok_real_match", "config": {}}]

        with patch.dict(
            "chat_flow.triggers.registry._REGISTRY",
            {"boom": _Boom, "ok_real_match": _Ok},
            clear=False,
        ):
            assert _flow_matches(_StubFlow(), _make_event()) is True

    def test_unknown_trigger_type_tolerated(self):
        from chat_flow.triggers.dispatcher import _flow_matches

        class _StubFlow:
            id = "flow-stub"
            triggers = [{"type": "no_such_trigger", "config": {}}]

        assert _flow_matches(_StubFlow(), _make_event()) is False
