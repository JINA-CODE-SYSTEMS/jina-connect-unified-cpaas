"""
Rule Registry for ChatFlow validation.

This module provides a registry pattern for auto-discovering and managing
validation rules. Rules register themselves using the @register decorator.
"""

from typing import Dict, List, Optional, Type

from .base import FlowRule, RuleCategory


class RuleRegistry:
    """
    Central registry for all flow validation rules.
    
    Rules can be registered using the @RuleRegistry.register decorator,
    and retrieved by category, ID, or all at once.
    
    Example:
        @RuleRegistry.register
        class MyRule(FlowRule):
            rule_id = "CUSTOM_001"
            ...
        
        # Later, get all rules
        rules = RuleRegistry.get_all_rules()
    """
    
    _rules: Dict[str, Type[FlowRule]] = {}
    
    @classmethod
    def register(cls, rule_class: Type[FlowRule]) -> Type[FlowRule]:
        """
        Decorator to register a rule class.
        
        Args:
            rule_class: The FlowRule subclass to register
            
        Returns:
            The same class (allows use as decorator)
            
        Raises:
            ValueError: If rule_id is missing or already registered
        """
        if not rule_class.rule_id:
            raise ValueError(f"Rule class {rule_class.__name__} must have a rule_id")
        
        if rule_class.rule_id in cls._rules:
            raise ValueError(
                f"Rule ID '{rule_class.rule_id}' is already registered "
                f"by {cls._rules[rule_class.rule_id].__name__}"
            )
        
        cls._rules[rule_class.rule_id] = rule_class
        return rule_class
    
    @classmethod
    def unregister(cls, rule_id: str) -> None:
        """Remove a rule from the registry (useful for testing)."""
        cls._rules.pop(rule_id, None)
    
    @classmethod
    def clear(cls) -> None:
        """Clear all registered rules (useful for testing)."""
        cls._rules.clear()
    
    @classmethod
    def get_all_rules(cls, enabled_only: bool = True) -> List[FlowRule]:
        """
        Get instances of all registered rules.
        
        Args:
            enabled_only: If True, only return rules where enabled=True
            
        Returns:
            List of FlowRule instances
        """
        rules = [rule_cls() for rule_cls in cls._rules.values()]
        
        if enabled_only:
            rules = [r for r in rules if r.enabled]
        
        return rules
    
    @classmethod
    def get_rule(cls, rule_id: str) -> Optional[FlowRule]:
        """Get a specific rule by ID."""
        rule_cls = cls._rules.get(rule_id)
        return rule_cls() if rule_cls else None
    
    @classmethod
    def get_rules_by_category(cls, category: RuleCategory, enabled_only: bool = True) -> List[FlowRule]:
        """
        Get all rules in a specific category.
        
        Args:
            category: The RuleCategory to filter by
            enabled_only: If True, only return enabled rules
            
        Returns:
            List of FlowRule instances in that category
        """
        rules = [
            rule_cls() for rule_cls in cls._rules.values()
            if rule_cls.category == category
        ]
        
        if enabled_only:
            rules = [r for r in rules if r.enabled]
        
        return rules
    
    @classmethod
    def list_rule_ids(cls) -> List[str]:
        """Get list of all registered rule IDs."""
        return list(cls._rules.keys())
    
    @classmethod
    def get_rule_documentation(cls) -> List[Dict[str, str]]:
        """
        Generate documentation for all registered rules.
        
        Returns:
            List of dicts with rule_id, description, category, severity
        """
        docs = []
        for rule_cls in cls._rules.values():
            docs.append({
                "rule_id": rule_cls.rule_id,
                "description": rule_cls.description,
                "category": rule_cls.category.value if hasattr(rule_cls.category, 'value') else str(rule_cls.category),
                "severity": rule_cls.severity.value if hasattr(rule_cls.severity, 'value') else str(rule_cls.severity),
                "enabled": rule_cls.enabled
            })
        return sorted(docs, key=lambda x: x["rule_id"])


# Convenience decorator
register = RuleRegistry.register
