"""
Template validation rules for ChatFlow.

These rules validate template nodes, which use pre-approved
WhatsApp templates to send messages.

Rules (from RULES.MD):
- Template nodes must reference an approved template (template_id required)
- Only templates with APPROVED status can be used
"""

import uuid
from typing import Any, Dict, List

from .base import NodeRule, RuleCategory, RuleSeverity, RuleViolation
from .registry import register


@register
class TemplateIdRequiredRule(NodeRule):
    """Template nodes must have a template_id."""

    rule_id = "TEMPLATE_001"
    description = "Template nodes must reference an approved WhatsApp template (template_id required)"
    category = RuleCategory.TEMPLATE
    applies_to_node_type = "template"

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        node_id = node.get("id")
        node_data = node.get("data", {})

        template_id = node_data.get("template_id")

        if not template_id:
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Template node '{node_id}' is missing template_id - select a WhatsApp template",
                    node_id=node_id,
                    severity=self.severity,
                )
            ]

        # Validate it's a valid UUID (WATemplate.id is a UUIDField) or a positive integer
        valid = False
        if isinstance(template_id, int) and template_id > 0:
            valid = True
        elif isinstance(template_id, str):
            try:
                uuid.UUID(template_id)
                valid = True
            except ValueError:
                pass

        if not valid:
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Template node '{node_id}' has invalid template_id: {template_id}",
                    node_id=node_id,
                    severity=self.severity,
                    details={"template_id": template_id},
                )
            ]

        return []


@register
class TemplateApprovedStatusRule(NodeRule):
    """
    Template must have APPROVED status.

    Note: This rule requires database access to verify template status.
    It may be skipped during frontend-only validation.
    """

    rule_id = "TEMPLATE_002"
    description = "Only templates with APPROVED status can be used in flows"
    category = RuleCategory.TEMPLATE
    applies_to_node_type = "template"

    # Flag to control DB validation (can be disabled for frontend-only validation)
    check_database: bool = True

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        if not self.check_database:
            return []

        node_id = node.get("id")
        node_data = node.get("data", {})
        template_id = node_data.get("template_id")

        if not template_id:
            return []  # Already caught by TEMPLATE_001

        try:
            from wa.models import WATemplate

            template = WATemplate.objects.filter(id=template_id).first()

            if not template:
                return [
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Template node '{node_id}' references non-existent template ID: {template_id}",
                        node_id=node_id,
                        severity=self.severity,
                        details={"template_id": template_id},
                    )
                ]

            if template.status != "APPROVED":
                return [
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Template '{template.element_name}' in node '{node_id}' is not approved (status: {template.status})",
                        node_id=node_id,
                        severity=self.severity,
                        details={
                            "template_id": template_id,
                            "template_name": template.element_name,
                            "current_status": template.status,
                            "required_status": "APPROVED",
                        },
                    )
                ]

        except Exception as e:
            # If we can't check database, return warning instead of error
            return [
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Could not verify template status for node '{node_id}': {str(e)}",
                    node_id=node_id,
                    severity=RuleSeverity.WARNING,
                    details={"error": str(e)},
                )
            ]

        return []


@register
class TemplateButtonEdgeRequiredRule(NodeRule):
    """
    Template nodes with QUICK_REPLY buttons must have edges from buttons.

    This ensures that interactive buttons lead somewhere in the flow.
    """

    rule_id = "TEMPLATE_003"
    description = "Template nodes with QUICK_REPLY buttons should have edges for each button"
    category = RuleCategory.TEMPLATE
    severity = RuleSeverity.WARNING
    applies_to_node_type = "template"

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_id = node.get("id")
        node_data = node.get("data", {})

        buttons = node_data.get("buttons", [])

        # Get QUICK_REPLY buttons only
        quick_reply_buttons = [b for b in buttons if b.get("type") == "QUICK_REPLY"]

        if not quick_reply_buttons:
            return []

        # Get edges from this node
        edges = flow_data.get("edges", [])
        outgoing_edges = [e for e in edges if e.get("source") == node_id]

        # Check if each QUICK_REPLY button has an edge
        edge_button_texts = set()
        edge_button_ids = set()
        edge_source_handles = set()  # fallback: match by sourceHandle pattern

        for edge in outgoing_edges:
            edge_data = edge.get("data") or {}
            if edge_data.get("button_text"):
                edge_button_texts.add(edge_data["button_text"])
            if edge_data.get("button_id"):
                edge_button_ids.add(edge_data["button_id"])
            # Track sourceHandle for fallback matching ("button-0", "button-1", etc.)
            src_handle = edge.get("sourceHandle") or ""
            if src_handle.startswith("button-"):
                edge_source_handles.add(src_handle)

        # Build a mapping of button index → button for sourceHandle fallback
        all_buttons = node_data.get("buttons", [])

        for btn in quick_reply_buttons:
            btn_text = btn.get("text")
            btn_id = btn.get("id")

            # Primary: match by button_text or button_id in edge data
            is_connected = btn_text in edge_button_texts or btn_id in edge_button_ids

            # Fallback: match by sourceHandle pattern ("button-N")
            # Find this button's index in the full buttons list
            if not is_connected:
                for idx, b in enumerate(all_buttons):
                    if b.get("text") == btn_text and b.get("type") == btn.get("type"):
                        if f"button-{idx}" in edge_source_handles:
                            is_connected = True
                        break

            if not is_connected:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Button '{btn_text}' in template node '{node_id}' has no outgoing edge - user clicks will go nowhere",
                        node_id=node_id,
                        severity=self.severity,
                        details={"button_text": btn_text, "button_id": btn_id},
                    )
                )

        return violations
