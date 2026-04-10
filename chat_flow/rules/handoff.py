"""
Handoff Node Validation Rules

Rules for validating handoff nodes in ChatFlow.
Handoff nodes transfer the conversation to human agents in Team Inbox.
"""

from typing import Any, Dict, List, Optional

from .base import NodeRule, RuleCategory, RuleSeverity, RuleViolation
from .registry import register


@register
class HandoffNodeRequired(NodeRule):
    """Handoff node must have basic configuration."""
    
    rule_id = "HANDOFF_001"
    description = "Handoff node must have a label"
    severity = RuleSeverity.ERROR
    category = RuleCategory.WHATSAPP
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []
        
        if node.get('type') != 'handoff':
            return violations
        
        node_data = node.get('data', {})
        label = node_data.get('label', '').strip()
        
        if not label:
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message="Handoff node must have a label to identify it",
                severity=self.severity,
                node_id=node.get('id'),
                details={"field": "label"}
            ))
        
        return violations


@register
class HandoffNodePriorityValid(NodeRule):
    """Handoff node priority should be valid if specified."""
    
    rule_id = "HANDOFF_002"
    description = "Handoff node priority must be valid"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP
    
    VALID_PRIORITIES = ('low', 'normal', 'high', 'urgent')
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []
        
        if node.get('type') != 'handoff':
            return violations
        
        node_data = node.get('data', {})
        priority = node_data.get('priority')
        
        # Priority is optional, but if provided must be valid
        if priority is not None:
            if not isinstance(priority, str) or priority.lower() not in self.VALID_PRIORITIES:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Invalid priority '{priority}'. Must be one of: {', '.join(self.VALID_PRIORITIES)}",
                    severity=self.severity,
                    node_id=node.get('id'),
                    details={
                        "field": "priority",
                        "value": priority,
                        "valid_values": self.VALID_PRIORITIES
                    }
                ))
        
        return violations


@register
class HandoffNodeAssignmentValid(NodeRule):
    """Handoff node assignment configuration should be valid."""
    
    rule_id = "HANDOFF_003"
    description = "Handoff node assignment must be properly configured"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP
    
    VALID_ASSIGNMENT_TYPES = ('auto', 'team', 'agent', 'round_robin', 'least_busy')
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []
        
        if node.get('type') != 'handoff':
            return violations
        
        node_data = node.get('data', {})
        assignment_type = node_data.get('assignment_type')
        team_id = node_data.get('team_id')
        agent_id = node_data.get('agent_id')
        
        # Validate assignment_type if provided
        if assignment_type is not None:
            if assignment_type not in self.VALID_ASSIGNMENT_TYPES:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Invalid assignment type '{assignment_type}'. Must be one of: {', '.join(self.VALID_ASSIGNMENT_TYPES)}",
                    severity=self.severity,
                    node_id=node.get('id'),
                    details={
                        "field": "assignment_type",
                        "value": assignment_type,
                        "valid_values": self.VALID_ASSIGNMENT_TYPES
                    }
                ))
            
            # If assignment is to team, team_id should be provided
            if assignment_type == 'team' and not team_id:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message="Team assignment requires a team_id",
                    severity=RuleSeverity.WARNING,
                    node_id=node.get('id'),
                    details={"field": "team_id", "assignment_type": assignment_type}
                ))
            
            # If assignment is to agent, agent_id should be provided
            if assignment_type == 'agent' and not agent_id:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message="Agent assignment requires an agent_id",
                    severity=RuleSeverity.WARNING,
                    node_id=node.get('id'),
                    details={"field": "agent_id", "assignment_type": assignment_type}
                ))
        
        return violations


@register
class HandoffNodeMessageValid(NodeRule):
    """Handoff node handoff message should be valid if provided."""
    
    rule_id = "HANDOFF_004"
    description = "Handoff message to user should be properly formatted"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []
        
        if node.get('type') != 'handoff':
            return violations
        
        node_data = node.get('data', {})
        handoff_message = node_data.get('handoff_message')
        
        # Message is optional, but if provided should be valid
        if handoff_message is not None:
            if not isinstance(handoff_message, str):
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message="Handoff message must be a string",
                    severity=RuleSeverity.ERROR,
                    node_id=node.get('id'),
                    details={"field": "handoff_message", "type": type(handoff_message).__name__}
                ))
            elif len(handoff_message.strip()) == 0:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message="Handoff message cannot be empty if provided",
                    severity=self.severity,
                    node_id=node.get('id'),
                    details={"field": "handoff_message"}
                ))
            elif len(handoff_message) > 1024:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Handoff message is too long ({len(handoff_message)} chars). Maximum is 1024 characters.",
                    severity=self.severity,
                    node_id=node.get('id'),
                    details={"field": "handoff_message", "length": len(handoff_message), "max_length": 1024}
                ))
        
        return violations


@register
class HandoffNodeInternalNoteValid(NodeRule):
    """Handoff node internal note for agents should be valid."""
    
    rule_id = "HANDOFF_005"
    description = "Internal note for agents should be properly formatted"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []
        
        if node.get('type') != 'handoff':
            return violations
        
        node_data = node.get('data', {})
        internal_note = node_data.get('internal_note')
        
        # Internal note is optional
        if internal_note is not None:
            if not isinstance(internal_note, str):
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message="Internal note must be a string",
                    severity=RuleSeverity.ERROR,
                    node_id=node.get('id'),
                    details={"field": "internal_note", "type": type(internal_note).__name__}
                ))
            elif len(internal_note) > 2048:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Internal note is too long ({len(internal_note)} chars). Maximum is 2048 characters.",
                    severity=self.severity,
                    node_id=node.get('id'),
                    details={"field": "internal_note", "length": len(internal_note), "max_length": 2048}
                ))
        
        return violations


@register
class HandoffNodePlacement(NodeRule):
    """Handoff node should be reachable and properly connected."""
    
    rule_id = "HANDOFF_006"
    description = "Handoff node should be reachable from the flow"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []
        
        if node.get('type') != 'handoff':
            return violations
        
        node_id = node.get('id')
        edges = flow_data.get('edges', [])
        
        # Check if handoff node has any incoming edges
        incoming_edges = [e for e in edges if e.get('target') == node_id]
        
        if not incoming_edges:
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message="Handoff node is not connected to any other node. It will never be reached.",
                severity=self.severity,
                node_id=node_id,
                details={}
            ))
        
        return violations


@register
class HandoffNodeTagsValid(NodeRule):
    """Handoff node tags should be valid if provided."""
    
    rule_id = "HANDOFF_007"
    description = "Handoff tags must be a list of strings"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []
        
        if node.get('type') != 'handoff':
            return violations
        
        node_data = node.get('data', {})
        tags = node_data.get('tags')
        
        # Tags are optional
        if tags is not None:
            if not isinstance(tags, list):
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message="Tags must be a list",
                    severity=RuleSeverity.ERROR,
                    node_id=node.get('id'),
                    details={"field": "tags", "type": type(tags).__name__}
                ))
            else:
                # Validate each tag
                for i, tag in enumerate(tags):
                    if not isinstance(tag, str):
                        violations.append(RuleViolation(
                            rule_id=self.rule_id,
                            message=f"Tag at index {i} must be a string",
                            severity=self.severity,
                            node_id=node.get('id'),
                            details={"field": "tags", "index": i, "type": type(tag).__name__}
                        ))
                    elif len(tag.strip()) == 0:
                        violations.append(RuleViolation(
                            rule_id=self.rule_id,
                            message=f"Tag at index {i} cannot be empty",
                            severity=self.severity,
                            node_id=node.get('id'),
                            details={"field": "tags", "index": i}
                        ))
        
        return violations


@register
class HandoffNodeMustTerminateToEnd(NodeRule):
    """Handoff node must connect to an end node (flow terminates after handoff)."""
    
    rule_id = "HANDOFF_008"
    description = "Handoff node must terminate to an end node"
    severity = RuleSeverity.ERROR
    category = RuleCategory.WHATSAPP
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []
        
        if node.get('type') != 'handoff':
            return violations
        
        node_id = node.get('id')
        edges = flow_data.get('edges', [])
        nodes = flow_data.get('nodes', [])
        
        # Build a map of node_id -> node_type
        node_type_map = {n.get('id'): n.get('type') for n in nodes}
        
        # Find outgoing edges from this handoff node
        outgoing_edges = [e for e in edges if e.get('source') == node_id]
        
        if not outgoing_edges:
            # No outgoing edges - handoff should connect to end node
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message="Handoff node must connect to an end node. The flow should terminate after transferring to an agent.",
                severity=self.severity,
                node_id=node_id,
                details={"reason": "no_outgoing_edges"}
            ))
            return violations
        
        # Check that all outgoing edges go to end nodes
        for edge in outgoing_edges:
            target_id = edge.get('target')
            target_type = node_type_map.get(target_id)
            
            if target_type != 'end':
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Handoff node must connect to an end node, but connects to '{target_type}' node. "
                            f"The flow should terminate after transferring to an agent.",
                    severity=self.severity,
                    node_id=node_id,
                    edge_id=edge.get('id'),
                    details={
                        "target_node_id": target_id,
                        "target_node_type": target_type,
                        "expected_type": "end"
                    }
                ))
        
        return violations