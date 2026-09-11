"""``TriggerEvent`` construction invariants (#270).

``body_text`` is annotated ``str | None`` and a dataclass does not enforce
annotations, so for the whole life of the subsystem WhatsApp emitted a dict
there and nothing objected.

The guard matters more than it looks: every emitter wraps ``emit()`` in
try/except so inbound ingestion cannot break, which means a wrong type
surfaces as one logged error naming the offending emitter rather than as a
subsystem that silently never fires.
"""

from __future__ import annotations

import pytest

from chat_flow.triggers.base import TriggerEvent

BASE = {
    "tenant_id": 1,
    "channel": "wa",
    "contact_id": 2,
    "inbound_row_id": "3",
    "inbound_row_model": "team_inbox.Messages",
    "received_at": "2026-09-11T00:00:00Z",
}


def test_a_string_body_is_accepted():
    assert TriggerEvent(**BASE, body_text="hello").body_text == "hello"


def test_none_is_accepted():
    """Voice emits ``None`` deliberately — a call has no body."""
    assert TriggerEvent(**BASE, body_text=None).body_text is None


def test_an_empty_string_is_accepted():
    """Empty is a legitimate value; triggers handle it with their own guard."""
    assert TriggerEvent(**BASE, body_text="").body_text == ""


@pytest.mark.parametrize(
    "value",
    [
        {"text": "hello"},  # the exact shape WhatsApp emitted for two releases
        {},
        ["hello"],
        42,
        object(),
    ],
)
def test_anything_else_is_refused_at_construction(value):
    with pytest.raises(TypeError, match="body_text must be str"):
        TriggerEvent(**BASE, body_text=value)


def test_the_error_names_the_offending_type():
    """So the log line points at what was passed, not merely that it was wrong."""
    with pytest.raises(TypeError, match="got dict"):
        TriggerEvent(**BASE, body_text={"text": "hello"})
