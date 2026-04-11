"""
Base classes for ChatFlow validation rules.

This module provides the foundation for the rule engine pattern,
allowing extensible, testable, and self-documenting validation rules.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RuleSeverity(str, Enum):
    """Severity levels for rule violations."""

    ERROR = "error"  # Blocks flow save/execution
    WARNING = "warning"  # Allows save but warns user
    INFO = "info"  # Informational, best practice suggestion


class RuleCategory(str, Enum):
    """Categories for grouping rules."""

    STRUCTURAL = "structural"
    SESSION_MESSAGE = "session_message"
    TEMPLATE = "template"
    BUTTON = "button"
    EDGE = "edge"
    WHATSAPP = "whatsapp"


@dataclass
class RuleViolation:
    """
    Represents a single rule violation found during validation.

    Attributes:
        rule_id: Unique identifier for the rule (e.g., "STRUCT_001")
        message: Human-readable description of the violation
        node_id: ID of the node involved (if applicable)
        edge_id: ID of the edge involved (if applicable)
        severity: How serious the violation is
        details: Additional context for debugging
    """

    rule_id: str
    message: str
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    severity: RuleSeverity = RuleSeverity.ERROR
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "severity": self.severity.value,
            "details": self.details,
        }


@dataclass
class ValidationResult:
    """
    Result of running validation rules on a flow.

    Attributes:
        is_valid: True if no ERROR-level violations
        violations: All violations found
        errors: Only ERROR-level violations
        warnings: Only WARNING-level violations
    """

    violations: List[RuleViolation] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Flow is valid if no error-level violations."""
        return not any(v.severity == RuleSeverity.ERROR for v in self.violations)

    @property
    def errors(self) -> List[RuleViolation]:
        """Get only error-level violations."""
        return [v for v in self.violations if v.severity == RuleSeverity.ERROR]

    @property
    def warnings(self) -> List[RuleViolation]:
        """Get only warning-level violations."""
        return [v for v in self.violations if v.severity == RuleSeverity.WARNING]

    @property
    def info(self) -> List[RuleViolation]:
        """Get only info-level violations."""
        return [v for v in self.violations if v.severity == RuleSeverity.INFO]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "is_valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "violations": [v.to_dict() for v in self.violations],
        }


class FlowRule(ABC):
    """
    Abstract base class for all flow validation rules.

    Each rule should:
    1. Have a unique rule_id (format: CATEGORY_XXX)
    2. Have a human-readable description
    3. Implement validate() to check the rule

    Example:
        class NoSelfLoopRule(FlowRule):
            rule_id = "STRUCT_001"
            description = "An edge cannot connect a node to itself"
            category = RuleCategory.STRUCTURAL

            def validate(self, flow_data: dict) -> List[RuleViolation]:
                violations = []
                for edge in flow_data.get("edges", []):
                    if edge["source"] == edge["target"]:
                        violations.append(RuleViolation(...))
                return violations
    """

    rule_id: str = ""
    description: str = ""
    category: RuleCategory = RuleCategory.STRUCTURAL
    severity: RuleSeverity = RuleSeverity.ERROR
    enabled: bool = True

    @abstractmethod
    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        """
        Validate the flow data against this rule.

        Args:
            flow_data: The ReactFlow JSON structure containing nodes, edges, viewport

        Returns:
            List of RuleViolation objects (empty list if rule passes)
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} [{self.rule_id}]>"


class NodeRule(FlowRule):
    """
    Base class for rules that validate individual nodes.

    Subclasses can override:
    - applies_to_node_type: Limit to specific node types
    - validate_node(): Check a single node
    """

    applies_to_node_type: Optional[str] = None  # None = all node types

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        """Iterate nodes and validate each one."""
        violations = []
        nodes = flow_data.get("nodes", [])

        for node in nodes:
            node_type = node.get("type", "template")

            # Skip if rule only applies to specific node type
            if self.applies_to_node_type and node_type != self.applies_to_node_type:
                continue

            node_violations = self.validate_node(node, flow_data)
            violations.extend(node_violations)

        return violations

    @abstractmethod
    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        """Validate a single node. Override in subclass."""
        pass


class EdgeRule(FlowRule):
    """
    Base class for rules that validate individual edges.

    Subclasses can override:
    - validate_edge(): Check a single edge
    """

    category = RuleCategory.EDGE

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        """Iterate edges and validate each one."""
        violations = []
        edges = flow_data.get("edges", [])
        nodes = flow_data.get("nodes", [])

        # Build node lookup for context
        node_map = {n["id"]: n for n in nodes}

        for edge in edges:
            source_node = node_map.get(edge.get("source"))
            target_node = node_map.get(edge.get("target"))

            edge_violations = self.validate_edge(edge, source_node, target_node, flow_data)
            violations.extend(edge_violations)

        return violations

    @abstractmethod
    def validate_edge(
        self,
        edge: Dict[str, Any],
        source_node: Optional[Dict[str, Any]],
        target_node: Optional[Dict[str, Any]],
        flow_data: Dict[str, Any],
    ) -> List[RuleViolation]:
        """Validate a single edge. Override in subclass."""
        pass
