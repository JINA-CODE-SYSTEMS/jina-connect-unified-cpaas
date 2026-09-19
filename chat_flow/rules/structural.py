"""
Structural validation rules for ChatFlow.

These rules validate the overall structure of the flow graph,
including node/edge relationships, unique IDs, and flow topology.

Rules (from RULES.MD):
- No self-loops (edge connecting node to itself)
- Unique node IDs
- Unique edge IDs
- Must have start node
- Should have end node
- Start cannot directly connect to end
- No simultaneous branching (except condition nodes)
"""

from typing import Any, Dict, List, Set

from ..constants import canonical_session_message_type
from .base import EdgeRule, FlowRule, NodeRule, RuleCategory, RuleSeverity, RuleViolation
from .registry import register


@register
class NoSelfLoopRule(EdgeRule):
    """An edge cannot connect a node to itself."""

    rule_id = "STRUCT_001"
    description = "An edge cannot connect a node to itself (no self-loops)"
    category = RuleCategory.STRUCTURAL

    def validate_edge(
        self, edge: Dict[str, Any], source_node: Dict[str, Any], target_node: Dict[str, Any], flow_data: Dict[str, Any]
    ) -> List[RuleViolation]:
        if edge.get("source") == edge.get("target"):
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Edge connects node '{edge.get('source')}' to itself",
                    edge_id=edge.get("id"),
                    node_id=edge.get("source"),
                    severity=self.severity,
                )
            ]
        return []


@register
class UniqueNodeIdsRule(FlowRule):
    """Node IDs must be unique within a flow."""

    rule_id = "STRUCT_002"
    description = "Node IDs must be unique within a flow - no duplicates allowed"
    category = RuleCategory.STRUCTURAL

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_ids = [n.get("id") for n in flow_data.get("nodes", [])]

        seen: Set[str] = set()
        duplicates: Set[str] = set()

        for node_id in node_ids:
            if node_id in seen:
                duplicates.add(node_id)
            seen.add(node_id)

        for dup in duplicates:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Duplicate node ID found: '{dup}'",
                    node_id=dup,
                    severity=self.severity,
                )
            )

        return violations


@register
class UniqueEdgeIdsRule(FlowRule):
    """Edge IDs must be unique within a flow."""

    rule_id = "STRUCT_003"
    description = "Edge IDs must be unique within a flow - no duplicates allowed"
    category = RuleCategory.STRUCTURAL

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        edge_ids = [e.get("id") for e in flow_data.get("edges", [])]

        seen: Set[str] = set()
        duplicates: Set[str] = set()

        for edge_id in edge_ids:
            if edge_id in seen:
                duplicates.add(edge_id)
            seen.add(edge_id)

        for dup in duplicates:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Duplicate edge ID found: '{dup}'",
                    edge_id=dup,
                    severity=self.severity,
                )
            )

        return violations


@register
class StartNodeRequiredRule(FlowRule):
    """Every flow must have at least one start node."""

    rule_id = "STRUCT_004"
    description = "Every flow must have at least one 'start' node as the entry point"
    category = RuleCategory.STRUCTURAL

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        nodes = flow_data.get("nodes", [])
        has_start = any(n.get("type") == "start" for n in nodes)

        if not has_start:
            return [
                RuleViolation(
                    rule_id=self.rule_id, message="Flow must have at least one 'start' node", severity=self.severity
                )
            ]
        return []


@register
class EndNodeRecommendedRule(FlowRule):
    """Every flow should have at least one end node."""

    rule_id = "STRUCT_005"
    description = "Every flow should have at least one 'end' node to properly terminate"
    category = RuleCategory.STRUCTURAL
    severity = RuleSeverity.WARNING  # Warning, not error

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        nodes = flow_data.get("nodes", [])
        has_end = any(n.get("type") == "end" for n in nodes)

        if not has_end:
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message="Flow should have at least one 'end' node for proper termination",
                    severity=self.severity,
                )
            ]
        return []


@register
class StartCannotConnectDirectlyToEndRule(EdgeRule):
    """Start node cannot directly connect to end node."""

    rule_id = "STRUCT_006"
    description = "Start flow cannot directly link with end"
    category = RuleCategory.STRUCTURAL

    def validate_edge(
        self, edge: Dict[str, Any], source_node: Dict[str, Any], target_node: Dict[str, Any], flow_data: Dict[str, Any]
    ) -> List[RuleViolation]:
        if not source_node or not target_node:
            return []

        source_type = source_node.get("type")
        target_type = target_node.get("type")

        if source_type == "start" and target_type == "end":
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message="Start node cannot connect directly to end node - add at least one message",
                    edge_id=edge.get("id"),
                    severity=self.severity,
                )
            ]
        return []


@register
class NoConsecutiveSameTypeNodesRule(EdgeRule):
    """No two nodes of same type should connect directly (except quick reply)."""

    rule_id = "STRUCT_007"
    description = "No two nodes should be connected directly if of same type (exception for quick reply)"
    category = RuleCategory.STRUCTURAL
    severity = RuleSeverity.WARNING

    # Types that are allowed to connect to themselves
    _exempt_types = {"condition", "start", "end"}

    def validate_edge(
        self, edge: Dict[str, Any], source_node: Dict[str, Any], target_node: Dict[str, Any], flow_data: Dict[str, Any]
    ) -> List[RuleViolation]:
        if not source_node or not target_node:
            return []

        source_type = source_node.get("type")
        target_type = target_node.get("type")

        # Same type and not exempt
        if source_type == target_type and source_type not in self._exempt_types:
            # Message nodes with interactive buttons route via button handles
            # — connecting to another message node is perfectly valid.
            source_handle = edge.get("sourceHandle", "")
            if source_type == "message" and source_handle and source_handle.startswith("button-"):
                return []

            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Two '{source_type}' nodes connected directly - consider adding a condition node between them",
                    edge_id=edge.get("id"),
                    node_id=source_node.get("id"),
                    severity=self.severity,
                    details={"source_type": source_type, "target_type": target_type},
                )
            ]
        return []


@register
class ConditionNodeMustHaveTwoEdgesRule(NodeRule):
    """Condition nodes must have exactly two outgoing edges (if/else)."""

    rule_id = "STRUCT_008"
    description = "Condition nodes must have exactly two outgoing edges (if/else branches)"
    category = RuleCategory.STRUCTURAL
    applies_to_node_type = "condition"

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        node_id = node.get("id")
        edges = flow_data.get("edges", [])

        # Count outgoing edges from this node
        outgoing_count = sum(1 for e in edges if e.get("source") == node_id)

        if outgoing_count != 2:
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Condition node '{node_id}' has {outgoing_count} outgoing edges, but must have exactly 2 (if/else)",
                    node_id=node_id,
                    severity=self.severity,
                    details={"outgoing_count": outgoing_count, "expected": 2},
                )
            ]
        return []


@register
class EdgeReferencesValidNodesRule(FlowRule):
    """All edges must reference existing nodes."""

    rule_id = "STRUCT_009"
    description = "All edges must reference existing source and target nodes"
    category = RuleCategory.STRUCTURAL

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        nodes = flow_data.get("nodes", [])
        edges = flow_data.get("edges", [])

        node_ids = {n.get("id") for n in nodes}

        for edge in edges:
            edge_id = edge.get("id")
            source = edge.get("source")
            target = edge.get("target")

            if source not in node_ids:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Edge references unknown source node: '{source}'",
                        edge_id=edge_id,
                        severity=self.severity,
                        details={"missing_node": source, "position": "source"},
                    )
                )

            if target not in node_ids:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Edge references unknown target node: '{target}'",
                        edge_id=edge_id,
                        severity=self.severity,
                        details={"missing_node": target, "position": "target"},
                    )
                )

        return violations


@register
class OnlyConditionNodeCanBranchRule(FlowRule):
    """Only condition nodes can have multiple outgoing edges."""

    rule_id = "STRUCT_010"
    description = "Only condition nodes can have multiple outgoing edges (no simultaneous branching)"
    category = RuleCategory.STRUCTURAL

    # Node types always allowed to have multiple outgoing edges
    _branching_allowed = {"condition", "template", "api"}  # template/api can branch via buttons/status

    def _node_has_interactive_buttons(self, node: Dict[str, Any]) -> bool:
        """Check if a message node has interactive options that justify multiple edges.

        Buttons live under 'buttons', list rows under 'sections' — a list node
        routed one edge per row has the latter and none of the former (#273).
        """
        node_data = node.get("data", {})
        buttons = node_data.get("buttons", [])
        sections = node_data.get("sections", [])
        msg_type = canonical_session_message_type(node_data.get("message_type"))
        if msg_type == "interactive_button":
            return bool(buttons)
        if msg_type == "interactive_list":
            return any(s.get("rows") for s in sections)
        return False

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        nodes = flow_data.get("nodes", [])
        edges = flow_data.get("edges", [])

        # Count outgoing edges per node
        outgoing_count: Dict[str, int] = {}
        for edge in edges:
            source = edge.get("source")
            outgoing_count[source] = outgoing_count.get(source, 0) + 1

        # Check each node
        for node in nodes:
            node_id = node.get("id")
            node_type = node.get("type", "template")
            count = outgoing_count.get(node_id, 0)

            if count <= 1:
                continue

            # Always-allowed branching types
            if node_type in self._branching_allowed:
                continue

            # Message nodes with interactive buttons are allowed to branch
            if node_type == "message" and self._node_has_interactive_buttons(node):
                continue

            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Node '{node_id}' ({node_type}) has {count} outgoing edges - use a condition node for branching",
                    node_id=node_id,
                    severity=self.severity,
                    details={"node_type": node_type, "outgoing_count": count},
                )
            )

        return violations


@register
class OneTargetPerButtonRule(FlowRule):
    """A button may route to exactly one node.

    Two edges leaving the same button handle is not an error the editor or the
    server ever reported, and at runtime it does not branch — it silently drops
    one of them. ``graph_executor`` builds routing as
    ``button_routes[btn_text] = edge.target_node.node_id``, a plain dict
    assignment, so whichever edge the database happens to return last wins and
    the other target is never reachable.

    The flow therefore saves, validates, and quietly does something other than
    what the canvas shows — which is worse than a refusal, because the canvas
    keeps drawing the branch that will never be taken.
    """

    rule_id = "STRUCT_011"
    description = "Each button may route to exactly one node - two edges from one button silently discard one"
    category = RuleCategory.STRUCTURAL

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []

        # Keyed on (source node, handle) rather than on button text: the handle
        # is what the canvas actually connects, and two buttons may legitimately
        # carry the same label on different nodes.
        targets: Dict[tuple, List[Dict[str, Any]]] = {}
        for edge in flow_data.get("edges", []):
            handle = edge.get("sourceHandle") or ""
            if not handle.startswith("button-"):
                continue
            targets.setdefault((edge.get("source"), handle), []).append(edge)

        for (node_id, handle), edges in targets.items():
            if len(edges) < 2:
                continue

            # Named rather than counted. An operator looking at a canvas with
            # two lines leaving one button needs to know which button and where
            # both lines go, or they cannot tell which one to delete.
            label = next((e.get("data", {}).get("button_text") for e in edges if e.get("data")), None) or handle
            destinations = sorted({str(e.get("target")) for e in edges})
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=(
                        f"Button '{label}' on node '{node_id}' routes to {len(destinations)} different nodes "
                        f"({', '.join(destinations)}). A button can only go to one, and the others are "
                        f"silently ignored when the flow runs - delete all but one."
                    ),
                    node_id=node_id,
                    severity=self.severity,
                    details={"handle": handle, "targets": destinations},
                )
            )

        return violations


#: Node types that end the conversation themselves, so they need no successor.
#:
#: ``end`` is terminal by definition. ``handoff`` earns its place: it reassigns
#: the contact to an agent or to the unassigned queue, so the contact is no
#: longer held by this flow — its own docstring notes that a following end
#: node's unassignment is a no-op by then. Every other type leaves the contact
#: assigned to the flow, so stopping there strands them.
TERMINATING_NODE_TYPES = frozenset({"end", "handoff"})


@register
class EveryPathMustLeadSomewhereRule(FlowRule):
    """A node that is not a terminus must have somewhere to go.

    Nothing checked this. ``STRUCT_005`` asks only that an end node *exists*
    somewhere in the flow, not that anything reaches one, so a session message
    reading "Auto-routes to next node" could be saved with nothing to route to
    — a promise the canvas makes and the validator never tested.

    The cost is not cosmetic, and it is silent. Reaching an end node runs the
    end handler: the session is marked complete and the contact is unassigned
    from the flow. Running out of edges instead returns LangGraph's ``END``
    straight from the router, which does neither — it logs one warning nobody
    reads, the session stays active and incomplete, and **the contact is left
    assigned to a flow that has finished**, which changes how their later
    messages are routed.
    """

    rule_id = "STRUCT_012"
    description = "Every node that is not a terminus must have an outgoing edge"
    category = RuleCategory.STRUCTURAL

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []

        sources = {edge.get("source") for edge in flow_data.get("edges", [])}

        for node in flow_data.get("nodes", []):
            node_id = node.get("id")
            node_type = (node.get("data", {}).get("nodeType") or node.get("type") or "").lower()

            if node_type in TERMINATING_NODE_TYPES or node_id in sources:
                continue

            label = node.get("data", {}).get("label") or node_type or node_id
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=(
                        f"'{label}' has no outgoing connection, so the flow stops there without "
                        f"reaching an End node - the contact stays assigned to this flow after it "
                        f"has finished. Connect it to an End node, or to whatever comes next."
                    ),
                    node_id=node_id,
                    severity=self.severity,
                    details={"node_type": node_type},
                )
            )

        return violations


@register
class OneStartNodeRule(FlowRule):
    """A flow has exactly one entry point.

    ``STRUCT_004`` requires at least one start node and nothing forbade a
    second. The node palette caps start at one instance, so this could not be
    drawn — but a flow arriving by API or import could carry two, and which of
    them ran would be whichever the graph builder happened to reach first.

    A constraint enforced on one side of the API and not the other is the same
    shape as the rule categories the builder could not match: the client is the
    only thing holding it, and anything that is not the client bypasses it.
    """

    rule_id = "STRUCT_013"
    description = "A flow must have exactly one start node"
    category = RuleCategory.STRUCTURAL

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        start_ids = [
            node.get("id")
            for node in flow_data.get("nodes", [])
            if (node.get("data", {}).get("nodeType") or node.get("type") or "").lower() == "start"
        ]

        # The absence of a start node is STRUCT_004's to report; saying it
        # twice would make one defect read as two.
        if len(start_ids) < 2:
            return []

        return [
            RuleViolation(
                rule_id=self.rule_id,
                message=(
                    f"This flow has {len(start_ids)} start nodes ({', '.join(str(i) for i in start_ids)}). "
                    f"A flow has one entry point - which of these ran would be arbitrary."
                ),
                node_id=start_ids[1],
                severity=self.severity,
                details={"start_node_ids": start_ids},
            )
        ]
