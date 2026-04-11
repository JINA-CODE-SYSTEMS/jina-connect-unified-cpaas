"""
ChatFlow Validation Rules Package.

This package provides a rule-based validation engine for ChatFlow configurations.
Rules are organized by category and auto-register themselves.

Usage:
    from chat_flow.rules import FlowValidatorService

    validator = FlowValidatorService()
    result = validator.validate(flow_data)

    if not result.is_valid:
        for error in result.errors:
            print(f"{error.rule_id}: {error.message}")

To add a new rule:
    1. Create a class that inherits from FlowRule (or NodeRule/EdgeRule)
    2. Set rule_id, description, and category
    3. Implement the validate() method
    4. Decorate with @register

Example:
    from chat_flow.rules.base import FlowRule, RuleViolation
    from chat_flow.rules.registry import register

    @register
    class MyCustomRule(FlowRule):
        rule_id = "CUSTOM_001"
        description = "My custom validation rule"

        def validate(self, flow_data: dict) -> list:
            # Return list of RuleViolation objects
            return []
"""

# Import all rule modules to trigger auto-registration
# These imports are necessary to register the rules!
from . import api, button, delay, handoff, session_message, structural, template  # noqa: F401

# Import base classes for external use
from .base import EdgeRule, FlowRule, NodeRule, RuleCategory, RuleSeverity, RuleViolation, ValidationResult

# Import registry
from .registry import RuleRegistry, register

# Import the validator service
from .validator import FlowValidatorService

__all__ = [
    # Base classes
    "FlowRule",
    "NodeRule",
    "EdgeRule",
    "RuleViolation",
    "ValidationResult",
    "RuleSeverity",
    "RuleCategory",
    # Registry
    "RuleRegistry",
    "register",
    # Validator
    "FlowValidatorService",
]
