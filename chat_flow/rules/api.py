"""
API Node Validation Rules

Rules for validating API (HTTP request) nodes in ChatFlow.
API nodes make HTTP requests and route based on response status.
"""

import re
from typing import Any, Dict, List, Optional

from ..constants import API_RESPONSE_HANDLES, VALID_HTTP_METHODS
from .base import NodeRule, RuleCategory, RuleSeverity, RuleViolation
from .registry import register


@register
class APINodeURLRequired(NodeRule):
    """API nodes must have a valid URL."""

    rule_id = "API_001"
    description = "API node must have a valid URL"
    severity = RuleSeverity.ERROR
    category = RuleCategory.WHATSAPP

    def validate_node(
        self, node: Dict[str, Any], flow_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []

        if node.get("type") != "api":
            return violations

        node_data = node.get("data", {})
        # Frontend sends 'api_url'; also accept 'url' for flexibility
        url = (node_data.get("api_url") or node_data.get("url") or "").strip()

        if not url:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="API node must have a URL",
                    severity=self.severity,
                    node_id=node.get("id"),
                    details={"field": "api_url"},
                )
            )
            return violations

        # Basic URL validation - allow variables like {{base_url}}/endpoint
        # URL can be absolute or contain template variables
        url_pattern = r"^(https?://|{{[^}]+}})"
        has_protocol = re.match(url_pattern, url, re.IGNORECASE)

        # Also allow relative URLs starting with /
        is_relative = url.startswith("/")

        # Allow URLs with template variables anywhere
        has_template_var = "{{" in url

        if not has_protocol and not is_relative and not has_template_var:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="URL must start with http://, https://, a template variable {{...}}, or be a relative path starting with /",
                    severity=self.severity,
                    node_id=node.get("id"),
                    details={"field": "url", "value": url},
                )
            )

        return violations


@register
class APINodeMethodRequired(NodeRule):
    """API nodes must have a valid HTTP method."""

    rule_id = "API_002"
    description = "API node must have a valid HTTP method"
    severity = RuleSeverity.ERROR
    category = RuleCategory.WHATSAPP

    def validate_node(
        self, node: Dict[str, Any], flow_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []

        if node.get("type") != "api":
            return violations

        node_data = node.get("data", {})
        # Frontend sends 'api_method'; also accept 'method' for flexibility
        method = (node_data.get("api_method") or node_data.get("method") or "").strip().upper()

        if not method:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="API node must have an HTTP method",
                    severity=self.severity,
                    node_id=node.get("id"),
                    details={"field": "api_method"},
                )
            )
            return violations

        if method not in VALID_HTTP_METHODS:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Invalid HTTP method '{method}'. Must be one of: {', '.join(VALID_HTTP_METHODS)}",
                    severity=self.severity,
                    node_id=node.get("id"),
                    details={"field": "method", "value": method, "valid_methods": VALID_HTTP_METHODS},
                )
            )

        return violations


@register
class APINodeHeadersFormat(NodeRule):
    """API node headers must be a valid dictionary."""

    rule_id = "API_003"
    description = "API node headers must be a valid key-value dictionary"
    severity = RuleSeverity.ERROR
    category = RuleCategory.WHATSAPP

    def validate_node(
        self, node: Dict[str, Any], flow_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []

        if node.get("type") != "api":
            return violations

        node_data = node.get("data", {})
        # Frontend sends 'api_headers'; also accept 'headers'
        headers = node_data.get("api_headers", node_data.get("headers"))

        # Headers are optional - if not provided, defaults will be used
        if headers is None:
            return violations

        if not isinstance(headers, dict):
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="Headers must be a dictionary of key-value pairs",
                    severity=self.severity,
                    node_id=node.get("id"),
                    details={"field": "headers", "type": type(headers).__name__},
                )
            )
            return violations

        # Validate each header key-value
        for key, value in headers.items():
            if not isinstance(key, str) or not key.strip():
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Header key must be a non-empty string, got: {key}",
                        severity=self.severity,
                        node_id=node.get("id"),
                        details={"field": "headers", "invalid_key": key},
                    )
                )

            # Value can be string or contain template variables
            if not isinstance(value, (str, int, float, bool)):
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Header value for '{key}' must be a string or primitive type",
                        severity=self.severity,
                        node_id=node.get("id"),
                        details={"field": "headers", "key": key, "value_type": type(value).__name__},
                    )
                )

        return violations


@register
class APINodeBodyFormat(NodeRule):
    """API node body must be valid for the request method."""

    rule_id = "API_004"
    description = "API node body must be valid JSON or form data"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP

    def validate_node(
        self, node: Dict[str, Any], flow_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []

        if node.get("type") != "api":
            return violations

        node_data = node.get("data", {})
        # Frontend sends 'api_method' / 'api_body'; also accept 'method' / 'body'
        method = (node_data.get("api_method") or node_data.get("method") or "").upper()
        body = node_data.get("api_body", node_data.get("body"))

        # Body is optional
        if body is None:
            return violations

        # Warn if body provided for GET/DELETE (unusual but not invalid)
        if method in ("GET", "DELETE") and body:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Request body is unusual for {method} requests. Consider using query parameters instead.",
                    severity=RuleSeverity.WARNING,
                    node_id=node.get("id"),
                    details={"field": "body", "method": method},
                )
            )

        # Body can be dict (JSON), string (raw), or contain template variables
        if not isinstance(body, (dict, str, list)):
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="Request body must be a dictionary (JSON), array, or string",
                    severity=self.severity,
                    node_id=node.get("id"),
                    details={"field": "body", "type": type(body).__name__},
                )
            )

        return violations


@register
class APINodeResponseStatusHandles(NodeRule):
    """API node edges must use valid response status handles."""

    rule_id = "API_005"
    description = "Edges from API node must use valid response status handles"
    severity = RuleSeverity.ERROR
    category = RuleCategory.WHATSAPP

    def validate_node(
        self, node: Dict[str, Any], flow_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []

        if node.get("type") != "api":
            return violations

        node_id = node.get("id")
        edges = flow_data.get("edges", [])

        # Find edges originating from this API node
        api_edges = [e for e in edges if e.get("source") == node_id]

        for edge in api_edges:
            source_handle = edge.get("sourceHandle")

            # Allow standard handles for passthrough
            if source_handle in ("bottom", "default", "output", "out", None):
                continue

            # Check if it's a valid API response handle
            if source_handle not in API_RESPONSE_HANDLES:
                # Extract numeric code: accept both '200' and 'status-200' formats
                code_str = source_handle
                if source_handle and source_handle.startswith("status-"):
                    code_str = source_handle[len("status-") :]
                if not (code_str and code_str.isdigit() and 100 <= int(code_str) <= 599):
                    violations.append(
                        RuleViolation(
                            rule_id=self.rule_id,
                            message=f"Invalid API response handle '{source_handle}'. "
                            f"Use one of: {', '.join(API_RESPONSE_HANDLES)} or a specific status code (e.g., '200', '404')",
                            severity=self.severity,
                            node_id=node_id,
                            edge_id=edge.get("id"),
                            details={"source_handle": source_handle, "valid_handles": API_RESPONSE_HANDLES},
                        )
                    )

        return violations


@register
class APINodeSuccessPathRequired(NodeRule):
    """API node should have at least a success path defined."""

    rule_id = "API_006"
    description = "API node should have at least one success output path"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP

    def validate_node(
        self, node: Dict[str, Any], flow_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []

        if node.get("type") != "api":
            return violations

        node_id = node.get("id")
        edges = flow_data.get("edges", [])

        # Find edges originating from this API node
        api_edges = [e for e in edges if e.get("source") == node_id]

        if not api_edges:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="API node has no outgoing edges. Consider adding at least a success path.",
                    severity=RuleSeverity.WARNING,
                    node_id=node_id,
                    details={},
                )
            )
            return violations

        # Check for success path (success handle, 2xx codes, or default/bottom)
        def _is_success_handle(h: str) -> bool:
            if h in ("success", "bottom", "default", "output", "out", None):
                return True
            if not h:
                return False
            # Accept both '200' and 'status-200' formats
            code = h[len("status-") :] if h.startswith("status-") else h
            return code.isdigit() and 200 <= int(code) < 300

        has_success_path = any(_is_success_handle(e.get("sourceHandle")) for e in api_edges)

        if not has_success_path:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="API node should have a 'success' output path for handling successful responses",
                    severity=RuleSeverity.WARNING,
                    node_id=node_id,
                    details={"existing_handles": [e.get("sourceHandle") for e in api_edges]},
                )
            )

        return violations


@register
class APINodeTimeoutConfig(NodeRule):
    """API node timeout should be reasonable."""

    rule_id = "API_007"
    description = "API node timeout should be within reasonable bounds"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP

    def validate_node(
        self, node: Dict[str, Any], flow_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []

        if node.get("type") != "api":
            return violations

        node_data = node.get("data", {})
        # Frontend sends 'api_timeout'; also accept 'timeout'
        timeout = node_data.get("api_timeout", node_data.get("timeout"))

        # Timeout is optional (will use default)
        if timeout is None:
            return violations

        try:
            timeout_val = float(timeout)
        except (TypeError, ValueError):
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="Timeout must be a number (seconds)",
                    severity=RuleSeverity.ERROR,
                    node_id=node.get("id"),
                    details={"field": "timeout", "value": timeout},
                )
            )
            return violations

        if timeout_val <= 0:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="Timeout must be greater than 0 seconds",
                    severity=RuleSeverity.ERROR,
                    node_id=node.get("id"),
                    details={"field": "timeout", "value": timeout_val},
                )
            )
        elif timeout_val > 120:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message=f"Timeout of {timeout_val}s is very long. Consider a shorter timeout (max recommended: 30s)",
                    severity=RuleSeverity.WARNING,
                    node_id=node.get("id"),
                    details={"field": "timeout", "value": timeout_val, "recommended_max": 30},
                )
            )

        return violations


@register
class APINodeResponseMapping(NodeRule):
    """API node response mapping should be valid if provided."""

    rule_id = "API_008"
    description = "API node response mapping should have valid variable names"
    severity = RuleSeverity.WARNING
    category = RuleCategory.WHATSAPP

    def validate_node(
        self, node: Dict[str, Any], flow_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> List[RuleViolation]:
        violations = []

        if node.get("type") != "api":
            return violations

        node_data = node.get("data", {})
        response_mapping = node_data.get("response_mapping")

        # Response mapping is optional
        if response_mapping is None:
            return violations

        if not isinstance(response_mapping, dict):
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    message="Response mapping must be a dictionary",
                    severity=RuleSeverity.ERROR,
                    node_id=node.get("id"),
                    details={"field": "response_mapping", "type": type(response_mapping).__name__},
                )
            )
            return violations

        # Validate variable names (should be valid identifiers)
        variable_pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

        for var_name, json_path in response_mapping.items():
            if not variable_pattern.match(var_name):
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"Invalid variable name '{var_name}'. Must start with letter/underscore and contain only alphanumeric/underscore.",
                        severity=RuleSeverity.WARNING,
                        node_id=node.get("id"),
                        details={"field": "response_mapping", "variable": var_name},
                    )
                )

            # JSON path should be a string
            if not isinstance(json_path, str):
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=f"JSON path for variable '{var_name}' must be a string (e.g., '$.data.id' or 'data.id')",
                        severity=RuleSeverity.WARNING,
                        node_id=node.get("id"),
                        details={
                            "field": "response_mapping",
                            "variable": var_name,
                            "path_type": type(json_path).__name__,
                        },
                    )
                )

        return violations
