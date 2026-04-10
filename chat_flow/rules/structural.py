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

from .base import (EdgeRule, FlowRule, NodeRule, RuleCategory, RuleSeverity,
                   RuleViolation)
from .registry import register


@register
class NoSelfLoopRule(EdgeRule):
    """An edge cannot connect a node to itself."""
    
    rule_id = "STRUCT_001"
    description = "An edge cannot connect a node to itself (no self-loops)"
    category = RuleCategory.STRUCTURAL
    
    def validate_edge(
        self,
        edge: Dict[str, Any],
        source_node: Dict[str, Any],
        target_node: Dict[str, Any],
        flow_data: Dict[str, Any]
    ) -> List[RuleViolation]:
        if edge.get("source") == edge.get("target"):
            return [RuleViolation(
                rule_id=self.rule_id,
                message=f"Edge connects node '{edge.get('source')}' to itself",
                edge_id=edge.get("id"),
                node_id=edge.get("source"),
                severity=self.severity
            )]
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
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message=f"Duplicate node ID found: '{dup}'",
                node_id=dup,
                severity=self.severity
            ))
        
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
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message=f"Duplicate edge ID found: '{dup}'",
                edge_id=dup,
                severity=self.severity
            ))
        
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
            return [RuleViolation(
                rule_id=self.rule_id,
                message="Flow must have at least one 'start' node",
                severity=self.severity
            )]
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
            return [RuleViolation(
                rule_id=self.rule_id,
                message="Flow should have at least one 'end' node for proper termination",
                severity=self.severity
            )]
        return []


@register
class StartCannotConnectDirectlyToEndRule(EdgeRule):
    """Start node cannot directly connect to end node."""
    
    rule_id = "STRUCT_006"
    description = "Start flow cannot directly link with end"
    category = RuleCategory.STRUCTURAL
    
    def validate_edge(
        self,
        edge: Dict[str, Any],
        source_node: Dict[str, Any],
        target_node: Dict[str, Any],
        flow_data: Dict[str, Any]
    ) -> List[RuleViolation]:
        if not source_node or not target_node:
            return []
        
        source_type = source_node.get("type")
        target_type = target_node.get("type")
        
        if source_type == "start" and target_type == "end":
            return [RuleViolation(
                rule_id=self.rule_id,
                message="Start node cannot connect directly to end node - add at least one message",
                edge_id=edge.get("id"),
                severity=self.severity
            )]
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
        self,
        edge: Dict[str, Any],
        source_node: Dict[str, Any],
        target_node: Dict[str, Any],
        flow_data: Dict[str, Any]
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

            return [RuleViolation(
                rule_id=self.rule_id,
                message=f"Two '{source_type}' nodes connected directly - consider adding a condition node between them",
                edge_id=edge.get("id"),
                node_id=source_node.get("id"),
                severity=self.severity,
                details={"source_type": source_type, "target_type": target_type}
            )]
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
            return [RuleViolation(
                rule_id=self.rule_id,
                message=f"Condition node '{node_id}' has {outgoing_count} outgoing edges, but must have exactly 2 (if/else)",
                node_id=node_id,
                severity=self.severity,
                details={"outgoing_count": outgoing_count, "expected": 2}
            )]
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
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Edge references unknown source node: '{source}'",
                    edge_id=edge_id,
                    severity=self.severity,
                    details={"missing_node": source, "position": "source"}
                ))
            
            if target not in node_ids:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Edge references unknown target node: '{target}'",
                    edge_id=edge_id,
                    severity=self.severity,
                    details={"missing_node": target, "position": "target"}
                ))
        
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
        """Check if a message node has interactive buttons that justify multiple edges."""
        node_data = node.get("data", {})
        buttons = node_data.get("buttons", [])
        msg_type = node_data.get("message_type", "")
        return bool(buttons) and msg_type in ("interactive_button", "interactive_list")
    
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
            
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message=f"Node '{node_id}' ({node_type}) has {count} outgoing edges - use a condition node for branching",
                node_id=node_id,
                severity=self.severity,
                details={"node_type": node_type, "outgoing_count": count}
            ))
        
        return violations
