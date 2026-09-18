"""Which characters a flow identifier may carry, and why an apostrophe is one.

The reported failure, from the flow editor, on a flow nobody had edited::

    Node 3 > buttons > id: Button ID cannot contain quotes, backslashes, or newlines

The node's three buttons were "Tell me more", "Let's have a call" and
"Not Interested". The frontend derives a button's synthetic id from its label
(``btn-{text}`` — ``flow_processor`` says so in as many words, and
``NodeButton.normalise_text_title`` reads the label back out of the id by
stripping that prefix), so the second button's id was ``btn-Let's have a call``.
Four validators rejected the apostrophe in it.

The rejection was written as "no special characters that could break JSON",
which is not a hazard that exists here: identifiers are stored in a JSONField
and serialized by ``json``, which escapes; nothing hand-builds JSON or SQL from
them, and the one place they reach HTML is ``format_html`` with a placeholder.
What the rule did instead was make a legal WhatsApp button label — apostrophes
and all, at 17 of the permitted 20 characters — unsavable.

So the rule now rejects what is genuinely unusable in an identifier and nothing
else: control characters, which are invisible (so an id carrying one compares
unequal to the one the operator believes they typed) and which break the
handle parsing and log lines these ids travel through.

HOW TO RUN:
    python -m pytest chat_flow/test_flow_identifier_charset.py -v
"""

from __future__ import annotations

import pytest

from chat_flow.validators import (
    EdgeData,
    NodeButton,
    ReactFlowEdge,
    ReactFlowNode,
    validate_reactflow_data,
)

# The exact labels from the reported flow.
REPORTED_BUTTONS = ["Tell me more", "Let's have a call", "Not Interested"]


def _node(buttons):
    return {
        "id": "node-1",
        "type": "template",
        "position": {"x": 0, "y": 0},
        "data": {
            "label": "Template",
            "buttons": [{"id": f"btn-{text}", "text": text} for text in buttons],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# The defect, closed
# ─────────────────────────────────────────────────────────────────────────────


def test_the_reported_flow_saves():
    """End to end through the entry point the editor actually calls.

    Asserted on ``validate_reactflow_data`` rather than on ``NodeButton``
    because that is the function whose error string the editor rendered, and
    a fix to the field alone would not prove the flow round-trips.
    """
    flow = validate_reactflow_data({"nodes": [_node(REPORTED_BUTTONS)], "edges": []})

    assert [b.id for b in flow.nodes[0].data.buttons] == [f"btn-{t}" for t in REPORTED_BUTTONS]
    assert [b.text for b in flow.nodes[0].data.buttons] == REPORTED_BUTTONS


@pytest.mark.parametrize(
    "identifier",
    [
        "btn-Let's have a call",
        'btn-Say "hello"',
        "btn-It's a 20% saving",
        "btn-Réserver",
        "btn-Back\\Forward",
    ],
    ids=["apostrophe", "double-quote", "apostrophe-and-percent", "accented", "backslash"],
)
def test_a_label_a_human_would_write_is_accepted(identifier):
    """Every one of these is a legal WhatsApp button label under 20 characters.

    The apostrophe case is the one that was reported; the rest are the same
    defect waiting for a different customer to write a different label.
    """
    assert NodeButton(id=identifier, text="ok").id == identifier
    assert EdgeData(button_id=identifier).button_id == identifier


def test_the_same_rule_applies_to_every_identifier():
    """All four validators held their own copy of this check.

    Node and edge ids are machine-generated today and so would not have hit
    the apostrophe — but four copies of one rule is how the copies drift, and
    this repository has closed that shape three times already (#265, #333,
    #365).
    """
    quoted = "it's-fine"

    assert ReactFlowNode(id=quoted, position={"x": 0, "y": 0}, data={"label": "L"}).id == quoted
    assert ReactFlowEdge(id=quoted, source="a", target="b").id == quoted
    assert NodeButton(id=quoted, text="ok").id == quoted
    assert EdgeData(button_id=quoted).button_id == quoted


# ─────────────────────────────────────────────────────────────────────────────
# What is still refused, and it is not nothing
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "identifier",
    ["btn-two\nlines", "btn-tab\there", "btn-carriage\rreturn", "btn-null\x00byte", "btn-bell\x07"],
    ids=["newline", "tab", "carriage-return", "null", "bell"],
)
def test_control_characters_are_still_refused(identifier):
    """Invisible in the editor, so an id carrying one silently fails to match.

    It is also the half of the original rule that was doing real work: these
    break the ``button-{n}`` handle parsing and split a log line in two.
    """
    with pytest.raises(ValueError, match="control character"):
        NodeButton(id=identifier, text="ok")


def test_the_message_names_the_character_and_the_value():
    """An error that misnames its own cause sends everyone to the wrong place.

    That is the #365 lesson, and this error had the same shape: "Node 3 >
    buttons > id" named the node but not which of its three buttons, and
    "quotes, backslashes, or newlines" described a rule rather than the
    offending input. A reader could not tell from it what to change.
    """
    with pytest.raises(ValueError) as caught:
        NodeButton(id="btn-two\nlines", text="ok")

    message = str(caught.value)
    assert "'\\n'" in message, "the character that was rejected"
    assert "btn-two\\nlines" in message, "the value it was rejected in"


def test_an_empty_identifier_is_still_refused():
    for blank in ("", "   ", "\t"):
        with pytest.raises(ValueError):
            NodeButton(id=blank, text="ok")


# ─────────────────────────────────────────────────────────────────────────────
# The error the reported flow hits *next*, once its ids are accepted
# ─────────────────────────────────────────────────────────────────────────────


def _reported_canvas(source_handle):
    """Start -> Template(3 quick replies) -> End, as drawn in the report.

    The edge leaves the template's own handle rather than any button's, which
    is what the canvas showed: all three buttons carried the not-connected
    marker.
    """
    return {
        "nodes": [
            {"id": "start-1", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "Start"}},
            {
                "id": "tpl-1",
                "type": "template",
                "position": {"x": 300, "y": 0},
                "data": {
                    "label": "Template",
                    "buttons": [{"id": f"btn-{t}", "text": t, "type": "QUICK_REPLY"} for t in REPORTED_BUTTONS],
                },
            },
            {"id": "end-1", "type": "end", "position": {"x": 600, "y": 0}, "data": {"label": "End"}},
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "tpl-1"},
            {"id": "e2", "source": "tpl-1", "target": "end-1", "sourceHandle": source_handle},
        ],
    }


@pytest.mark.parametrize("handle", ["bottom", None], ids=["bottom-handle", "no-handle"])
def test_an_unrouted_quick_reply_names_the_buttons_it_means(handle):
    """The rule is right and the wording was not.

    A template whose replies go nowhere is a real structural problem, so this
    still refuses. But it used to refuse with "Template has interactive
    buttons (Good, Bad, etc.)" — buttons from some other flow — leaving a
    reader with a canvas containing no "Good" and no "Bad" unable to tell
    whether the message was even about their node.
    """
    with pytest.raises(ValueError) as caught:
        validate_reactflow_data(_reported_canvas(handle))

    message = str(caught.value)
    assert "tpl-1" in message, "which node"
    for label in REPORTED_BUTTONS:
        assert label in message, f"the buttons it actually has, missing {label!r}"
    assert "Good, Bad" not in message


def test_routing_from_a_button_handle_saves():
    """The other half: the fix for that error, proved to work."""
    flow = validate_reactflow_data(_reported_canvas("button-0"))

    assert len(flow.edges) == 2


def test_a_label_derived_from_an_id_trims_a_prefix_rather_than_every_match():
    """``btn-`` is a prefix, and was being removed wherever it appeared.

    The template extractor issues ``template-btn-{i}``, so a button arriving
    with that id and no label of its own was named "template1".
    """
    assert NodeButton(id="btn-Call me").text == "Call me"
    assert NodeButton(id="template-btn-1").text == "template-btn-1"


def test_the_length_caps_that_existed_still_hold():
    """Unchanged by this fix, and asserted so that relaxing one rule is not
    read as relaxing the ones beside it."""
    with pytest.raises(ValueError, match="too long"):
        ReactFlowNode(id="n" * 256, position={"x": 0, "y": 0}, data={"label": "L"})

    with pytest.raises(ValueError, match="too long"):
        ReactFlowEdge(id="e" * 256, source="a", target="b")
