"""
Delay node validation rules for ChatFlow.

These rules validate delay nodes, which pause flow execution
for a specified duration before continuing.

Rules:
- Delay nodes should have delay configuration
- Delay nodes can only have ONE outgoing edge (passthrough)
- Delay values should be reasonable (not too long)
"""

from typing import Any, Dict, List

from .base import FlowRule, NodeRule, RuleCategory, RuleSeverity, RuleViolation
from .registry import register

# Unit multipliers for converting delay_duration + delay_unit to total seconds
_UNIT_MULTIPLIERS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}


def _compute_total_delay(node_data: Dict[str, Any]) -> int:
    """Compute total delay in seconds from node data.

    Supports two formats:
      - Frontend format: delay_duration + delay_unit (e.g. 5, "seconds")
      - Legacy format: delay_seconds, delay_minutes, delay_hours
    """
    delay_duration = node_data.get("delay_duration", 0) or 0
    delay_unit = node_data.get("delay_unit", "seconds")
    total = delay_duration * _UNIT_MULTIPLIERS.get(delay_unit, 1)

    if total <= 0:
        delay_seconds = node_data.get("delay_seconds", 0) or 0
        delay_minutes = node_data.get("delay_minutes", 0) or 0
        delay_hours = node_data.get("delay_hours", 0) or 0
        total = delay_seconds + (delay_minutes * 60) + (delay_hours * 3600)

    return total


@register
class DelayNodeSingleOutgoingEdgeRule(FlowRule):
    """Delay nodes can only have ONE outgoing edge."""

    rule_id = "DELAY_001"
    description = "Delay nodes can only have ONE outgoing edge (passthrough to single next node)"
    category = RuleCategory.STRUCTURAL

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        nodes = flow_data.get("nodes", [])
        edges = flow_data.get("edges", [])

        # Find delay nodes
        delay_node_ids = {n.get("id") for n in nodes if n.get("type") == "delay"}

        # Count outgoing edges per delay node
        for node_id in delay_node_ids:
            outgoing_count = sum(1 for e in edges if e.get("source") == node_id)

            if outgoing_count > 1:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Delay node '{node_id}' has {outgoing_count} outgoing edges - only 1 allowed",
                        node_id=node_id,
                        severity=self.severity,
                        details={"outgoing_count": outgoing_count, "max_allowed": 1},
                    )
                )

        return violations


@register
class DelayConfigurationRule(NodeRule):
    """Delay nodes should have delay configuration."""

    rule_id = "DELAY_002"
    description = "Delay nodes should specify delay duration"
    category = RuleCategory.STRUCTURAL
    severity = RuleSeverity.WARNING
    applies_to_node_type = "delay"

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        node_id = node.get("id")
        node_data = node.get("data", {})

        total_delay = _compute_total_delay(node_data)

        if total_delay <= 0:
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Delay node '{node_id}' has no delay configured - will default to 60 seconds",
                    node_id=node_id,
                    severity=self.severity,
                    details={"default_delay": 60},
                )
            ]

        return []


@register
class DelayMaxDurationRule(NodeRule):
    """Delay duration should not exceed reasonable limits."""

    rule_id = "DELAY_003"
    description = "Delay duration should not exceed 24 hours"
    category = RuleCategory.STRUCTURAL
    severity = RuleSeverity.WARNING
    applies_to_node_type = "delay"

    MAX_DELAY_SECONDS = 24 * 60 * 60  # 24 hours

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        node_id = node.get("id")
        node_data = node.get("data", {})

        total_delay = _compute_total_delay(node_data)

        if total_delay > self.MAX_DELAY_SECONDS:
            hours = total_delay / 3600
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Delay node '{node_id}' has delay of {hours:.1f} hours - max recommended is 24 hours",
                    node_id=node_id,
                    severity=self.severity,
                    details={"delay_seconds": total_delay, "max_recommended": self.MAX_DELAY_SECONDS, "hours": hours},
                )
            ]

        return []
