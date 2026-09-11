"""Inbound WhatsApp → ``TriggerEvent`` boundary (#270).

The event-trigger subsystem shipped inert on WhatsApp: the emission site read
``content["body"]`` and handed the trigger a ``{"text": …}`` dict where a
string was required. Every component passed its own tests — the trigger's
tests construct a ``TriggerEvent`` by hand with a string, and nothing
exercised the place the two meet.

So these tests deliberately start from the *real* output of
``_build_team_inbox_content`` and run all the way to a real trigger's
``matches()``. Asserting on a hand-built event would reproduce exactly the
blind spot that let this ship.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_trigger_emission.py -v
"""

from __future__ import annotations

import pytest

from chat_flow.triggers.base import TriggerEvent
from chat_flow.triggers.types.inbound_keyword_match import InboundKeywordMatch
from wa.tasks import _build_team_inbox_content, _trigger_body_text


class _FakeEvent:
    """Stand-in for the ``WAWebhookEvent`` row; only ``pk`` is read."""

    pk = 1234


def _content(extracted: dict) -> dict:
    return _build_team_inbox_content(extracted, _FakeEvent())


# ─────────────────────────────────────────────────────────────────────────────
# What the customer wrote, however they wrote it
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "extracted", "expected"),
    [
        ("plain text", {"text": "HELP me please"}, "HELP me please"),
        ("button reply", {"button_title": "Talk to an agent"}, "Talk to an agent"),
        (
            "image with caption",
            {"image_link": "https://x/i.jpg", "text": "help, broken screen"},
            "help, broken screen",
        ),
        (
            "video with caption",
            {"video_link": "https://x/v.mp4", "text": "see this"},
            "see this",
        ),
        (
            "document with caption",
            {"document_link": "https://x/d.pdf", "text": "invoice attached"},
            "invoice attached",
        ),
        ("image, no caption", {"image_link": "https://x/i.jpg"}, None),
        ("audio note", {"audio_link": "https://x/a.ogg"}, None),
        ("unrecognised type", {}, None),
    ],
)
def test_body_text_is_extracted_from_real_content(label, extracted, expected):
    """Every inbound shape yields the text a human typed, or nothing."""
    assert _trigger_body_text(_content(extracted)) == expected, label


def test_order_messages_carry_their_summary():
    """Orders synthesise a body; the trigger should see the same string."""
    content = _content({"message_type": "order", "order": {"catalog_id": "c1", "product_items": [{}, {}]}})
    body = _trigger_body_text(content)
    assert isinstance(body, str)
    assert "2 item(s)" in body


def test_the_result_is_always_a_string_or_none():
    """The property that was violated: never a dict, for any inbound shape."""
    for extracted in (
        {"text": "hi"},
        {"button_title": "yes"},
        {"image_link": "https://x/i.jpg", "text": "cap"},
        {"audio_link": "https://x/a.ogg"},
        {},
    ):
        assert isinstance(_trigger_body_text(_content(extracted)), (str, type(None)))


def test_helper_tolerates_junk():
    assert _trigger_body_text(None) is None
    assert _trigger_body_text({}) is None
    assert _trigger_body_text({"body": "not a dict"}) is None
    assert _trigger_body_text({"body": {"text": None}}) is None
    assert _trigger_body_text({"type": "image", "image": {}}) is None


# ─────────────────────────────────────────────────────────────────────────────
# End to end: webhook content → TriggerEvent → a real trigger matching
# ─────────────────────────────────────────────────────────────────────────────


def _event_for(extracted: dict) -> TriggerEvent:
    return TriggerEvent(
        tenant_id=1,
        channel="wa",
        contact_id=2,
        inbound_row_id="3",
        inbound_row_model="team_inbox.Messages",
        body_text=_trigger_body_text(_content(extracted)),
        received_at="2026-09-11T00:00:00Z",
    )


@pytest.mark.parametrize(
    ("label", "extracted"),
    [
        ("plain text", {"text": "HELP me please"}),
        ("button reply", {"button_title": "help"}),
        ("captioned image", {"image_link": "https://x/i.jpg", "text": "help, broken screen"}),
    ],
)
def test_a_keyword_trigger_actually_fires(label, extracted):
    """The claim the subsystem exists to make, from webhook payload to match."""
    assert InboundKeywordMatch().matches(_event_for(extracted), {"keywords": ["help"]}), label


def test_case_sensitive_configs_match_on_content_not_on_dict_keys():
    """The silent half of #270.

    With ``case_sensitive`` set, the old code raised nothing at all — ``in``
    tested the dict's keys, found no keyword, and returned False. This pins
    that the comparison now runs against the message text.
    """
    event = _event_for({"text": "please HELP"})
    assert InboundKeywordMatch().matches(event, {"keywords": ["HELP"], "case_sensitive": True})
    assert not InboundKeywordMatch().matches(event, {"keywords": ["help"], "case_sensitive": True})


def test_a_non_matching_message_still_does_not_fire():
    event = _event_for({"text": "just saying hello"})
    assert not InboundKeywordMatch().matches(event, {"keywords": ["refund"]})


def test_an_uncaptioned_image_does_not_fire():
    """No text means no keyword match — not a crash, and not a false positive."""
    event = _event_for({"image_link": "https://x/i.jpg"})
    assert event.body_text is None
    assert not InboundKeywordMatch().matches(event, {"keywords": ["help"]})
