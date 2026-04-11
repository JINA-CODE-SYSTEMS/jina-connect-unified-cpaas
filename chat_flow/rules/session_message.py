"""
Session Message validation rules for ChatFlow.

These rules validate session message (message) nodes, which send
non-template messages within the 24-hour WhatsApp session window.

Rules (from RULES.MD):
- Session messages support multiple content types
- Only work within 24-hour window (informational)
- Are passthrough (auto-route, no user input wait)
- Can only have ONE outgoing edge
- Should follow a template node
- Content limits for buttons/lists
"""

from typing import Any, Dict, List

from .base import FlowRule, NodeRule, RuleCategory, RuleSeverity, RuleViolation
from .registry import register


@register
class SessionMessageSingleOutgoingEdgeRule(FlowRule):
    """Session message nodes can only have ONE outgoing edge."""

    rule_id = "SESSION_001"
    description = "Session message nodes can only have ONE outgoing edge (passthrough to single next node)"
    category = RuleCategory.SESSION_MESSAGE

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        nodes = flow_data.get("nodes", [])
        edges = flow_data.get("edges", [])

        # Build node lookup
        node_map = {n.get("id"): n for n in nodes}

        # Find message nodes
        message_node_ids = {n.get("id") for n in nodes if n.get("type") == "message"}

        # Count outgoing edges per message node
        for node_id in message_node_ids:
            node = node_map.get(node_id, {})
            node_data = node.get("data", {})
            buttons = node_data.get("buttons", [])
            msg_type = node_data.get("message_type", "")

            # Message nodes with interactive buttons (interactive_button / interactive_list)
            # are allowed one edge per button — skip the single-edge rule for them.
            has_interactive_buttons = buttons and msg_type in ("interactive_button", "interactive_list")
            if has_interactive_buttons:
                continue

            outgoing_count = sum(1 for e in edges if e.get("source") == node_id)

            if outgoing_count > 1:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Session message node '{node_id}' has {outgoing_count} outgoing edges - only 1 allowed",
                        node_id=node_id,
                        severity=self.severity,
                        details={"outgoing_count": outgoing_count, "max_allowed": 1},
                    )
                )

        return violations


@register
class SessionMessageShouldFollowTemplateRule(FlowRule):
    """Session message nodes should follow a template node (not be first after start)."""

    rule_id = "SESSION_002"
    description = "Session message nodes should follow a template node (templates initiate conversations)"
    category = RuleCategory.SESSION_MESSAGE
    severity = RuleSeverity.WARNING

    def validate(self, flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        nodes = flow_data.get("nodes", [])
        edges = flow_data.get("edges", [])

        # Build node lookup
        node_map = {n.get("id"): n for n in nodes}

        # Find start nodes
        start_node_ids = {n.get("id") for n in nodes if n.get("type") == "start"}

        # Check edges from start nodes
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")

            if source in start_node_ids:
                target_node = node_map.get(target)
                if target_node and target_node.get("type") == "message":
                    violations.append(
                        RuleViolation(
                            rule_id=self.rule_id,
                            message=f"Session message node '{target}' is directly after start - use a template node first to initiate the conversation",
                            node_id=target,
                            edge_id=edge.get("id"),
                            severity=self.severity,
                        )
                    )

        return violations


@register
class SessionMessageInteractiveButtonLimitRule(NodeRule):
    """Interactive button messages can have max 3 buttons."""

    rule_id = "SESSION_003"
    description = "Interactive Button messages: max 3 buttons, button title max 20 chars, body max 1024 chars"
    category = RuleCategory.SESSION_MESSAGE
    applies_to_node_type = "message"

    MAX_BUTTONS = 3
    MAX_BUTTON_TITLE = 20
    MAX_BODY_LENGTH = 1024

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_id = node.get("id")
        node_data = node.get("data", {})

        message_type = node_data.get("message_type")

        # Only validate interactive button messages
        if message_type not in ("interactive_button", "button"):
            return []

        # Check button count
        buttons = node_data.get("buttons", [])
        if len(buttons) > self.MAX_BUTTONS:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Session message '{node_id}' has {len(buttons)} buttons - max {self.MAX_BUTTONS} allowed",
                    node_id=node_id,
                    severity=self.severity,
                    details={"button_count": len(buttons), "max_allowed": self.MAX_BUTTONS},
                )
            )

        # Check button titles
        for i, btn in enumerate(buttons):
            title = btn.get("title", btn.get("text", ""))
            if len(title) > self.MAX_BUTTON_TITLE:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Button {i + 1} title in '{node_id}' exceeds {self.MAX_BUTTON_TITLE} characters",
                        node_id=node_id,
                        severity=self.severity,
                        details={"button_index": i, "title_length": len(title)},
                    )
                )

        # Check body length
        body = node_data.get("body", node_data.get("message_content", ""))
        if isinstance(body, str) and len(body) > self.MAX_BODY_LENGTH:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Message body in '{node_id}' exceeds {self.MAX_BODY_LENGTH} characters",
                    node_id=node_id,
                    severity=self.severity,
                    details={"body_length": len(body), "max_allowed": self.MAX_BODY_LENGTH},
                )
            )

        return violations


@register
class SessionMessageInteractiveListLimitRule(NodeRule):
    """Interactive list messages have section and row limits."""

    rule_id = "SESSION_004"
    description = "Interactive List messages: max 10 sections, max 10 rows total, body max 4096 chars"
    category = RuleCategory.SESSION_MESSAGE
    applies_to_node_type = "message"

    MAX_SECTIONS = 10
    MAX_ROWS_TOTAL = 10
    MAX_BODY_LENGTH = 4096
    MAX_ROW_TITLE = 24
    MAX_ROW_DESCRIPTION = 72

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_id = node.get("id")
        node_data = node.get("data", {})

        message_type = node_data.get("message_type")

        # Only validate interactive list messages
        if message_type not in ("interactive_list", "list"):
            return []

        sections = node_data.get("sections", [])

        # Check section count
        if len(sections) > self.MAX_SECTIONS:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"List message '{node_id}' has {len(sections)} sections - max {self.MAX_SECTIONS} allowed",
                    node_id=node_id,
                    severity=self.severity,
                    details={"section_count": len(sections), "max_allowed": self.MAX_SECTIONS},
                )
            )

        # Check total row count
        total_rows = sum(len(s.get("rows", [])) for s in sections)
        if total_rows > self.MAX_ROWS_TOTAL:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"List message '{node_id}' has {total_rows} total rows - max {self.MAX_ROWS_TOTAL} allowed",
                    node_id=node_id,
                    severity=self.severity,
                    details={"total_rows": total_rows, "max_allowed": self.MAX_ROWS_TOTAL},
                )
            )

        # Check row titles and descriptions
        for section_idx, section in enumerate(sections):
            for row_idx, row in enumerate(section.get("rows", [])):
                title = row.get("title", "")
                if len(title) > self.MAX_ROW_TITLE:
                    violations.append(
                        RuleViolation(
                            rule_id=self.rule_id,
                            message=f"Row title in '{node_id}' section {section_idx + 1} row {row_idx + 1} exceeds {self.MAX_ROW_TITLE} chars",
                            node_id=node_id,
                            severity=self.severity,
                        )
                    )

                desc = row.get("description", "")
                if len(desc) > self.MAX_ROW_DESCRIPTION:
                    violations.append(
                        RuleViolation(
                            rule_id=self.rule_id,
                            message=f"Row description in '{node_id}' section {section_idx + 1} row {row_idx + 1} exceeds {self.MAX_ROW_DESCRIPTION} chars",
                            node_id=node_id,
                            severity=self.severity,
                        )
                    )

        # Check body length
        body = node_data.get("body", node_data.get("message_content", ""))
        if isinstance(body, str) and len(body) > self.MAX_BODY_LENGTH:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"List message body in '{node_id}' exceeds {self.MAX_BODY_LENGTH} characters",
                    node_id=node_id,
                    severity=self.severity,
                    details={"body_length": len(body), "max_allowed": self.MAX_BODY_LENGTH},
                )
            )

        return violations


@register
class SessionMessageUniqueButtonIdsRule(NodeRule):
    """Button/Row IDs must be unique within a session message."""

    rule_id = "SESSION_005"
    description = "Button/Row IDs must be unique within the message"
    category = RuleCategory.SESSION_MESSAGE
    applies_to_node_type = "message"

    def validate_node(self, node: Dict[str, Any], flow_data: Dict[str, Any]) -> List[RuleViolation]:
        violations = []
        node_id = node.get("id")
        node_data = node.get("data", {})

        message_type = node_data.get("message_type", "")

        ids = []

        # Collect button IDs
        if message_type in ("interactive_button", "button"):
            buttons = node_data.get("buttons", [])
            ids = [b.get("id") for b in buttons if b.get("id")]

        # Collect row IDs from list
        elif message_type in ("interactive_list", "list"):
            sections = node_data.get("sections", [])
            for section in sections:
                for row in section.get("rows", []):
                    if row.get("id"):
                        ids.append(row.get("id"))

        # Check for duplicates
        seen = set()
        duplicates = set()
        for item_id in ids:
            if item_id in seen:
                duplicates.add(item_id)
            seen.add(item_id)

        for dup in duplicates:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Duplicate button/row ID '{dup}' in session message '{node_id}'",
                    node_id=node_id,
                    severity=self.severity,
                    details={"duplicate_id": dup},
                )
            )

        return violations
