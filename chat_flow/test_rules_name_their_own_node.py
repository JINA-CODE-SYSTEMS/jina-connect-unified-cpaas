"""Every node type's rules must be findable from that node type.

Reported from the flow builder: hovering delay, handoff and API in the node
palette showed the *same* list of rules. It was not a UI glitch — those three
node types had no rules of their own that the builder could find.

A rule's ``category`` is what the builder matches against a node type. The
categories the builder looks for are ``delay``, ``handoff`` and ``api``; the
categories the rules carried were ``structural`` (delay) and ``whatsapp``
(handoff, API), and ``whatsapp`` maps to no node type at all. So sixteen real
rules — eight for API nodes, eight for handoff — were invisible everywhere,
and the delay rules were buried among the generic structural ones. All three
node types fell back to the same structural list, which is what the report saw.

The tests are about reachability rather than counts: a rule filed under a
category no node type maps to is dead documentation however correct it is.

HOW TO RUN:
    python -m pytest chat_flow/test_rules_name_their_own_node.py -v
"""

from __future__ import annotations

import pytest

from chat_flow.rules.base import RuleCategory
from chat_flow.rules.registry import RuleRegistry

# The builder's map from a palette node type to the categories it shows.
# Mirrored from the web client so that a category renamed on one side without
# the other fails here rather than by showing an operator the wrong rules.
NODE_TYPE_TO_CATEGORIES = {
    "template": {RuleCategory.STRUCTURAL, RuleCategory.TEMPLATE, RuleCategory.BUTTON},
    "message": {RuleCategory.STRUCTURAL, RuleCategory.SESSION_MESSAGE, RuleCategory.BUTTON},
    "delay": {RuleCategory.STRUCTURAL, RuleCategory.DELAY},
    "handoff": {RuleCategory.STRUCTURAL, RuleCategory.HANDOFF},
    "api": {RuleCategory.STRUCTURAL, RuleCategory.API},
}


def _by_category():
    grouped: dict[str, list[str]] = {}
    for rule in RuleRegistry.get_all_rules():
        grouped.setdefault(rule.category, []).append(rule.rule_id)
    return grouped


@pytest.mark.parametrize("node_type", ["delay", "handoff", "api"])
def test_a_node_type_has_rules_of_its_own(node_type):
    """The reported symptom, as a property rather than a screenshot.

    Each of these showed only the shared structural list, because it had
    nothing else the builder could reach.
    """
    own_category = RuleCategory(node_type)
    own_rules = _by_category().get(own_category, [])

    assert own_rules, f"{node_type} nodes have no rules filed under '{own_category}'"


def test_delay_handoff_and_api_do_not_all_see_the_same_thing():
    """The report in one assertion: three node types, three different lists."""
    grouped = _by_category()

    def visible(node_type):
        return {rule_id for category in NODE_TYPE_TO_CATEGORIES[node_type] for rule_id in grouped.get(category, [])}

    delay, handoff, api = visible("delay"), visible("handoff"), visible("api")

    assert delay != handoff
    assert handoff != api
    assert delay != api


def test_no_rule_is_filed_where_no_node_type_can_reach_it():
    """A rule nobody can see is documentation that does not exist.

    ``whatsapp`` held sixteen of them. The check is written against the
    builder's own map so that inventing a new category without giving a node
    type a way to reach it fails here.
    """
    reachable = set().union(*NODE_TYPE_TO_CATEGORIES.values())
    # EDGE rules are reported against an edge rather than a node, so no node
    # type maps to them and that is correct.
    reachable.add(RuleCategory.EDGE)

    orphaned = {category: rule_ids for category, rule_ids in _by_category().items() if category not in reachable}

    assert not orphaned, f"rules no node type can show: {orphaned}"


def test_every_registered_rule_carries_a_known_category():
    """Guards against a stray string where the enum was meant."""
    known = {c.value for c in RuleCategory}

    for rule in RuleRegistry.get_all_rules():
        assert rule.category in known, f"{rule.rule_id} has unknown category {rule.category!r}"


def test_the_structural_list_is_still_shared_by_everyone():
    """The fix must not go the other way and hide the genuinely shared rules."""
    grouped = _by_category()
    structural = grouped.get(RuleCategory.STRUCTURAL, [])

    assert structural, "structural rules exist"
    for node_type, categories in NODE_TYPE_TO_CATEGORIES.items():
        assert RuleCategory.STRUCTURAL in categories, node_type
