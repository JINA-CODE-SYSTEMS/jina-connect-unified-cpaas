"""
ChatFlow Constants

Central source of truth for node types, button types, and other constants
used across the ChatFlow module.
"""

from typing import FrozenSet, Tuple

# =============================================================================
# NODE TYPES
# =============================================================================

# All valid node types in the flow editor
VALID_NODE_TYPES: Tuple[str, ...] = (
    'template',   # WhatsApp template message node
    'start',      # Flow entry point
    'end',        # Flow termination point
    'condition',  # Conditional branching logic
    'action',     # Custom action execution
    'delay',      # Timed delay before continuing
    'message',    # Session message (free-form text/media)
    'handoff',    # Handoff to human agent (Team Inbox)
    'api',        # HTTP API call node with multiple response outputs
)

# Node types that don't require button references for outgoing edges
# These nodes automatically route to the next node without user interaction
PASSTHROUGH_NODE_TYPES: Tuple[str, ...] = (
    'start',      # Always passthrough, immediately routes to next node
    'end',        # Flow termination, no outgoing edges expected
    'condition',  # Special logic node, routes based on condition
    'action',     # Special logic node, routes after action
    'delay',      # Waits for specified time then continues
    'handoff',    # Handoff to agent, may continue after
    # Note: 'api' is NOT passthrough - it has multiple outputs based on response status
    # Note: 'message' REMOVED — message nodes with QUICK_REPLY buttons must
    #        wait for user input.  Plain (button-less) message nodes still
    #        passthrough via the 'bottom' sourceHandle, which flow_processor
    #        recognises as a passthrough handle.
)

# Node types that are not template nodes (don't have template_id)
NON_TEMPLATE_NODE_TYPES: Tuple[str, ...] = (
    'start',
    'message',
    'end',
    'condition',
    'action',
    'delay',
    'handoff',
    'api',
)

# Node types that have multiple conditional outputs (not button-based)
MULTI_OUTPUT_NODE_TYPES: Tuple[str, ...] = (
    'condition',  # Routes based on condition evaluation
    'api',        # Routes based on HTTP response status
)

# =============================================================================
# BUTTON TYPES
# =============================================================================

# Valid button types for WhatsApp templates
VALID_BUTTON_TYPES: Tuple[str, ...] = (
    'QUICK_REPLY',    # Quick reply button (triggers response)
    'URL',            # URL button (opens link)
    'PHONE_NUMBER',   # Phone number button (opens dialer)
    'OTP',            # OTP button (for verification)
    'COPY_CODE',      # Copy code button
    'CALL_TO_ACTION', # Call to action button
)

# Button types that trigger user response (interactive)
INTERACTIVE_BUTTON_TYPES: Tuple[str, ...] = (
    'QUICK_REPLY',
)

# Button types that don't require edge validation (non-interactive)
NON_INTERACTIVE_BUTTON_TYPES: Tuple[str, ...] = (
    'URL',
    'PHONE_NUMBER',
    'OTP',
    'COPY_CODE',
    'CALL_TO_ACTION',
)

# =============================================================================
# EDGE HANDLES
# =============================================================================

# Source handles that indicate passthrough (no button reference required)
PASSTHROUGH_SOURCE_HANDLES: FrozenSet[str] = frozenset({
    'bottom',
    'default',
    'output',
    'out',
})

# =============================================================================
# SESSION MESSAGE TYPES
# =============================================================================

# Valid content types for session messages
SESSION_MESSAGE_TYPES: Tuple[str, ...] = (
    'text',
    'image',
    'video',
    'audio',
    'document',
    'sticker',
    'location',
    'order_details',
    'order_status',
)

# =============================================================================
# API NODE CONSTANTS
# =============================================================================

# Valid HTTP methods for API node
VALID_HTTP_METHODS: Tuple[str, ...] = (
    'GET',
    'POST',
    'PUT',
    'PATCH',
    'DELETE',
)

# Common response status categories for API node outputs
# These are the sourceHandle values for edges from API nodes
API_RESPONSE_HANDLES: Tuple[str, ...] = (
    'success',      # 2xx responses (200, 201, etc.)
    'client_error', # 4xx responses (400, 401, 403, 404, etc.)
    'server_error', # 5xx responses (500, 502, 503, etc.)
    'timeout',      # Request timeout
    'error',        # Network/connection errors
)

# Default headers for API requests
DEFAULT_API_HEADERS: dict = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def is_passthrough_node(node_type: str) -> bool:
    """Check if a node type is a passthrough node."""
    return node_type in PASSTHROUGH_NODE_TYPES


def is_template_node(node_type: str) -> bool:
    """Check if a node type is a template node."""
    return node_type not in NON_TEMPLATE_NODE_TYPES


def is_interactive_button(button_type: str) -> bool:
    """Check if a button type triggers user response."""
    return button_type in INTERACTIVE_BUTTON_TYPES


def is_passthrough_handle(source_handle: str | None) -> bool:
    """Check if a source handle indicates passthrough edge."""
    return source_handle is None or source_handle in PASSTHROUGH_SOURCE_HANDLES


def is_multi_output_node(node_type: str) -> bool:
    """Check if a node type has multiple conditional outputs."""
    return node_type in MULTI_OUTPUT_NODE_TYPES


def is_valid_http_method(method: str) -> bool:
    """Check if an HTTP method is valid."""
    return method.upper() in VALID_HTTP_METHODS


def is_valid_api_handle(handle: str | None) -> bool:
    """Check if a source handle is a valid API response handle."""
    return handle in API_RESPONSE_HANDLES
