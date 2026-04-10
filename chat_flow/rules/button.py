"""
Button validation rules for ChatFlow.

These rules validate button configurations within nodes,
ensuring they meet WhatsApp requirements and flow design best practices.

Rules (from RULES.MD):
- Button IDs within a node must be unique
- Button texts within a node must be unique
- Non-interactive buttons (PHONE_NUMBER, URL, CALL_TO_ACTION) are passthrough
"""

from typing import Any, Dict, List, Set

from .base import NodeRule, RuleCategory, RuleSeverity, RuleViolation
from .registry import register


@register
class UniqueButtonIdsRule(NodeRule):
    """Button IDs within a node must be unique."""
    
    rule_id = "BUTTON_001"
    description = "Button IDs within a node must be unique"
    category = RuleCategory.BUTTON
    applies_to_node_type = "template"
    
    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_id = node.get("id")
        node_data = node.get("data", {})
        
        buttons = node_data.get("buttons", [])
        
        if not buttons:
            return []
        
        seen: Set[str] = set()
        duplicates: Set[str] = set()
        
        for btn in buttons:
            btn_id = btn.get("id")
            if btn_id:
                if btn_id in seen:
                    duplicates.add(btn_id)
                seen.add(btn_id)
        
        for dup in duplicates:
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message=f"Duplicate button ID '{dup}' in node '{node_id}'",
                node_id=node_id,
                severity=self.severity,
                details={"duplicate_id": dup}
            ))
        
        return violations


@register
class UniqueButtonTextsRule(NodeRule):
    """Button texts within a node must be unique."""
    
    rule_id = "BUTTON_002"
    description = "Button texts within a node must be unique (no duplicate button labels)"
    category = RuleCategory.BUTTON
    applies_to_node_type = "template"
    
    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_id = node.get("id")
        node_data = node.get("data", {})
        
        buttons = node_data.get("buttons", [])
        
        if not buttons:
            return []
        
        seen: Set[str] = set()
        duplicates: Set[str] = set()
        
        for btn in buttons:
            btn_text = btn.get("text", "").strip().lower()  # Case-insensitive
            if btn_text:
                if btn_text in seen:
                    duplicates.add(btn.get("text", ""))
                seen.add(btn_text)
        
        for dup in duplicates:
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message=f"Duplicate button text '{dup}' in node '{node_id}'",
                node_id=node_id,
                severity=self.severity,
                details={"duplicate_text": dup}
            ))
        
        return violations


@register
class ButtonTextLengthRule(NodeRule):
    """Button text cannot exceed 20 characters (WhatsApp limitation)."""
    
    rule_id = "BUTTON_003"
    description = "Button text cannot exceed 20 characters (WhatsApp limitation)"
    category = RuleCategory.BUTTON
    applies_to_node_type = "template"
    
    MAX_LENGTH = 20
    
    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_id = node.get("id")
        node_data = node.get("data", {})
        
        buttons = node_data.get("buttons", [])
        
        for i, btn in enumerate(buttons):
            btn_text = btn.get("text", "")
            if len(btn_text) > self.MAX_LENGTH:
                violations.append(RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Button '{btn_text[:20]}...' in node '{node_id}' exceeds {self.MAX_LENGTH} characters ({len(btn_text)} chars)",
                    node_id=node_id,
                    severity=self.severity,
                    details={
                        "button_index": i,
                        "button_text": btn_text,
                        "length": len(btn_text),
                        "max_allowed": self.MAX_LENGTH
                    }
                ))
        
        return violations


@register
class MaxButtonsPerNodeRule(NodeRule):
    """Maximum 10 buttons per node (WhatsApp limitation)."""
    
    rule_id = "BUTTON_004"
    description = "Maximum 10 buttons per node (WhatsApp limitation)"
    category = RuleCategory.BUTTON
    applies_to_node_type = "template"
    
    MAX_BUTTONS = 10
    
    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        node_id = node.get("id")
        node_data = node.get("data", {})
        
        buttons = node_data.get("buttons", [])
        
        if len(buttons) > self.MAX_BUTTONS:
            return [RuleViolation(
                rule_id=self.rule_id,
                message=f"Node '{node_id}' has {len(buttons)} buttons - max {self.MAX_BUTTONS} allowed",
                node_id=node_id,
                severity=self.severity,
                details={"button_count": len(buttons), "max_allowed": self.MAX_BUTTONS}
            )]
        
        return []


@register
class NonInteractiveButtonsInfoRule(NodeRule):
    """
    Inform about non-interactive buttons (PHONE_NUMBER, URL, CALL_TO_ACTION).
    
    These buttons don't trigger flow navigation - they perform external actions.
    This is informational, not an error.
    """
    
    rule_id = "BUTTON_005"
    description = "Non-interactive buttons (PHONE_NUMBER, URL, CALL_TO_ACTION) don't trigger flow navigation"
    category = RuleCategory.BUTTON
    severity = RuleSeverity.INFO
    applies_to_node_type = "template"
    
    NON_INTERACTIVE_TYPES = {"PHONE_NUMBER", "URL", "CALL_TO_ACTION", "COPY_CODE", "OTP"}
    
    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_id = node.get("id")
        node_data = node.get("data", {})
        
        buttons = node_data.get("buttons", [])
        
        # Check if all buttons are non-interactive
        non_interactive_buttons = [b for b in buttons if b.get("type") in self.NON_INTERACTIVE_TYPES]
        
        if buttons and len(non_interactive_buttons) == len(buttons):
            violations.append(RuleViolation(
                rule_id=self.rule_id,
                message=f"All buttons in node '{node_id}' are non-interactive (URL/Phone/etc.) - flow will passthrough to next node automatically",
                node_id=node_id,
                severity=self.severity,
                details={
                    "button_types": [b.get("type") for b in buttons],
                    "passthrough": True
                }
            ))
        
        return violations
