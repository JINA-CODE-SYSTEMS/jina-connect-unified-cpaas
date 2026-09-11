"""
ChatFlow Constants

Central source of truth for node types, button types, and other constants
used across the ChatFlow module.
"""

from typing import Dict, FrozenSet, Tuple

# =============================================================================
# NODE TYPES
# =============================================================================

# All valid node types in the flow editor
VALID_NODE_TYPES: Tuple[str, ...] = (
    "template",  # WhatsApp template message node
    "start",  # Flow entry point
    "end",  # Flow termination point
    "condition",  # Conditional branching logic
    "action",  # Declared for the editor; no runtime behaviour yet (#273)
    "delay",  # Timed delay before continuing
    "message",  # Session message (free-form text/media)
    "handoff",  # Handoff to human agent (Team Inbox)
    "api",  # HTTP API call node with multiple response outputs
)

# Node types that don't require button references for outgoing edges
# These nodes automatically route to the next node without user interaction
PASSTHROUGH_NODE_TYPES: Tuple[str, ...] = (
    "start",  # Always passthrough, immediately routes to next node
    "end",  # Flow termination, no outgoing edges expected
    "condition",  # Special logic node, routes based on condition
    "action",  # Special logic node, routes after action
    "delay",  # Waits for specified time then continues
    "handoff",  # Handoff to agent, may continue after
    # Note: 'api' is NOT passthrough - it has multiple outputs based on response status
    # Note: 'message' REMOVED — message nodes with QUICK_REPLY buttons must
    #        wait for user input.  Plain (button-less) message nodes still
    #        passthrough via the 'bottom' sourceHandle, which flow_processor
    #        recognises as a passthrough handle.
)

# Node types that are not template nodes (don't have template_id)
NON_TEMPLATE_NODE_TYPES: Tuple[str, ...] = (
    "start",
    "message",
    "end",
    "condition",
    "action",
    "delay",
    "handoff",
    "api",
)

# Node types that have multiple conditional outputs (not button-based)
MULTI_OUTPUT_NODE_TYPES: Tuple[str, ...] = (
    "condition",  # Routes based on condition evaluation
    "api",  # Routes based on HTTP response status
)

# =============================================================================
# BUTTON TYPES
# =============================================================================

# Valid button types for WhatsApp templates
VALID_BUTTON_TYPES: Tuple[str, ...] = (
    "QUICK_REPLY",  # Quick reply button (triggers response)
    "URL",  # URL button (opens link)
    "PHONE_NUMBER",  # Phone number button (opens dialer)
    "OTP",  # OTP button (for verification)
    "COPY_CODE",  # Copy code button
    "CALL_TO_ACTION",  # Call to action button
)

# Button types that trigger user response (interactive)
INTERACTIVE_BUTTON_TYPES: Tuple[str, ...] = ("QUICK_REPLY",)

# Button types that don't require edge validation (non-interactive)
NON_INTERACTIVE_BUTTON_TYPES: Tuple[str, ...] = (
    "URL",
    "PHONE_NUMBER",
    "OTP",
    "COPY_CODE",
    "CALL_TO_ACTION",
)

# =============================================================================
# EDGE HANDLES
# =============================================================================

# Source handles that indicate passthrough (no button reference required)
PASSTHROUGH_SOURCE_HANDLES: FrozenSet[str] = frozenset(
    {
        "bottom",
        "default",
        "output",
        "out",
    }
)

# =============================================================================
# SESSION MESSAGE TYPES
# =============================================================================

# Valid content types for session messages.
#
# This tuple is the authoring contract: SESSION_006 rejects a message node
# whose ``message_type`` is not listed here, and ``send_session_message``
# has a branch for every entry.  The two lists used to be maintained
# independently — authoring accepted ``interactive_list`` while the executor
# sent the body as a plain paragraph — so the pair is now asserted by
# ``chat_flow/test_node_type_coverage.py`` (#273).
SESSION_MESSAGE_TYPES: Tuple[str, ...] = (
    "text",
    "image",
    "video",
    "audio",
    "document",
    "sticker",
    "location",
    "contacts",
    "reaction",
    "interactive_button",
    "interactive_list",
    "cta_url",
    "order_details",
    "order_status",
)

# Spellings the flow editor has emitted over time, mapped onto the canonical
# type above.  Kept so flows saved before #273 keep validating and sending;
# the validation rules already accepted both halves of each pair.
SESSION_MESSAGE_TYPE_ALIASES: Dict[str, str] = {
    "button": "interactive_button",
    "list": "interactive_list",
    "contact": "contacts",
    "interactive_cta_url": "cta_url",
}

# Session message types whose reply the flow must wait for.  A list message
# stalls forever if the executor passes through instead of waiting, because
# the row reply then has no node to resume (#273).
AWAITS_REPLY_SESSION_MESSAGE_TYPES: Tuple[str, ...] = (
    "interactive_button",
    "interactive_list",
)

# =============================================================================
# API NODE CONSTANTS
# =============================================================================

# Valid HTTP methods for API node
VALID_HTTP_METHODS: Tuple[str, ...] = (
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
)

# Common response status categories for API node outputs
# These are the sourceHandle values for edges from API nodes
API_RESPONSE_HANDLES: Tuple[str, ...] = (
    "success",  # 2xx responses (200, 201, etc.)
    "client_error",  # 4xx responses (400, 401, 403, 404, etc.)
    "server_error",  # 5xx responses (500, 502, 503, etc.)
    "timeout",  # Request timeout
    "error",  # Network/connection errors
)

# Default headers for API requests
DEFAULT_API_HEADERS: dict = {
    "Content-Type": "application/json",
    "Accept": "application/json",
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


def canonical_session_message_type(message_type: str | None) -> str:
    """Resolve a node's ``message_type`` to its canonical spelling.

    A missing type means ``text``: that is what the executor has always
    defaulted to, and plenty of saved flows rely on it.
    """
    resolved = (message_type or "text").strip()
    return SESSION_MESSAGE_TYPE_ALIASES.get(resolved, resolved)


def is_valid_session_message_type(message_type: str | None) -> bool:
    """Check if a session message type can actually be sent."""
    return canonical_session_message_type(message_type) in SESSION_MESSAGE_TYPES


def session_message_awaits_reply(message_type: str | None) -> bool:
    """Check if a session message type expects a user reply before routing."""
    return canonical_session_message_type(message_type) in AWAITS_REPLY_SESSION_MESSAGE_TYPES
