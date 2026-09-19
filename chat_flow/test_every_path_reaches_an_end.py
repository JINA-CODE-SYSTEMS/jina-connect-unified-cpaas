"""A flow that stops without reaching an End node strands the contact.

Reported from the canvas: two session messages reading "Auto-routes to next
node" with nothing to route to, and the flow saved anyway.

Nothing checked it. STRUCT_005 asks only that an end node *exists* somewhere,
not that anything reaches one — so the label was a promise the validator never
tested. The two paths are not equivalent:

* reaching an end node runs the end handler — the session is marked complete
  and the contact is unassigned from the flow;
* running out of edges returns LangGraph's ``END`` straight from the router,
  which does neither. One warning is logged, the session stays active and
  incomplete, and the contact is left assigned to a flow that has finished.

``handoff`` is the one honest exception: it reassigns the contact to an agent
or to the unassigned queue, so the flow is no longer holding them.

Also here: a flow may have exactly one start node. The palette caps it at one,
so it cannot be drawn — but a flow arriving by API or import could carry two,
and a constraint only the client enforces is not a constraint.

HOW TO RUN:
    python -m pytest chat_flow/test_every_path_reaches_an_end.py -v
"""

from __future__ import annotations

from chat_flow.rules.structural import EveryPathMustLeadSomewhereRule, OneStartNodeRule


def _node(node_id, node_type, label=None):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"label": label or node_id, "nodeType": node_type},
    }


def _edge(source, target):
    return {"id": f"{source}->{target}", "source": source, "target": target}


def _dangling():
    """The reported canvas: a session message wired in, wired to nothing."""
    return {
        "nodes": [
            _node("start-1", "start"),
            _node("tpl-1", "template"),
            _node("msg-1", "message", label="End of 'call' branch"),
            _node("end-1", "end"),
        ],
        "edges": [_edge("start-1", "tpl-1"), _edge("tpl-1", "msg-1")],
    }


def _violations(graph):
    return EveryPathMustLeadSomewhereRule().validate(graph)


# ─────────────────────────────────────────────────────────────────────────────
# STRUCT_012 — every path leads somewhere
# ─────────────────────────────────────────────────────────────────────────────


def test_a_node_wired_to_nothing_is_reported():
    violations = _violations(_dangling())

    assert len(violations) == 1
    assert violations[0].node_id == "msg-1"
    assert "End of 'call' branch" in violations[0].message, "names the node the operator sees"
    assert "stays assigned" in violations[0].message, "says what it costs, not just that it is wrong"


def test_wiring_it_to_an_end_node_clears_it():
    graph = _dangling()
    graph["edges"].append(_edge("msg-1", "end-1"))

    assert _violations(graph) == []


def test_an_end_node_needs_no_successor():
    """Terminal by definition — the rule must not report every flow's own End."""
    graph = _dangling()
    graph["edges"].append(_edge("msg-1", "end-1"))

    assert all(v.node_id != "end-1" for v in _violations(graph))


def test_a_handoff_may_end_the_flow():
    """The one honest exception.

    A handoff reassigns the contact to an agent or the unassigned queue, so the
    flow is no longer holding them — a following end node's unassignment would
    be a no-op. Flagging it would report a legitimate shape.
    """
    graph = {
        "nodes": [_node("start-1", "start"), _node("ho-1", "handoff"), _node("end-1", "end")],
        "edges": [_edge("start-1", "ho-1")],
    }

    assert _violations(graph) == []


def test_a_node_nobody_dropped_an_edge_on_is_still_reported():
    """An orphan left on the canvas is the same defect, arrived at differently."""
    graph = {
        "nodes": [_node("start-1", "start"), _node("end-1", "end"), _node("stray", "message")],
        "edges": [_edge("start-1", "end-1")],
    }

    assert [v.node_id for v in _violations(graph)] == ["stray"]


def test_several_dead_ends_are_reported_separately():
    """One row per node, because each needs its own edge drawn."""
    graph = {
        "nodes": [
            _node("start-1", "start"),
            _node("tpl-1", "template"),
            _node("msg-1", "message"),
            _node("msg-2", "message"),
            _node("end-1", "end"),
        ],
        "edges": [_edge("start-1", "tpl-1"), _edge("tpl-1", "msg-1"), _edge("tpl-1", "msg-2")],
    }

    assert sorted(v.node_id for v in _violations(graph)) == ["msg-1", "msg-2"]


def test_several_end_nodes_are_fine():
    """Multiple terminators keep a branching flow readable, and each behaves
    identically — so the rule must not push everything back to one End."""
    graph = {
        "nodes": [
            _node("start-1", "start"),
            _node("tpl-1", "template"),
            _node("msg-1", "message"),
            _node("msg-2", "message"),
            _node("end-1", "end"),
            _node("end-2", "end"),
        ],
        "edges": [
            _edge("start-1", "tpl-1"),
            _edge("tpl-1", "msg-1"),
            _edge("tpl-1", "msg-2"),
            _edge("msg-1", "end-1"),
            _edge("msg-2", "end-2"),
        ],
    }

    assert _violations(graph) == []


# ─────────────────────────────────────────────────────────────────────────────
# STRUCT_013 — one way in
# ─────────────────────────────────────────────────────────────────────────────


def test_two_start_nodes_are_reported():
    graph = {
        "nodes": [_node("start-1", "start"), _node("start-2", "start"), _node("end-1", "end")],
        "edges": [],
    }

    violations = OneStartNodeRule().validate(graph)

    assert len(violations) == 1
    assert "start-1" in violations[0].message and "start-2" in violations[0].message


def test_one_start_node_is_correct():
    graph = {"nodes": [_node("start-1", "start"), _node("end-1", "end")], "edges": []}

    assert OneStartNodeRule().validate(graph) == []


def test_a_missing_start_is_left_to_the_rule_that_owns_it():
    """STRUCT_004 reports that. Saying it twice makes one defect read as two."""
    graph = {"nodes": [_node("end-1", "end")], "edges": []}

    assert OneStartNodeRule().validate(graph) == []
