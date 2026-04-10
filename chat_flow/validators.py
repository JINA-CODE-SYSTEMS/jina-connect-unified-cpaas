"""
ReactFlow Data Validators

This module provides Pydantic validators for ReactFlow JSON components
to ensure data integrity and proper structure before saving to the database.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)

from .constants import (API_RESPONSE_HANDLES, INTERACTIVE_BUTTON_TYPES,
                        MULTI_OUTPUT_NODE_TYPES, PASSTHROUGH_NODE_TYPES,
                        VALID_BUTTON_TYPES, VALID_NODE_TYPES,
                        is_multi_output_node, is_passthrough_node)


class ReactFlowPosition(BaseModel):
    """Validates ReactFlow node position coordinates."""
    x: Union[int, float] = Field(..., description="X coordinate of the node")
    y: Union[int, float] = Field(..., description="Y coordinate of the node")
    
    @field_validator('x', 'y')
    def validate_coordinates(cls, v):
        """Ensure coordinates are reasonable values."""
        if not isinstance(v, (int, float, Decimal)):
            raise ValueError("Coordinates must be numeric")
        
        # Reasonable bounds for canvas coordinates
        if abs(v) > 100000:
            raise ValueError("Coordinates must be within reasonable bounds (-100000 to 100000)")
        
        return float(v)


class ReactFlowViewport(BaseModel):
    """Validates ReactFlow viewport configuration."""
    x: Union[int, float] = Field(default=0, description="Viewport X offset")
    y: Union[int, float] = Field(default=0, description="Viewport Y offset") 
    zoom: Union[int, float] = Field(default=1, description="Viewport zoom level")
    
    @field_validator('x', 'y')
    def validate_offsets(cls, v):
        """Ensure viewport offsets are reasonable."""
        if not isinstance(v, (int, float, Decimal)):
            raise ValueError("Viewport offsets must be numeric")
        
        # Reasonable bounds for viewport
        if abs(v) > 50000:
            raise ValueError("Viewport offsets must be within reasonable bounds")
        
        return float(v)
    
    @field_validator('zoom')
    def validate_zoom(cls, v):
        """Ensure zoom level is within reasonable bounds."""
        if not isinstance(v, (int, float, Decimal)):
            raise ValueError("Zoom must be numeric")
        
        zoom_val = float(v)
        if zoom_val < 0.1 or zoom_val > 10:
            raise ValueError("Zoom must be between 0.1 and 10")
        
        return zoom_val


class NodeButton(BaseModel):
    """Validates individual button within a node."""
    id: str = Field(..., description="Unique button identifier within the node")
    text: Optional[str] = Field(None, min_length=1, max_length=20, description="Button display text")
    title: Optional[str] = Field(None, min_length=1, max_length=20, description="Button display text (alias for text, used by message nodes)")
    type: str = Field(default="QUICK_REPLY", description="Button type (QUICK_REPLY, URL, PHONE_NUMBER, etc.)")
    template_button_index: Optional[int] = Field(None, description="Index in the original template buttons")
    
    # Allow additional fields for button-specific data
    model_config = ConfigDict(extra="allow")
    
    @model_validator(mode='before')
    @classmethod
    def normalise_text_title(cls, values):
        """Accept 'title' as a fallback for 'text' (message node buttons use title)."""
        if isinstance(values, dict):
            text = values.get('text')
            title = values.get('title')
            if not text and title:
                values['text'] = title
            elif not text and not title:
                # generate from id when both missing
                btn_id = values.get('id', '')
                values['text'] = btn_id.replace('btn-', '') if btn_id else 'Button'
        return values
    
    @field_validator('id')
    def validate_button_id(cls, v):
        """Ensure button ID is properly formatted."""
        if not v or not isinstance(v, str):
            raise ValueError("Button ID must be a non-empty string")
        
        # Basic format validation - no special characters that could break JSON
        if any(char in v for char in ['"', "'", '\\', '\n', '\r', '\t']):
            raise ValueError("Button ID cannot contain quotes, backslashes, or newlines")
        
        return v.strip()
    
    @field_validator('text')
    def validate_button_text(cls, v):
        """Ensure button text meets WhatsApp requirements."""
        if not v or not isinstance(v, str):
            raise ValueError("Button text must be a non-empty string")
        
        text = v.strip()
        if len(text) == 0:
            raise ValueError("Button text cannot be empty or only whitespace")
        
        # WhatsApp button text limitations
        if len(text) > 20:
            raise ValueError("Button text cannot exceed 20 characters")
        
        return text
    
    @field_validator('type')
    def validate_button_type(cls, v):
        """Ensure button type is valid."""
        if v not in VALID_BUTTON_TYPES:
            raise ValueError(f"Button type must be one of: {', '.join(VALID_BUTTON_TYPES)}")
        
        return v
    
    @field_validator('template_button_index')
    def validate_template_index(cls, v):
        """Ensure template button index is reasonable."""
        if v is not None:
            if not isinstance(v, int) or v < 0:
                raise ValueError("Template button index must be a non-negative integer")
            
            if v > 10:  # WhatsApp typically allows max 3-10 buttons
                raise ValueError("Template button index seems too high (max 10)")
        
        return v


class NodeData(BaseModel):
    """Validates ReactFlow node data payload."""
    template_id: Optional[Union[int, str]] = Field(None, description="Associated WhatsApp template ID (integer or UUID string)")
    label: str = Field(..., min_length=1, max_length=100, description="Node display label")
    buttons: List[NodeButton] = Field(default_factory=list, description="Interactive buttons for this node")
    
    # Allow additional custom fields
    model_config = ConfigDict(extra="allow")
    
    @field_validator('template_id')
    def validate_template_id(cls, v):
        """Ensure template ID is valid if provided (positive int or UUID string)."""
        if v is not None:
            if isinstance(v, int):
                if v <= 0:
                    raise ValueError("Template ID must be a positive integer")
            elif isinstance(v, str):
                import uuid
                try:
                    uuid.UUID(v)
                except ValueError:
                    raise ValueError("Template ID must be a positive integer or a valid UUID string")
            else:
                raise ValueError("Template ID must be a positive integer or a valid UUID string")
        return v
    
    @field_validator('label')
    def validate_label(cls, v):
        """Ensure label is properly formatted."""
        if not v or not isinstance(v, str):
            raise ValueError("Label must be a non-empty string")
        
        label = v.strip()
        if len(label) == 0:
            raise ValueError("Label cannot be empty or only whitespace")
        
        return label
    
    @field_validator('buttons')
    def validate_buttons_list(cls, v):
        """Validate the buttons list."""
        if not isinstance(v, list):
            raise ValueError("Buttons must be a list")
        
        # Check for duplicate button IDs
        button_ids = [btn.id for btn in v if hasattr(btn, 'id')]
        if len(button_ids) != len(set(button_ids)):
            raise ValueError("Button IDs must be unique within a node")
        
        # Check for duplicate button texts
        button_texts = [btn.text for btn in v if hasattr(btn, 'text')]
        if len(button_texts) != len(set(button_texts)):
            raise ValueError("Button texts must be unique within a node")
        
        # WhatsApp typically limits buttons per message
        if len(v) > 10:
            raise ValueError("Too many buttons (max 10 per node)")
        
        return v


class ReactFlowNode(BaseModel):
    """Validates complete ReactFlow node structure."""
    id: str = Field(..., description="Unique node identifier")
    type: str = Field(default="template", description="Node type")
    position: ReactFlowPosition = Field(..., description="Node position on canvas")
    data: NodeData = Field(..., description="Node-specific data and configuration")
    
    @field_validator('id')
    def validate_node_id(cls, v):
        """Ensure node ID is properly formatted."""
        if not v or not isinstance(v, str):
            raise ValueError("Node ID must be a non-empty string")
        
        # Basic format validation
        if any(char in v for char in ['"', "'", '\\', '\n', '\r', '\t']):
            raise ValueError("Node ID cannot contain quotes, backslashes, or newlines")
        
        # Reasonable length limit
        if len(v) > 255:
            raise ValueError("Node ID too long (max 255 characters)")
        
        return v.strip()
    
    @field_validator('type')
    def validate_node_type(cls, v):
        """Ensure node type is valid."""
        if v not in VALID_NODE_TYPES:
            raise ValueError(f"Node type must be one of: {', '.join(VALID_NODE_TYPES)}")
        
        return v


class EdgeData(BaseModel):
    """Validates ReactFlow edge data payload."""
    button_id: Optional[str] = Field(None, description="Button ID that triggers this transition")
    button_text: Optional[str] = Field(None, description="Button text that triggers this transition")
    button_type: str = Field(default="QUICK_REPLY", description="Type of button triggering transition")
    
    # Allow additional custom fields
    model_config = ConfigDict(extra="allow")
    
    # Note: We don't require button_id or button_text here because
    # the button info might come from edge.sourceHandle (e.g., 'button-0')
    # The validation happens at the ReactFlowEdge level instead.
    
    @field_validator('button_id')
    def validate_button_id(cls, v):
        """Validate button ID format if provided."""
        if v is not None:
            if not isinstance(v, str) or len(v.strip()) == 0:
                raise ValueError("Button ID must be a non-empty string")
            
            # Basic format validation
            if any(char in v for char in ['"', "'", '\\', '\n', '\r', '\t']):
                raise ValueError("Button ID cannot contain quotes, backslashes, or newlines")
        
        return v.strip() if v else v
    
    @field_validator('button_text')
    def validate_button_text(cls, v):
        """Validate button text if provided."""
        if v is not None:
            if not isinstance(v, str) or len(v.strip()) == 0:
                raise ValueError("Button text must be a non-empty string")
            
            # WhatsApp limitations
            if len(v.strip()) > 20:
                raise ValueError("Button text cannot exceed 20 characters")
        
        return v.strip() if v else v


class ReactFlowEdge(BaseModel):
    """Validates complete ReactFlow edge structure."""
    id: str = Field(..., description="Unique edge identifier")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    sourceHandle: Optional[str] = Field(None, description="Source handle (e.g., 'button-0')")
    targetHandle: Optional[str] = Field(None, description="Target handle")
    data: Optional[EdgeData] = Field(None, description="Edge-specific data and configuration")
    
    # Allow additional fields from ReactFlow
    model_config = ConfigDict(extra="allow")
    
    # Note: Button reference validation is done at ReactFlowData level
    # because we need node context to know if source is a passthrough node
    # (start, message nodes don't require button references)
    
    def get_button_index(self) -> Optional[int]:
        """Extract button index from sourceHandle like 'button-0' -> 0"""
        if self.sourceHandle and self.sourceHandle.startswith('button-'):
            try:
                return int(self.sourceHandle.split('-')[1])
            except (IndexError, ValueError):
                return None
        return None
    
    @field_validator('id')
    def validate_edge_id(cls, v):
        """Ensure edge ID is properly formatted."""
        if not v or not isinstance(v, str):
            raise ValueError("Edge ID must be a non-empty string")
        
        # Basic format validation
        if any(char in v for char in ['"', "'", '\\', '\n', '\r', '\t']):
            raise ValueError("Edge ID cannot contain quotes, backslashes, or newlines")
        
        # Reasonable length limit — ReactFlow auto-generates IDs like
        # reactflow__edge-{uuid}{handle}-{uuid}{handle} which can be ~120 chars
        if len(v) > 255:
            raise ValueError("Edge ID too long (max 255 characters)")
        
        return v.strip()
    
    @field_validator('source', 'target')
    def validate_node_references(cls, v):
        """Ensure source and target node IDs are valid."""
        if not v or not isinstance(v, str):
            raise ValueError("Node reference must be a non-empty string")
        
        return v.strip()


class ReactFlowData(BaseModel):
    """Validates complete ReactFlow JSON structure."""
    nodes: List[ReactFlowNode] = Field(..., min_length=1, description="List of flow nodes")
    edges: List[ReactFlowEdge] = Field(default_factory=list, description="List of flow edges")
    viewport: ReactFlowViewport = Field(default_factory=ReactFlowViewport, description="Canvas viewport settings")
    
    # Allow additional flow-level metadata
    name: Optional[str] = Field(None, max_length=100, description="Flow name")
    description: Optional[str] = Field(None, max_length=500, description="Flow description")
    
    model_config = ConfigDict(extra="allow")
    
    @field_validator('nodes')
    def validate_nodes_list(cls, v):
        """Validate nodes list and check for duplicates."""
        if not isinstance(v, list) or len(v) == 0:
            raise ValueError("Flow must have at least one node")
        
        # Check for duplicate node IDs
        node_ids = [node.id for node in v]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Node IDs must be unique within the flow")
        
        return v
    
    @field_validator('edges')
    def validate_edges_list(cls, v):
        """Validate edges list and check for duplicates."""
        if not isinstance(v, list):
            raise ValueError("Edges must be a list")
        
        # Check for duplicate edge IDs
        edge_ids = [edge.id for edge in v]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("Edge IDs must be unique within the flow")
        
        return v
    
    @model_validator(mode='after')
    def validate_flow_structure(self):
        """Validate the overall flow structure and relationships."""
        nodes = self.nodes
        edges = self.edges
        
        if not nodes:
            return self
        
        # Create set of valid node IDs
        node_ids = {node.id for node in nodes}
        
        # Validate edge references
        for edge in edges:
            if edge.source not in node_ids:
                raise ValueError(f"Edge {edge.id} references unknown source node: {edge.source}")
            
            if edge.target not in node_ids:
                raise ValueError(f"Edge {edge.id} references unknown target node: {edge.target}")
            
            if edge.source == edge.target:
                raise ValueError(f"Edge {edge.id} cannot connect a node to itself")
        
        # Validate button references in edges
        for edge in edges:
            # Get button reference - can come from data or sourceHandle
            button_id = edge.data.button_id if edge.data else None
            button_text = edge.data.button_text if edge.data else None
            button_type = edge.data.button_type if edge.data else None
            source_handle = edge.sourceHandle
            
            # Find source node
            source_node = next((n for n in nodes if n.id == edge.source), None)
            if not source_node:
                continue
            
            # Skip validation for passthrough nodes (they don't require button references)
            if is_passthrough_node(source_node.type):
                continue
            
            # Skip validation for multi-output nodes (api, condition)
            # These have their own validation rules in the rules engine
            if is_multi_output_node(source_node.type):
                continue
            
            # Get buttons from source node
            node_buttons = source_node.data.buttons or []
            
            # Check if template has only non-QUICK_REPLY buttons
            # Non-interactive buttons (PHONE_NUMBER, URL, etc.) don't trigger user responses
            # so edges from them are essentially passthrough and don't need strict validation
            has_quick_reply = any(
                btn.type == 'QUICK_REPLY' for btn in node_buttons
            )
            
            # For non-QUICK_REPLY button templates, allow any edge (passthrough)
            if not has_quick_reply:
                # Just verify button_text matches if provided (for consistency)
                if button_text and node_buttons:
                    button_exists = any(btn.text == button_text for btn in node_buttons)
                    if not button_exists:
                        raise ValueError(
                            f"Button '{button_text}' not found in the template. "
                            f"Available buttons: {[btn.text for btn in node_buttons]}"
                        )
                continue
            
            # For template nodes with QUICK_REPLY buttons, require button reference
            has_button_ref = button_id or button_text or (
                source_handle and source_handle.startswith('button-')
            )
            
            # Allow passthrough edges via 'bottom' handle (but only if no QUICK_REPLY buttons)
            is_passthrough_edge = source_handle in ('bottom', 'default', None) and not has_button_ref
            
            if is_passthrough_edge:
                # Template has QUICK_REPLY buttons, so edge should use button reference
                raise ValueError(
                    f"Template has interactive buttons (Good, Bad, etc.). "
                    f"Please connect the edge from a specific button, not from the node directly."
                )
            
            if not has_button_ref:
                raise ValueError(
                    f"Edge from template must specify which button triggers it. "
                    f"Please connect the edge from a button handle."
                )
            
            # Validate button reference - prioritize button_text over button_id
            # since button_text is what actually matters for routing
            # (Frontend may generate synthetic button_ids like 'btn-0')
            if button_text:
                # Validate button_text exists
                button_exists = any(btn.text == button_text for btn in node_buttons)
                if not button_exists:
                    raise ValueError(
                        f"Button '{button_text}' not found in the template. "
                        f"Available buttons: {[btn.text for btn in node_buttons]}"
                    )
            
            elif button_id:
                # Validate button_id exists (only if button_text wasn't provided)
                button_exists = any(btn.id == button_id for btn in node_buttons)
                if not button_exists:
                    # Don't fail on synthetic button_ids like 'btn-0' if sourceHandle is valid
                    if source_handle and source_handle.startswith('button-'):
                        button_index = edge.get_button_index()
                        if button_index is not None and button_index < len(node_buttons):
                            continue  # sourceHandle index is valid, allow it
                    raise ValueError(
                        f"Button ID '{button_id}' not found in the template. "
                        f"Available button IDs: {[btn.id for btn in node_buttons]}"
                    )
            
            elif source_handle and source_handle.startswith('button-'):
                # Validate sourceHandle index is valid
                button_index = edge.get_button_index()
                if button_index is not None:
                    if node_buttons and button_index >= len(node_buttons):
                        raise ValueError(
                            f"Button index {button_index} is out of range. "
                            f"Template only has {len(node_buttons)} button(s)."
                        )
        
        return self


# Convenience validation functions
def validate_reactflow_data(data: Dict[str, Any]) -> ReactFlowData:
    """
    Validate and parse ReactFlow data using Pydantic.
    
    Args:
        data: Raw dictionary from frontend or API
        
    Returns:
        Validated ReactFlowData instance
        
    Raises:
        ValueError: If validation fails (with user-friendly message)
    """
    from pydantic import ValidationError
    
    try:
        return ReactFlowData(**data)
    except ValidationError as e:
        # Convert Pydantic errors to user-friendly messages
        friendly_errors = []
        for error in e.errors():
            loc = error.get('loc', ())
            msg = error.get('msg', 'Unknown error')
            
            # Build a user-friendly location string
            if loc:
                # e.g., ('nodes', 0, 'data', 'template_id') -> "Node 1"
                # e.g., ('edges', 2, 'source') -> "Edge 3"
                location_parts = []
                for i, part in enumerate(loc):
                    if part == 'nodes' and i + 1 < len(loc) and isinstance(loc[i + 1], int):
                        location_parts.append(f"Node {loc[i + 1] + 1}")
                    elif part == 'edges' and i + 1 < len(loc) and isinstance(loc[i + 1], int):
                        location_parts.append(f"Edge {loc[i + 1] + 1}")
                    elif isinstance(part, str) and part not in ('nodes', 'edges', 'data', 'position'):
                        location_parts.append(part)
                
                location = ' > '.join(location_parts) if location_parts else 'Flow data'
            else:
                location = 'Flow data'
            
            # Clean up common Pydantic message prefixes
            msg = msg.replace('Value error, ', '')
            
            friendly_errors.append(f"{location}: {msg}")
        
        if friendly_errors:
            raise ValueError("; ".join(friendly_errors))
        else:
            raise ValueError("Invalid flow data. Please check your flow configuration.")
    except ValueError:
        # Re-raise ValueError (from model_validator) as-is
        raise
    except Exception as e:
        raise ValueError(f"Invalid flow data: {str(e)}")


def validate_node_data(data: Dict[str, Any]) -> ReactFlowNode:
    """
    Validate individual node data.
    
    Args:
        data: Raw node dictionary
        
    Returns:
        Validated ReactFlowNode instance
        
    Raises:
        ValueError: If validation fails
    """
    try:
        return ReactFlowNode(**data)
    except Exception as e:
        raise ValueError(f"Node data validation failed: {str(e)}")


def validate_edge_data(data: Dict[str, Any]) -> ReactFlowEdge:
    """
    Validate individual edge data.
    
    Args:
        data: Raw edge dictionary
        
    Returns:
        Validated ReactFlowEdge instance
        
    Raises:
        ValueError: If validation fails
    """
    try:
        return ReactFlowEdge(**data)
    except Exception as e:
        raise ValueError(f"Edge data validation failed: {str(e)}")


def validate_viewport_data(data: Dict[str, Any]) -> ReactFlowViewport:
    """
    Validate viewport data.
    
    Args:
        data: Raw viewport dictionary
        
    Returns:
        Validated ReactFlowViewport instance
        
    Raises:
        ValueError: If validation fails
    """
    try:
        return ReactFlowViewport(**data)
    except Exception as e:
        raise ValueError(f"Viewport data validation failed: {str(e)}")
