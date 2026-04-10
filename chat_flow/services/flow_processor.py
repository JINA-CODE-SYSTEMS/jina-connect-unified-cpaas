from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Type

from django.db import models, transaction
from wa.models import WATemplate

from ..constants import (NON_TEMPLATE_NODE_TYPES, PASSTHROUGH_NODE_TYPES,
                         is_multi_output_node, is_passthrough_node)
from ..models import ChatFlow, ChatFlowEdge, ChatFlowNode
from ..rules import FlowValidatorService, ValidationResult
from ..validators import (EdgeData, NodeButton, NodeData, ReactFlowData,
                          ReactFlowEdge, ReactFlowNode, ReactFlowPosition,
                          ReactFlowViewport, validate_reactflow_data)

# =============================================================================
# Interaction Element Framework (Extensible for buttons, cards, carousels, etc.)
# =============================================================================

class InteractionElement(ABC):
    """
    Base class for all interaction elements that can trigger flow transitions.
    
    Interaction elements are the clickable/actionable parts of a message that
    allow users to navigate through the flow. Examples:
    - Buttons (quick replies, CTAs)
    - Card actions (in carousels)
    - List items
    - Location picker results
    - etc.
    
    To add a new interaction type:
    1. Create a subclass of InteractionElement
    2. Implement extract_from_template() and extract_from_node_data()
    3. Register it in INTERACTION_ELEMENT_TYPES
    """
    
    element_type: str = "base"  # Override in subclasses
    
    @classmethod
    @abstractmethod
    def extract_from_template(cls, template: WATemplate) -> List[Dict]:
        """
        Extract interaction elements from a WhatsApp template.
        
        Args:
            template: WATemplate instance
            
        Returns:
            List of dicts, each representing an interaction element with at least:
            - id: Unique identifier
            - text: Display text (used for edge matching)
            - type: Element type (e.g., 'QUICK_REPLY', 'URL')
            - index: Position in the template
        """
        pass
    
    @classmethod
    @abstractmethod
    def extract_from_node_data(cls, node_data: Dict, node_id: str) -> List[Dict]:
        """
        Extract interaction elements from node_data JSON.
        
        Args:
            node_data: The node_data dict from ReactFlow
            node_id: The node's ID (for generating element IDs)
            
        Returns:
            List of dicts representing interaction elements
        """
        pass
    
    @classmethod
    def generate_element_id(cls, node_id: str, index: int) -> str:
        """Generate a unique ID for an interaction element."""
        return f"{node_id}-{cls.element_type}-{index}"
    
    @classmethod
    def validate_element(cls, element: Dict) -> bool:
        """Validate an interaction element dict has required fields."""
        required = ['id', 'text', 'type']
        return all(key in element for key in required)


class ButtonElement(InteractionElement):
    """
    Button interaction elements - quick replies, CTAs, URL buttons, etc.
    
    WhatsApp supports up to 3 quick reply buttons or 2 CTA buttons per message.
    """
    
    element_type = "button"
    
    @classmethod
    def extract_from_template(cls, template: WATemplate) -> List[Dict]:
        """Extract buttons from a WhatsApp template."""
        if not template or not template.buttons:
            return []
        
        buttons = []
        for i, btn in enumerate(template.buttons):
            buttons.append({
                'id': f"template-btn-{i}",
                'text': btn.get('text', ''),
                'type': btn.get('type', 'QUICK_REPLY'),
                'index': i,
                'template_button_index': i,
                'element_type': cls.element_type,
                # Preserve any additional button data
                **{k: v for k, v in btn.items() if k not in ['text', 'type']}
            })
        
        return buttons
    
    @classmethod
    def extract_from_node_data(cls, node_data: Dict, node_id: str) -> List[Dict]:
        """Extract buttons from node_data."""
        buttons = []
        
        # Check multiple possible locations for buttons
        raw_buttons = (
            node_data.get('buttons', []) or
            node_data.get('selected_template', {}).get('buttons', []) or
            []
        )
        
        for i, btn in enumerate(raw_buttons):
            btn_id = btn.get('id') or cls.generate_element_id(node_id, i)
            buttons.append({
                'id': btn_id,
                'text': btn.get('text', f'Button {i}'),
                'type': btn.get('type', 'QUICK_REPLY'),
                'index': i,
                'template_button_index': btn.get('template_button_index', i),
                'element_type': cls.element_type,
                **{k: v for k, v in btn.items() if k not in ['id', 'text', 'type', 'index', 'template_button_index', 'element_type']}
            })
        
        return buttons


class CardElement(InteractionElement):
    """
    Card interaction elements - for carousel/card templates.
    
    Each card can have its own buttons/actions. This is a placeholder
    for future WhatsApp carousel support.
    """
    
    element_type = "card"
    
    @classmethod
    def extract_from_template(cls, template: WATemplate) -> List[Dict]:
        """Extract cards from a carousel template."""
        if not template:
            return []
        
        # Check for carousel/card data in template
        cards_data = getattr(template, 'cards', None) or template.__dict__.get('cards', [])
        
        if not cards_data:
            return []
        
        cards = []
        for i, card in enumerate(cards_data):
            card_id = f"template-card-{i}"
            cards.append({
                'id': card_id,
                'text': card.get('title', f'Card {i}'),
                'type': 'CARD',
                'index': i,
                'element_type': cls.element_type,
                'image_url': card.get('image_url'),
                'description': card.get('description'),
                'buttons': card.get('buttons', []),  # Cards can have their own buttons
            })
        
        return cards
    
    @classmethod
    def extract_from_node_data(cls, node_data: Dict, node_id: str) -> List[Dict]:
        """Extract cards from node_data."""
        cards = []
        raw_cards = node_data.get('cards', [])
        
        for i, card in enumerate(raw_cards):
            card_id = card.get('id') or cls.generate_element_id(node_id, i)
            cards.append({
                'id': card_id,
                'text': card.get('title', f'Card {i}'),
                'type': 'CARD',
                'index': i,
                'element_type': cls.element_type,
                'image_url': card.get('image_url'),
                'description': card.get('description'),
                'buttons': card.get('buttons', []),
            })
        
        return cards


class ListItemElement(InteractionElement):
    """
    List item interaction elements - for interactive list messages.
    
    WhatsApp allows list messages with sections and selectable items.
    """
    
    element_type = "list_item"
    
    @classmethod
    def extract_from_template(cls, template: WATemplate) -> List[Dict]:
        """Extract list items from a list template."""
        if not template:
            return []
        
        # Check for list data in template
        sections = getattr(template, 'sections', None) or template.__dict__.get('sections', [])
        
        if not sections:
            return []
        
        items = []
        item_index = 0
        
        for section in sections:
            section_title = section.get('title', '')
            for row in section.get('rows', []):
                items.append({
                    'id': row.get('id', f"template-list-{item_index}"),
                    'text': row.get('title', f'Item {item_index}'),
                    'type': 'LIST_ITEM',
                    'index': item_index,
                    'element_type': cls.element_type,
                    'section': section_title,
                    'description': row.get('description'),
                })
                item_index += 1
        
        return items
    
    @classmethod
    def extract_from_node_data(cls, node_data: Dict, node_id: str) -> List[Dict]:
        """Extract list items from node_data."""
        items = []
        sections = node_data.get('sections', [])
        
        item_index = 0
        for section in sections:
            section_title = section.get('title', '')
            for row in section.get('rows', []):
                item_id = row.get('id') or cls.generate_element_id(node_id, item_index)
                items.append({
                    'id': item_id,
                    'text': row.get('title', f'Item {item_index}'),
                    'type': 'LIST_ITEM',
                    'index': item_index,
                    'element_type': cls.element_type,
                    'section': section_title,
                    'description': row.get('description'),
                })
                item_index += 1
        
        return items


# Registry of interaction element types
# Add new element types here when extending
INTERACTION_ELEMENT_TYPES: Dict[str, Type[InteractionElement]] = {
    'button': ButtonElement,
    'card': CardElement,
    'list_item': ListItemElement,
}


class InteractionElementExtractor:
    """
    Utility class to extract all interaction elements from a node.
    
    This provides a unified interface to get all clickable/actionable
    elements from a node, regardless of their type.
    """
    
    @classmethod
    def extract_all(
        cls,
        template: Optional[WATemplate],
        node_data: Dict,
        node_id: str,
        element_types: Optional[List[str]] = None
    ) -> Dict[str, List[Dict]]:
        """
        Extract all interaction elements from a node.
        
        Args:
            template: WATemplate (if any)
            node_data: The node's data dict
            node_id: The node's ID
            element_types: List of element types to extract (None = all)
            
        Returns:
            Dict mapping element_type -> list of elements
            Example: {
                'button': [{'id': '...', 'text': '...', ...}, ...],
                'card': [...],
            }
        """
        types_to_extract = element_types or list(INTERACTION_ELEMENT_TYPES.keys())
        result = {}
        
        for element_type in types_to_extract:
            extractor_class = INTERACTION_ELEMENT_TYPES.get(element_type)
            if not extractor_class:
                continue
            
            # Try node_data first, then template
            elements = extractor_class.extract_from_node_data(node_data, node_id)
            
            # If no elements in node_data and we have a template, try template
            if not elements and template:
                elements = extractor_class.extract_from_template(template)
                # Update IDs to be node-specific
                for i, elem in enumerate(elements):
                    if elem['id'].startswith('template-'):
                        elem['id'] = extractor_class.generate_element_id(node_id, i)
            
            if elements:
                result[element_type] = elements
        
        return result
    
    @classmethod
    def get_all_elements_flat(
        cls,
        template: Optional[WATemplate],
        node_data: Dict,
        node_id: str
    ) -> List[Dict]:
        """
        Get all interaction elements as a flat list.
        
        Useful for edge creation where any element type can trigger a transition.
        """
        all_elements = cls.extract_all(template, node_data, node_id)
        flat_list = []
        
        for element_type, elements in all_elements.items():
            flat_list.extend(elements)
        
        return flat_list
    
    @classmethod
    def find_element_by_text(
        cls,
        template: Optional[WATemplate],
        node_data: Dict,
        node_id: str,
        text: str
    ) -> Optional[Dict]:
        """Find an interaction element by its text."""
        all_elements = cls.get_all_elements_flat(template, node_data, node_id)
        
        for element in all_elements:
            if element.get('text') == text:
                return element
        
        return None
    
    @classmethod
    def find_element_by_id(
        cls,
        template: Optional[WATemplate],
        node_data: Dict,
        node_id: str,
        element_id: str
    ) -> Optional[Dict]:
        """Find an interaction element by its ID."""
        all_elements = cls.get_all_elements_flat(template, node_data, node_id)
        
        for element in all_elements:
            if element.get('id') == element_id:
                return element
        
        return None


# =============================================================================
# ChatFlow Processor (Main Class)
# =============================================================================


class ChatFlowProcessor:
    """
    Service class for processing chat flow operations and ReactFlow integration.
    """
    
    @staticmethod
    def create_flow_from_reactflow(
        flow_data: Dict, tenant, created_by,
        name: str = None, description: str = None,
    ) -> ChatFlow:
        """
        Create a ChatFlow with nodes and edges from ReactFlow JSON data.
        
        Now uses Pydantic validators to ensure data integrity before processing.
        The flow_data is first validated and converted to class objects for
        type safety and better error handling.
        
        Args:
            name: Flow name from the top-level request payload (takes priority).
            description: Flow description from the top-level request payload.
        """
        # Step 1: Validate the entire ReactFlow structure (Pydantic)
        try:
            validated_flow: ReactFlowData = validate_reactflow_data(flow_data)
        except ValueError as e:
            raise ValueError(f"Invalid ReactFlow data: {str(e)}")
        
        # Step 2: Run business rule validation
        validation_result = ChatFlowProcessor.validate_flow_rules(flow_data)
        if not validation_result.is_valid:
            error_messages = [f"[{v.rule_id}] {v.message}" for v in validation_result.errors]
            raise ValueError(f"Flow validation failed:\n" + "\n".join(error_messages))
        
        with transaction.atomic():
            # Step 2: Extract basic flow info — prefer top-level request
            # values passed by the serializer, then Pydantic-parsed, then raw.
            name = name or validated_flow.name or flow_data.get('name', 'Untitled Flow')
            description = description or validated_flow.description or flow_data.get('description', '')
            
            # Step 3: Find start template - look for first template node
            # (Start nodes are visual entry points, not template nodes)
            if not validated_flow.nodes:
                raise ValueError("Flow must have at least one node")
            
            # Find the first template node (skip start/end/condition nodes)
            start_template_id = None
            first_template_node = None
            
            # First, try to find the template connected to the start node via edges
            start_node = next(
                (n for n in validated_flow.nodes if n.type == 'start' or n.data.is_start_node),
                None
            )
            
            if start_node and validated_flow.edges:
                # Find edge from start node
                start_edge = next(
                    (e for e in validated_flow.edges if e.source == start_node.id),
                    None
                )
                if start_edge:
                    # Find target node of start edge
                    first_template_node = next(
                        (n for n in validated_flow.nodes if n.id == start_edge.target and n.type == 'template'),
                        None
                    )
            
            # Fallback: find first template node in the list
            if not first_template_node:
                first_template_node = next(
                    (n for n in validated_flow.nodes if n.type == 'template'),
                    None
                )
            
            if first_template_node:
                start_template_id = first_template_node.data.template_id
            
            # start_template is optional - flow can be saved without it
            start_template = None
            if start_template_id:
                try:
                    start_template = WATemplate.objects.get(
                        id=start_template_id,
                        wa_app__tenant=tenant,
                        status='APPROVED'
                    )
                except WATemplate.DoesNotExist:
                    raise ValueError("Start template must be approved and belong to your tenant")
            
            # Step 4: Create the flow with validated data
            flow = ChatFlow.objects.create(
                name=name,
                description=description,
                flow_data=validated_flow.model_dump(),  # Store validated data
                start_template=start_template,
                tenant=tenant,
                created_by=created_by
            )
            
            # Step 5: Create nodes using validated node objects
            nodes_map = {}
            for node_obj in validated_flow.nodes:
                node_obj = ChatFlowProcessor._create_node_from_validated_data(
                    flow, node_obj, tenant, nodes_map
                )
            
            # Step 6: Create edges using validated edge objects
            for edge_obj in validated_flow.edges:
                ChatFlowProcessor._create_edge_from_validated_data(
                    flow, edge_obj, nodes_map
                )
            
            return flow

    @staticmethod
    def update_flow_from_reactflow(flow: ChatFlow, flow_data: Dict, tenant) -> ChatFlow:
        """
        Update an existing ChatFlow, only recreating nodes/edges if flow structure changed.
        
        Uses a hash-based comparison to detect structural changes efficiently.
        Metadata (name, description, viewport, positions) can be updated without
        triggering a full rebuild.
        
        Args:
            flow: The existing ChatFlow to update
            flow_data: New ReactFlow JSON data
            tenant: The tenant for validation
            
        Returns:
            Updated ChatFlow instance
        """
        # Step 1: Validate the entire ReactFlow structure (Pydantic)
        try:
            validated_flow: ReactFlowData = validate_reactflow_data(flow_data)
        except ValueError as e:
            raise ValueError(f"Invalid ReactFlow data: {str(e)}")
        
        # Step 2: Run business rule validation
        validation_result = ChatFlowProcessor.validate_flow_rules(flow_data)
        if not validation_result.is_valid:
            error_messages = [f"[{v.rule_id}] {v.message}" for v in validation_result.errors]
            raise ValueError(f"Flow validation failed:\n" + "\n".join(error_messages))
        
        new_flow_data = validated_flow.model_dump()
        
        # Step 3: Compare structural hash to detect changes
        old_hash = ChatFlowProcessor._compute_flow_structure_hash(flow.flow_data)
        new_hash = ChatFlowProcessor._compute_flow_structure_hash(new_flow_data)
        structure_changed = old_hash != new_hash
        
        with transaction.atomic():
            # Step 4: Update metadata (always)
            if validated_flow.name:
                flow.name = validated_flow.name
            if validated_flow.description:
                flow.description = validated_flow.description
            
            # Always update flow_data (includes positions, viewport)
            flow.flow_data = new_flow_data
            
            if not structure_changed:
                # No structural changes — still sync node_data from the
                # incoming flow_data so execution-time config (api_url,
                # message_content, etc.) stays up-to-date.
                incoming_nodes_by_id = {
                    n['id']: n.get('data', {})
                    for n in new_flow_data.get('nodes', [])
                }
                for db_node in flow.nodes.all():
                    incoming_data = incoming_nodes_by_id.get(db_node.node_id)
                    if incoming_data is not None and incoming_data != db_node.node_data:
                        db_node.node_data = incoming_data
                        db_node.save(update_fields=['node_data'])
                flow.save(update_fields=['name', 'description', 'flow_data', 'updated_at'])
                return flow
            
            # Step 4: Structure changed - delete and recreate nodes/edges
            flow.nodes.all().delete()
            # edges are deleted automatically via CASCADE on source_node/target_node
            
            # Step 5: Update start_template if template node changed
            # Find the first template node (connected to start node or first in list)
            if validated_flow.nodes:
                start_template_id = None
                first_template_node = None
                
                # First, try to find the template connected to the start node via edges
                start_node = next(
                    (n for n in validated_flow.nodes if n.type == 'start' or n.data.is_start_node),
                    None
                )
                
                if start_node and validated_flow.edges:
                    start_edge = next(
                        (e for e in validated_flow.edges if e.source == start_node.id),
                        None
                    )
                    if start_edge:
                        first_template_node = next(
                            (n for n in validated_flow.nodes if n.id == start_edge.target and n.type == 'template'),
                            None
                        )
                
                # Fallback: find first template node in the list
                if not first_template_node:
                    first_template_node = next(
                        (n for n in validated_flow.nodes if n.type == 'template'),
                        None
                    )
                
                if first_template_node:
                    start_template_id = first_template_node.data.template_id
                
                if start_template_id:
                    try:
                        start_template = WATemplate.objects.get(
                            id=start_template_id,
                            status='APPROVED'
                        )
                        flow.start_template = start_template
                    except WATemplate.DoesNotExist:
                        raise ValueError("Start template must be approved and belong to your tenant")
                else:
                    flow.start_template = None
            
            flow.save()  # This updates updated_at timestamp
            
            # Step 6: Recreate nodes
            nodes_map = {}
            for node_obj in validated_flow.nodes:
                ChatFlowProcessor._create_node_from_validated_data(
                    flow, node_obj, tenant, nodes_map
                )
            
            # Step 7: Recreate edges
            for edge_obj in validated_flow.edges:
                ChatFlowProcessor._create_edge_from_validated_data(
                    flow, edge_obj, nodes_map
                )
            
            return flow

    @staticmethod
    def _compute_flow_structure_hash(flow_data: Dict) -> str:
        """
        Compute a SHA256 hash of the flow's structural data.
        
        Extracts only the parts that affect flow execution:
        - Node IDs, types, template_id, node_type, buttons
        - Edge IDs, source, target, sourceHandle, button_text
        
        Ignores visual-only properties:
        - Node positions (x, y)
        - Viewport (pan, zoom)
        - Colors, labels, etc.
        
        Returns:
            Hex digest of SHA256 hash, or empty string if no data
        """
        import hashlib
        import json
        
        if not flow_data:
            return ""
        
        # Extract structural data from nodes
        nodes_structure = []
        for node in flow_data.get('nodes', []):
            node_data = node.get('data', {})
            
            # Extract button structure (text and type only)
            buttons = []
            for btn in node_data.get('buttons', []):
                buttons.append({
                    'text': btn.get('text'),
                    'type': btn.get('type')
                })
            
            nodes_structure.append({
                'id': node.get('id'),
                'type': node.get('type'),
                'template_id': node_data.get('template_id'),
                'node_type': node_data.get('node_type'),
                'buttons': buttons,
                'is_start_node': node_data.get('is_start_node'),
                # API node execution config — changes here must trigger rebuild
                'api_url': node_data.get('api_url'),
                'api_method': node_data.get('api_method'),
                'api_headers': node_data.get('api_headers'),
                'api_params': node_data.get('api_params'),
                'api_body': node_data.get('api_body'),
                'api_body_type': node_data.get('api_body_type'),
                'api_timeout': node_data.get('api_timeout'),
                'api_retry_count': node_data.get('api_retry_count'),
                'api_response_codes': node_data.get('api_response_codes'),
                'response_variables': node_data.get('response_variables'),
                'variable_name': node_data.get('variable_name'),
                # Message / delay / handoff execution config
                'message_type': node_data.get('message_type'),
                'message_content': node_data.get('message_content'),
                'delay_type': node_data.get('delay_type'),
                'delay_duration': node_data.get('delay_duration'),
                'delay_unit': node_data.get('delay_unit'),
                'handoff_message': node_data.get('handoff_message'),
                'handoff_team_id': node_data.get('handoff_team_id'),
                'variables': node_data.get('variables'),
            })
        
        # Extract structural data from edges
        edges_structure = []
        for edge in flow_data.get('edges', []):
            edge_data = edge.get('data') or {}
            edges_structure.append({
                'id': edge.get('id'),
                'source': edge.get('source'),
                'target': edge.get('target'),
                'sourceHandle': edge.get('sourceHandle'),
                'button_text': edge_data.get('button_text'),
            })
        
        # Sort for consistent ordering
        nodes_structure.sort(key=lambda x: x.get('id', ''))
        edges_structure.sort(key=lambda x: x.get('id', ''))
        
        # Create canonical JSON and hash
        structure = {
            'nodes': nodes_structure,
            'edges': edges_structure
        }
        
        canonical_json = json.dumps(structure, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()

    @staticmethod
    def validate_flow_rules(
        flow_data: Dict,
        skip_db_checks: bool = False,
        return_warnings: bool = False
    ) -> ValidationResult:
        """
        Validate flow data against all business rules.
        
        This runs the rule engine validation which checks:
        - Structural rules (unique IDs, valid node references, etc.)
        - Session message rules (single edge, content limits, etc.)
        - Template rules (approved status, button edges, etc.)
        - Button rules (unique IDs/texts, length limits, etc.)
        
        Args:
            flow_data: The ReactFlow JSON structure
            skip_db_checks: If True, skip rules that require database access
                           (useful for frontend-only validation)
            return_warnings: If True, include warnings in is_valid check
            
        Returns:
            ValidationResult with:
            - is_valid: True if no ERROR-level violations
            - errors: List of ERROR-level violations
            - warnings: List of WARNING-level violations
            - violations: All violations
            
        Usage:
            result = ChatFlowProcessor.validate_flow_rules(flow_data)
            if not result.is_valid:
                for error in result.errors:
                    print(f"[{error.rule_id}] {error.message}")
        """
        validator = FlowValidatorService(skip_db_checks=skip_db_checks)
        return validator.validate(flow_data)

    @staticmethod
    def get_flow_validation_rules() -> List[Dict]:
        """
        Get documentation for all available validation rules.
        
        Useful for frontend to display rule information to users.
        
        Returns:
            List of dicts with rule_id, description, category, severity
        """
        return FlowValidatorService.get_all_rule_documentation()

    @staticmethod
    def _create_node_from_validated_data(
        flow: ChatFlow, 
        node_obj: ReactFlowNode, 
        tenant, 
        nodes_map: Dict[str, ChatFlowNode]
    ) -> ChatFlowNode:
        """
        Create a ChatFlowNode from validated ReactFlowNode object.
        
        This method handles different node types and extracts interaction elements
        (buttons, cards, list items, etc.) that can trigger flow transitions.
        
        Node Types:
        - template: Sends a WhatsApp template, extracts interaction elements
        - end: Terminal node, marks flow completion
        - condition: Logic node for branching
        - action: API/webhook integration node
        - delay: Wait/timing node (future)
        - handoff: Transfer to human agent (future)
        """
        node_data_dict = node_obj.data.model_dump()
        
        # ========================================
        # Handle non-template nodes (no interaction elements required)
        # These nodes don't have associated WhatsApp templates
        if node_obj.type in NON_TEMPLATE_NODE_TYPES:
            node = ChatFlowNode.objects.create(
                flow=flow,
                node_id=node_obj.id,
                node_type=node_obj.type,
                template=None,
                position_x=node_obj.position.x,
                position_y=node_obj.position.y,
                node_data=node_data_dict
            )
            nodes_map[node_obj.id] = node
            return node
        
        # ========================================
        # Handle template nodes with interaction elements
        # ========================================
        template_id = node_obj.data.template_id
        template = None
        
        # Step 1: Resolve the template
        if template_id:
            try:
                template = WATemplate.objects.get(
                    id=template_id,
                    wa_app__tenant=tenant,
                    status='APPROVED'
                )
            except WATemplate.DoesNotExist:
                raise ValueError(f"Template {template_id} must be approved and belong to your tenant")
        else:
            # Try to find template by matching buttons
            if node_obj.data.buttons:
                button_dicts = [btn.model_dump() for btn in node_obj.data.buttons]
                template = ChatFlowProcessor._find_matching_template(button_dicts, tenant)
                if not template:
                    raise ValueError(f"No matching approved template found for node {node_obj.id}")
        
        if not template:
            raise ValueError(f"Node {node_obj.id} must specify template_id or have matching interaction elements")
        
        # Step 2: Extract all interaction elements using the extensible framework
        # This handles buttons, cards, list items, etc. uniformly
        interaction_elements = InteractionElementExtractor.extract_all(
            template=template,
            node_data=node_data_dict,
            node_id=node_obj.id
        )
        
        # Step 3: Validate interaction elements against template
        if interaction_elements.get('button') and template.buttons:
            ChatFlowProcessor._validate_interaction_elements_with_template(
                interaction_elements['button'], 
                template,
                element_type='button'
            )
        
        # Step 4: Enrich node_data with extracted interaction elements
        # This ensures consistent structure regardless of input format
        enriched_node_data = {
            **node_data_dict,
            'interaction_elements': interaction_elements,
            # Keep backward compatibility with 'buttons' key
            'buttons': interaction_elements.get('button', []),
            # Add other element types for future use
            'cards': interaction_elements.get('card', []),
            'list_items': interaction_elements.get('list_item', []),
        }
        
        # Step 5: Create the node
        node = ChatFlowNode.objects.create(
            flow=flow,
            node_id=node_obj.id,
            node_type=node_obj.type,
            template=template,
            position_x=node_obj.position.x,
            position_y=node_obj.position.y,
            node_data=enriched_node_data
        )
        
        nodes_map[node_obj.id] = node
        return node
    
    @staticmethod
    def _validate_interaction_elements_with_template(
        elements: List[Dict],
        template: WATemplate,
        element_type: str = 'button'
    ) -> bool:
        """
        Validate interaction elements against a template.
        
        This is an extensible validation that works for buttons, cards, etc.
        """
        if element_type == 'button':
            template_buttons = template.buttons or []
            template_button_texts = {btn.get('text') for btn in template_buttons}
            
            for element in elements:
                element_text = element.get('text')
                if element_text and element_text not in template_button_texts:
                    raise ValueError(
                        f"Button '{element_text}' not found in template '{template.element_name}'"
                    )
        
        # Add validation for other element types as needed
        # elif element_type == 'card':
        #     ...
        
        return True
    
    @staticmethod
    def _create_edge_from_validated_data(
        flow: ChatFlow,
        edge_obj: ReactFlowEdge,
        nodes_map: Dict[str, ChatFlowNode]
    ) -> ChatFlowEdge:
        """
        Create a ChatFlowEdge from validated ReactFlowEdge object.
        
        Edges connect interaction elements (buttons, cards, list items) to target nodes.
        This method uses the InteractionElementExtractor to find the triggering element
        regardless of its type.
        
        For passthrough nodes (start, message), no button reference is required.
        """
        # Get source and target nodes
        source_node = nodes_map.get(edge_obj.source)
        target_node = nodes_map.get(edge_obj.target)
        
        if not source_node or not target_node:
            raise ValueError(f"Invalid edge: {edge_obj.source} -> {edge_obj.target}")
        
        # Check if source node is a passthrough node (doesn't require button reference)
        is_passthrough = is_passthrough_node(source_node.node_type)
        
        # Initialize edge properties
        element_id = None
        element_text = None
        element_type = "PASSTHROUGH" if is_passthrough else "QUICK_REPLY"
        
        # Extract edge data if present
        if edge_obj.data:
            element_id = edge_obj.data.button_id  # 'button_id' is legacy name, works for all elements
            element_text = edge_obj.data.button_text
            element_type = edge_obj.data.button_type or element_type
        
        # For passthrough nodes, create edge without button resolution
        if is_passthrough:
            edge_data = edge_obj.data.model_dump() if edge_obj.data else {}
            edge_data.update({
                'element_id': None,
                'element_type': 'PASSTHROUGH',
                'sourceHandle': edge_obj.sourceHandle,
                'is_passthrough': True,
            })
            
            edge = ChatFlowEdge.objects.create(
                flow=flow,
                edge_id=edge_obj.id,
                source_node=source_node,
                target_node=target_node,
                button_text="__PASSTHROUGH__",  # Special marker for passthrough edges
                button_type="PASSTHROUGH",
                edge_data=edge_data
            )
            
            return edge
        
        # For multi-output nodes (api, condition), sourceHandles represent
        # routing branches (e.g. 'status-200', 'if_true', 'else'), NOT buttons.
        # Create a routing edge with the handle metadata.
        if is_multi_output_node(source_node.node_type) and edge_obj.sourceHandle:
            handle = edge_obj.sourceHandle
            # Determine the routing label from the handle
            # API nodes: 'status-200' -> label '200', 'success' -> label 'success'
            # Condition nodes: 'if_true' -> label 'if_true', 'else' -> label 'else'
            routing_label = handle
            if handle.startswith('status-'):
                routing_label = handle  # Keep full handle as label
            
            edge_data = edge_obj.data.model_dump() if edge_obj.data else {}
            edge_data.update({
                'element_id': None,
                'element_type': 'ROUTING',
                'sourceHandle': handle,
                'routing_label': routing_label,
                'is_routing': True,
            })
            
            edge = ChatFlowEdge.objects.create(
                flow=flow,
                edge_id=edge_obj.id,
                source_node=source_node,
                target_node=target_node,
                button_text=routing_label,
                button_type="ROUTING",
                edge_data=edge_data
            )
            
            return edge
        
        # ========================================
        # Resolve interaction element using the extensible framework
        # ========================================
        
        # Method 1: Explicit element_id provided
        if element_id:
            element = InteractionElementExtractor.find_element_by_id(
                template=source_node.template,
                node_data=source_node.node_data,
                node_id=source_node.node_id,
                element_id=element_id
            )
            if not element and element_text:
                # Fallback: ID may be a synthetic "btn-{text}" from the frontend —
                # try matching by button_text instead so the edge still resolves.
                element = InteractionElementExtractor.find_element_by_text(
                    template=source_node.template,
                    node_data=source_node.node_data,
                    node_id=source_node.node_id,
                    text=element_text
                )
            if not element:
                raise ValueError(f"Element ID '{element_id}' not found in source node '{source_node.node_id}'")
            
            element_text = element['text']
            element_type = element.get('type', element_type)
        
        # Method 2: Explicit element_text provided
        elif element_text:
            element = InteractionElementExtractor.find_element_by_text(
                template=source_node.template,
                node_data=source_node.node_data,
                node_id=source_node.node_id,
                text=element_text
            )
            if element:
                element_id = element.get('id')
                element_type = element.get('type', element_type)
            elif source_node.template:
                # Validate the text exists in template for backward compatibility
                ChatFlowProcessor._validate_button_exists(source_node.template, element_text)
        
        # Method 3: sourceHandle like 'button-0', 'card-1', 'list_item-2'
        elif edge_obj.sourceHandle:
            # Handle passthrough handles ('bottom', 'default', 'output', etc.)
            passthrough_handles = {'bottom', 'default', 'output', 'out', None}
            if edge_obj.sourceHandle in passthrough_handles:
                # Allow passthrough from any node via bottom handle
                # User explicitly chose to connect from bottom instead of a button
                edge_data = edge_obj.data.model_dump() if edge_obj.data else {}
                edge_data.update({
                    'element_id': None,
                    'element_type': 'PASSTHROUGH',
                    'sourceHandle': edge_obj.sourceHandle,
                    'is_passthrough': True,
                })
                
                edge = ChatFlowEdge.objects.create(
                    flow=flow,
                    edge_id=edge_obj.id,
                    source_node=source_node,
                    target_node=target_node,
                    button_text="__PASSTHROUGH__",
                    button_type="PASSTHROUGH",
                    edge_data=edge_data
                )
                
                return edge
            
            # Try to resolve button/element from sourceHandle
            element = ChatFlowProcessor._resolve_element_from_source_handle(
                source_node=source_node,
                source_handle=edge_obj.sourceHandle
            )
            if element:
                element_id = element.get('id')
                element_text = element.get('text')
                element_type = element.get('type', element_type)
            else:
                raise ValueError(
                    f"Cannot resolve element from sourceHandle '{edge_obj.sourceHandle}' "
                    f"for node '{source_node.node_id}'. "
                    f"Use 'button-0', 'button-1', etc. or connect from 'bottom' for passthrough."
                )
        
        else:
            raise ValueError(
                f"Edge '{edge_obj.id}' must specify interaction element via "
                "data.button_id, data.button_text, or sourceHandle"
            )
        
        # Create the edge with element metadata
        edge_data = edge_obj.data.model_dump() if edge_obj.data else {}
        edge_data.update({
            'element_id': element_id,
            'element_type': element_type,
            'sourceHandle': edge_obj.sourceHandle,
        })
        
        edge = ChatFlowEdge.objects.create(
            flow=flow,
            edge_id=edge_obj.id,
            source_node=source_node,
            target_node=target_node,
            button_text=element_text,  # Keep for backward compatibility
            button_type=element_type,
            edge_data=edge_data
        )
        
        return edge
    
    @staticmethod
    def _resolve_element_from_source_handle(
        source_node: ChatFlowNode,
        source_handle: str
    ) -> Optional[Dict]:
        """
        Resolve an interaction element from a ReactFlow sourceHandle.
        
        sourceHandle formats:
        - 'button-0', 'button-1' -> Button at index 0, 1
        - 'card-0', 'card-1' -> Card at index 0, 1
        - 'list_item-0' -> List item at index 0
        - Legacy: 'btn-0' -> Button at index 0
        
        This is extensible - add new patterns as needed.
        """
        if not source_handle:
            return None
        
        # Parse the sourceHandle to get element type and index
        parts = source_handle.rsplit('-', 1)
        if len(parts) != 2:
            return None
        
        handle_type, index_str = parts
        
        try:
            index = int(index_str)
        except ValueError:
            return None
        
        # Map handle prefixes to element types
        handle_to_element_type = {
            'button': 'button',
            'btn': 'button',  # Legacy format
            'card': 'card',
            'list_item': 'list_item',
            'list': 'list_item',  # Alternative format
        }
        
        element_type = handle_to_element_type.get(handle_type)
        if not element_type:
            # Default to button for backward compatibility
            element_type = 'button'
        
        # Get all elements of this type
        all_elements = InteractionElementExtractor.extract_all(
            template=source_node.template,
            node_data=source_node.node_data,
            node_id=source_node.node_id,
            element_types=[element_type]
        )
        
        elements = all_elements.get(element_type, [])
        
        if index < len(elements):
            return elements[index]
        
        return None
    
    @staticmethod
    def _find_button_by_id_in_validated_node(node: ChatFlowNode, button_id: str) -> Optional[Dict]:
        """
        Find button by ID in node's validated button data.
        
        DEPRECATED: Use InteractionElementExtractor.find_element_by_id() instead.
        Kept for backward compatibility.
        """
        node_buttons = node.node_data.get('buttons', [])
        
        for button in node_buttons:
            if button.get('id') == button_id:
                return button
        
        return None
    
    @staticmethod
    def _validate_button_exists(template: WATemplate, button_text: str) -> bool:
        """
        Validate that a button exists in the template.
        """
        template_buttons = template.buttons or []
        
        button_exists = any(
            button.get('text') == button_text 
            for button in template_buttons
        )
        
        if not button_exists:
            raise ValueError(
                f"Button '{button_text}' does not exist in template '{template.element_name}'"
            )
        
        return True
    
    @staticmethod
    def _find_button_by_id(node: 'ChatFlowNode', button_id: str) -> Optional[Dict]:
        """
        Find button by ID in node's button data.
        """
        node_buttons = node.node_data.get('buttons', [])
        
        for button in node_buttons:
            if button.get('id') == button_id:
                return button
        
        return None
    
    @staticmethod
    def _find_matching_template(node_buttons: List[Dict], tenant) -> Optional[WATemplate]:
        """
        Find an approved template that matches the node's button structure.
        """
        if not node_buttons:
            return None
        
        # Get all button texts from node
        node_button_texts = {btn.get('text') for btn in node_buttons if btn.get('text')}
        
        # Find templates with matching buttons
        approved_templates = WATemplate.objects.filter(
            wa_app__tenant=tenant,
            status='APPROVED',
            buttons__isnull=False
        )
        
        for template in approved_templates:
            template_button_texts = {btn.get('text') for btn in template.buttons if btn.get('text')}
            
            # Check if node buttons are a subset of template buttons
            if node_button_texts.issubset(template_button_texts):
                return template
        
        return None
    
    @staticmethod
    def _validate_node_buttons_with_template(node_buttons: List[Dict], template: WATemplate) -> bool:
        """
        Validate that node buttons are consistent with template buttons.
        """
        template_buttons = template.buttons or []
        template_button_map = {btn.get('text'): btn for btn in template_buttons}
        
        for node_button in node_buttons:
            button_text = node_button.get('text')
            button_type = node_button.get('type')
            template_button_index = node_button.get('template_button_index')
            
            if not button_text:
                raise ValueError("Node button must have text")
            
            # Check if button exists in template
            if button_text not in template_button_map:
                raise ValueError(f"Node button '{button_text}' not found in template '{template.element_name}'")
            
            # Validate button type consistency
            template_button = template_button_map[button_text]
            template_button_type = template_button.get('type')
            
            if button_type and template_button_type and button_type != template_button_type:
                raise ValueError(
                    f"Button type mismatch for '{button_text}': "
                    f"node has '{button_type}', template has '{template_button_type}'"
                )
            
            # Validate template_button_index if provided
            if template_button_index is not None:
                if template_button_index >= len(template_buttons):
                    raise ValueError(f"Invalid template_button_index {template_button_index} for button '{button_text}'")
                
                indexed_button = template_buttons[template_button_index]
                if indexed_button.get('text') != button_text:
                    raise ValueError(
                        f"template_button_index {template_button_index} points to button "
                        f"'{indexed_button.get('text')}', expected '{button_text}'"
                    )
        
        return True
    
    @staticmethod
    def get_flow_statistics(flow: ChatFlow) -> Dict:
        """
        Get basic statistics for a chat flow.
        """
        # Node statistics
        nodes_count = flow.nodes.count()
        edges_count = flow.edges.count()
        
        return {
            'nodes_count': nodes_count,
            'edges_count': edges_count,
        }


class ReactFlowConverter:
    """
    Utility class for converting between internal models and ReactFlow format.
    Now uses validated class objects for better type safety and error handling.
    """
    
    @staticmethod
    def flow_to_reactflow(flow: ChatFlow) -> Dict:
        """
        Convert ChatFlow to ReactFlow JSON format with validation.
        """
        nodes = []
        edges = []
        
        # Convert nodes with enhanced button structure
        for node in flow.nodes.all():
            # Get buttons from node data or template
            node_buttons = node.node_data.get('buttons', [])
            template_buttons = node.template.buttons or []
            
            # If node doesn't have buttons in data, generate from template
            if not node_buttons and template_buttons:
                node_buttons = [
                    {
                        'id': f"{node.node_id}-btn-{i}",
                        'text': btn.get('text', ''),
                        'type': btn.get('type', 'QUICK_REPLY'),
                        'template_button_index': i,
                        **{k: v for k, v in btn.items() if k not in ['text', 'type']}
                    }
                    for i, btn in enumerate(template_buttons)
                ]
            
            # Ensure buttons have IDs and validate structure
            validated_buttons = []
            for i, button_data in enumerate(node_buttons):
                if not button_data.get('id'):
                    button_data['id'] = f"{node.node_id}-btn-{i}"
                
                # Validate button structure
                try:
                    validated_button = NodeButton(**button_data)
                    validated_buttons.append(validated_button.model_dump())
                except Exception as e:
                    # Log warning but continue with original data
                    print(f"Warning: Button validation failed for node {node.node_id}: {e}")
                    validated_buttons.append(button_data)
            
            # Create validated node data
            node_data_dict = {
                'template_id': node.template.id,
                'template_name': node.template.element_name,
                'template_content': node.template.content,
                'label': node.node_data.get('label', node.template.element_name),
                'buttons': validated_buttons,
                **{k: v for k, v in node.node_data.items() if k not in ['label', 'buttons']}
            }
            
            try:
                # Validate complete node structure
                validated_node_data = NodeData(**node_data_dict)
                validated_position = ReactFlowViewport(x=node.position_x, y=node.position_y)
                
                node_dict = {
                    'id': node.node_id,
                    'type': node.node_type,
                    'position': {
                        'x': node.position_x,
                        'y': node.position_y
                    },
                    'data': validated_node_data.model_dump()
                }
                
                # Validate complete node
                validated_node = ReactFlowNode(**node_dict)
                nodes.append(validated_node.model_dump())
                
            except Exception as e:
                # Fallback to original structure if validation fails
                print(f"Warning: Node validation failed for {node.node_id}: {e}")
                nodes.append({
                    'id': node.node_id,
                    'type': node.node_type,
                    'position': {
                        'x': node.position_x,
                        'y': node.position_y
                    },
                    'data': node_data_dict
                })
        
        # Convert edges with validation
        for edge in flow.edges.all():
            # Try to find button_id from source node
            source_node_data = next(
                (n for n in nodes if n['id'] == edge.source_node.node_id), {}
            )
            source_node_buttons = source_node_data.get('data', {}).get('buttons', [])
            
            button_id = None
            for button in source_node_buttons:
                if button.get('text') == edge.button_text:
                    button_id = button.get('id')
                    break
            
            # Create edge data dictionary
            edge_data_dict = {
                'button_text': edge.button_text,
                'button_type': edge.button_type,
                **edge.edge_data
            }
            
            # Add button_id if found
            if button_id:
                edge_data_dict['button_id'] = button_id
            
            try:
                # Validate edge data
                validated_edge_data = EdgeData(**edge_data_dict)
                
                edge_dict = {
                    'id': edge.edge_id,
                    'source': edge.source_node.node_id,
                    'target': edge.target_node.node_id,
                    'data': validated_edge_data.model_dump()
                }
                
                # Validate complete edge
                validated_edge = ReactFlowEdge(**edge_dict)
                edges.append(validated_edge.model_dump())
                
            except Exception as e:
                # Fallback to original structure if validation fails
                print(f"Warning: Edge validation failed for {edge.edge_id}: {e}")
                edges.append({
                    'id': edge.edge_id,
                    'source': edge.source_node.node_id,
                    'target': edge.target_node.node_id,
                    'data': edge_data_dict
                })
        
        # Validate viewport
        viewport_data = flow.flow_data.get('viewport', {'x': 0, 'y': 0, 'zoom': 1})
        try:
            validated_viewport = ReactFlowViewport(**viewport_data)
            viewport_dict = validated_viewport.model_dump()
        except Exception as e:
            print(f"Warning: Viewport validation failed: {e}")
            viewport_dict = viewport_data
        
        # Create final flow data structure
        flow_data = {
            'nodes': nodes,
            'edges': edges,
            'viewport': viewport_dict
        }
        
        # Validate complete flow structure
        try:
            validated_flow = ReactFlowData(**flow_data)
            return validated_flow.model_dump()
        except Exception as e:
            print(f"Warning: Complete flow validation failed: {e}")
            return flow_data
    
    @staticmethod 
    def migrate_legacy_flow_data(flow: ChatFlow) -> Dict:
        """
        Migrate legacy flow data to new improved structure with validation.
        This helps convert existing flows to the new button ID format.
        """
        current_data = flow.flow_data
        
        # First try to validate current data - if it passes, no migration needed
        try:
            validated_flow = validate_reactflow_data(current_data)
            # Check if nodes have button IDs
            has_button_ids = any(
                btn.id for node in validated_flow.nodes 
                for btn in node.data.buttons
            )
            if has_button_ids:
                return validated_flow.model_dump()
        except:
            # Current data doesn't validate, proceed with migration
            pass
        
        # Migrate to new structure
        migrated_nodes = []
        nodes = current_data.get('nodes', [])
        
        for node_data in nodes:
            node_id = node_data.get('id', '')
            node_type = node_data.get('type', 'template')
            position = node_data.get('position', {'x': 0, 'y': 0})
            data = node_data.get('data', {})
            
            # Generate buttons from template if not present
            template_id = data.get('template_id')
            node_buttons = data.get('buttons', [])
            
            if template_id and not node_buttons:
                try:
                    from wa.models import WATemplate
                    template = WATemplate.objects.get(id=template_id)
                    template_buttons = template.buttons or []
                    
                    node_buttons = [
                        {
                            'id': f"{node_id}-btn-{i}",
                            'text': btn.get('text', ''),
                            'type': btn.get('type', 'QUICK_REPLY'),
                            'template_button_index': i,
                            **{k: v for k, v in btn.items() if k not in ['text', 'type']}
                        }
                        for i, btn in enumerate(template_buttons)
                    ]
                except:
                    # If template not found, keep empty buttons
                    pass
            
            # Ensure existing buttons have IDs and validate
            validated_buttons = []
            for i, button_data in enumerate(node_buttons):
                if not button_data.get('id'):
                    button_data['id'] = f"{node_id}-btn-{i}"
                
                # Try to validate and clean button data
                try:
                    validated_button = NodeButton(**button_data)
                    validated_buttons.append(validated_button.model_dump())
                except:
                    # Keep original data if validation fails
                    validated_buttons.append(button_data)
            
            # Create validated node data
            try:
                validated_node_data = NodeData(
                    template_id=data.get('template_id'),
                    label=data.get('label', 'Untitled Node'),
                    buttons=validated_buttons,
                    **{k: v for k, v in data.items() if k not in ['template_id', 'label', 'buttons']}
                )
                
                validated_position = ReactFlowPosition(x=position.get('x', 0), y=position.get('y', 0))
                
                validated_node = ReactFlowNode(
                    id=node_id,
                    type=node_type,
                    position=validated_position,
                    data=validated_node_data
                )
                
                migrated_nodes.append(validated_node.model_dump())
                
            except Exception as e:
                # Fallback to original structure with minimal fixes
                print(f"Warning: Node migration failed for {node_id}: {e}")
                migrated_nodes.append({
                    'id': node_id,
                    'type': node_type,
                    'position': position,
                    'data': {
                        **data,
                        'buttons': validated_buttons
                    }
                })
        
        # Migrate edges to include button_id with validation
        edges = current_data.get('edges', [])
        migrated_edges = []
        
        for edge_data in edges:
            edge_id = edge_data.get('id', '')
            source = edge_data.get('source', '')
            target = edge_data.get('target', '')
            data = edge_data.get('data', {})
            
            button_text = data.get('button_text')
            
            # Find corresponding button_id in source node
            source_node = next((n for n in migrated_nodes if n['id'] == source), None)
            
            button_id = None
            if source_node and button_text:
                source_buttons = source_node.get('data', {}).get('buttons', [])
                for button in source_buttons:
                    if button.get('text') == button_text:
                        button_id = button.get('id')
                        break
            
            # Create edge data with validation
            edge_data_dict = {**data}
            if button_id:
                edge_data_dict['button_id'] = button_id
            
            try:
                validated_edge_data = EdgeData(**edge_data_dict)
                validated_edge = ReactFlowEdge(
                    id=edge_id,
                    source=source,
                    target=target,
                    data=validated_edge_data
                )
                migrated_edges.append(validated_edge.model_dump())
                
            except Exception as e:
                # Fallback to original structure
                print(f"Warning: Edge migration failed for {edge_id}: {e}")
                migrated_edges.append({
                    'id': edge_id,
                    'source': source,
                    'target': target,
                    'data': edge_data_dict
                })
        
        # Validate viewport
        viewport_data = current_data.get('viewport', {'x': 0, 'y': 0, 'zoom': 1})
        try:
            validated_viewport = ReactFlowViewport(**viewport_data)
            viewport_dict = validated_viewport.model_dump()
        except:
            viewport_dict = viewport_data
        
        # Create final migrated structure
        migrated_data = {
            'nodes': migrated_nodes,
            'edges': migrated_edges,
            'viewport': viewport_dict
        }
        
        # Final validation of complete structure
        try:
            validated_flow = ReactFlowData(**migrated_data)
            return validated_flow.model_dump()
        except Exception as e:
            print(f"Warning: Complete flow migration validation failed: {e}")
            return migrated_data
