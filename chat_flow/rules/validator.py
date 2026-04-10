"""
Flow Validator Service.

This module provides the main entry point for validating ChatFlow configurations
using the registered rules.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from .base import (FlowRule, RuleCategory, RuleSeverity, RuleViolation,
                   ValidationResult)
from .registry import RuleRegistry

logger = logging.getLogger(__name__)


class FlowValidatorService:
    """
    Main service for validating ChatFlow configurations.
    
    This service orchestrates the execution of all registered validation rules
    and aggregates the results.
    
    Usage:
        validator = FlowValidatorService()
        result = validator.validate(flow_data)
        
        if not result.is_valid:
            for error in result.errors:
                print(f"{error.rule_id}: {error.message}")
    
    Options:
        - exclude_rules: Skip specific rules by ID
        - include_only_categories: Only run rules from specific categories
        - stop_on_first_error: Stop validation after first error (for performance)
        - skip_db_checks: Disable rules that require database access
    """
    
    def __init__(
        self,
        exclude_rules: Optional[Set[str]] = None,
        include_only_categories: Optional[Set[RuleCategory]] = None,
        skip_db_checks: bool = False
    ):
        """
        Initialize the validator service.
        
        Args:
            exclude_rules: Set of rule IDs to skip
            include_only_categories: If set, only run rules from these categories
            skip_db_checks: If True, skip rules that require database access
        """
        self.exclude_rules = exclude_rules or set()
        self.include_only_categories = include_only_categories
        self.skip_db_checks = skip_db_checks
    
    def get_active_rules(self) -> List[FlowRule]:
        """Get the list of rules that will be executed."""
        all_rules = RuleRegistry.get_all_rules(enabled_only=True)
        
        active_rules = []
        for rule in all_rules:
            # Skip excluded rules
            if rule.rule_id in self.exclude_rules:
                continue
            
            # Filter by category if specified
            if self.include_only_categories:
                if rule.category not in self.include_only_categories:
                    continue
            
            # Skip DB rules if requested
            if self.skip_db_checks and getattr(rule, 'check_database', False):
                continue
            
            active_rules.append(rule)
        
        return active_rules
    
    def validate(
        self,
        flow_data: Dict[str, Any],
        stop_on_first_error: bool = False
    ) -> ValidationResult:
        """
        Validate flow data against all active rules.
        
        Args:
            flow_data: The ReactFlow JSON structure with nodes, edges, viewport
            stop_on_first_error: If True, stop after finding first ERROR-level violation
            
        Returns:
            ValidationResult with all violations found
        """
        all_violations: List[RuleViolation] = []
        rules = self.get_active_rules()
        
        logger.debug(f"Running {len(rules)} validation rules")
        
        for rule in rules:
            try:
                violations = rule.validate(flow_data)
                all_violations.extend(violations)
                
                if violations:
                    logger.debug(f"Rule {rule.rule_id} found {len(violations)} violations")
                
                # Early exit if requested and we have errors
                if stop_on_first_error:
                    has_errors = any(v.severity == RuleSeverity.ERROR for v in violations)
                    if has_errors:
                        logger.debug(f"Stopping validation early due to error in {rule.rule_id}")
                        break
                        
            except Exception as e:
                logger.error(f"Error running rule {rule.rule_id}: {e}")
                # Add a violation for the rule failure
                all_violations.append(RuleViolation(
                    rule_id=rule.rule_id,
                    message=f"Rule validation failed: {str(e)}",
                    severity=RuleSeverity.WARNING,
                    details={"exception": str(e)}
                ))
        
        result = ValidationResult(violations=all_violations)
        
        logger.info(
            f"Validation complete: is_valid={result.is_valid}, "
            f"errors={len(result.errors)}, warnings={len(result.warnings)}"
        )
        
        return result
    
    def validate_node(
        self,
        node: Dict[str, Any],
        flow_data: Optional[Dict[str, Any]] = None
    ) -> ValidationResult:
        """
        Validate a single node (useful for real-time UI validation).
        
        Args:
            node: The node to validate
            flow_data: Full flow context (optional, used for cross-node validation)
            
        Returns:
            ValidationResult for this node only
        """
        # Create minimal flow_data if not provided
        if flow_data is None:
            flow_data = {"nodes": [node], "edges": [], "viewport": {}}
        
        all_violations: List[RuleViolation] = []
        rules = self.get_active_rules()
        node_id = node.get("id")
        
        for rule in rules:
            try:
                violations = rule.validate(flow_data)
                # Filter to only violations for this node
                node_violations = [v for v in violations if v.node_id == node_id]
                all_violations.extend(node_violations)
            except Exception as e:
                logger.error(f"Error running rule {rule.rule_id} for node {node_id}: {e}")
        
        return ValidationResult(violations=all_violations)
    
    def validate_edge(
        self,
        edge: Dict[str, Any],
        flow_data: Dict[str, Any]
    ) -> ValidationResult:
        """
        Validate a single edge (useful for real-time UI validation).
        
        Args:
            edge: The edge to validate
            flow_data: Full flow context (needed for node lookup)
            
        Returns:
            ValidationResult for this edge only
        """
        all_violations: List[RuleViolation] = []
        rules = self.get_active_rules()
        edge_id = edge.get("id")
        
        for rule in rules:
            try:
                violations = rule.validate(flow_data)
                # Filter to only violations for this edge
                edge_violations = [v for v in violations if v.edge_id == edge_id]
                all_violations.extend(edge_violations)
            except Exception as e:
                logger.error(f"Error running rule {rule.rule_id} for edge {edge_id}: {e}")
        
        return ValidationResult(violations=all_violations)
    
    @classmethod
    def get_all_rule_documentation(cls) -> List[Dict[str, Any]]:
        """
        Get documentation for all registered rules.
        
        Returns:
            List of dicts with rule metadata (useful for API/docs)
        """
        return RuleRegistry.get_rule_documentation()


# Convenience function for quick validation
def validate_flow(flow_data: Dict[str, Any], **kwargs) -> ValidationResult:
    """
    Convenience function to validate flow data.
    
    Args:
        flow_data: The ReactFlow JSON structure
        **kwargs: Passed to FlowValidatorService.__init__
        
    Returns:
        ValidationResult
    """
    validator = FlowValidatorService(**kwargs)
    return validator.validate(flow_data)
