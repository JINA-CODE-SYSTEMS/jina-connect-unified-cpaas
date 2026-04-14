"""
LangGraph Executor for ChatFlow.

This module builds and executes LangGraph StateGraphs from ChatFlow models.
It reads ChatFlowNode and ChatFlowEdge from the database and constructs
a graph that can be executed for each user session.

Key Concepts:
- Graph is built once per flow and cached (invalidated on flow update)
- Session state is managed in-memory (can be extended with persistent storage)
- thread_id = f"flow_{flow_id}_contact_{contact_id}" for session tracking

Usage:
    from chat_flow.services.graph_executor import ChatFlowExecutor

    executor = get_executor(flow)

    # Start a new session for a contact
    result = executor.start_session(contact_id=123)

    # Process a button click
    result = executor.process_input(contact_id=123, user_input="Get Started")

    # Get current session state
    state = executor.get_session_state(contact_id=123)
"""

import json
import logging
import uuid as _uuid
from typing import Any, Dict, List, Optional, TypedDict, Union

import requests as _requests
from django.conf import settings
from django.utils import timezone
from langgraph.graph import END, StateGraph

from ..models import ChatFlow, ChatFlowEdge, ChatFlowNode, UserChatFlowSession

logger = logging.getLogger(__name__)


# =============================================================================
# In-Memory Session Storage
# =============================================================================

# Simple in-memory storage for session states
# Key: thread_id (f"flow_{flow_id}_contact_{contact_id}")
# Value: FlowState dict
_session_store: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# State Schema
# =============================================================================


class FlowState(TypedDict):
    """
    State that flows through the LangGraph execution.

    This state is automatically persisted by the checkpointer.

    Attributes:
        flow_id: The ChatFlow being executed
        contact_id: The contact going through the flow
        current_node_id: The node_id of the current position
        user_input: The button text/input from user (if any)
        messages_sent: List of template IDs that have been sent
        context: Additional context data (variables, etc.)
        is_complete: Whether the flow has reached an end node
        awaiting_input: Whether we're waiting for user to respond (template sent, no reply yet)
        error: Any error message if execution failed
        _resume_target: When set, handlers skip (passthrough) until this node_id is reached
        _pending_user_input: Real button text stashed during replay; restored at _resume_target node
    """

    flow_id: int
    contact_id: int
    current_node_id: str
    user_input: Optional[str]
    messages_sent: List[int]
    context: Dict[str, Any]
    is_complete: bool
    awaiting_input: bool
    error: Optional[str]
    _resume_target: Optional[str]
    _pending_user_input: Optional[str]


# =============================================================================
# Template Sender (creates Broadcast entries and processes via broadcast pipeline)
# =============================================================================


def send_template_message(
    template_id: Union[int, str], contact_id: int, params: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Send a WhatsApp template message to a contact by creating a Broadcast entry.

    This creates a Broadcast with SCHEDULED status, which triggers the broadcast
    signal to automatically:
    - Schedule the Celery task
    - Deduct credits
    - Handle all platform-specific sending
    - Create Team Inbox entry

    Args:
        template_id: ID of the WATemplate to send
        contact_id: ID of the TenantContact to send to
        params: Optional template parameters (placeholder values)

    Returns:
        Dict with send result including broadcast_id, broadcast_message_id, etc.
    """
    from broadcast.models import (
        Broadcast,
        BroadcastMessage,
        BroadcastPlatformChoices,
        BroadcastStatusChoices,
        MessageStatusChoices,
    )
    from contacts.models import TenantContact
    from message_templates.models import TemplateNumber
    from wa.models import TemplateType, WATemplate

    result = {
        "success": False,
        "message_id": None,
        "template_id": template_id,
        "contact_id": contact_id,
        "broadcast_id": None,
        "broadcast_message_id": None,
        "status": "pending",
        "error": None,
    }

    try:
        # Load template and contact
        template = WATemplate.objects.select_related("wa_app", "wa_app__tenant").get(id=template_id)
        contact = TenantContact.objects.select_related("tenant").get(id=contact_id)

        if not template.wa_app:
            result["error"] = f"Template {template_id} has no associated WA app"
            logger.error(result["error"])
            return result

        # ── Ensure placeholder_mapping is populated ──────────────────
        # If the template was synced before placeholder_mapping support,
        # it may be null.  Without it, BroadcastMessage._wa_payload
        # skips _build_template_components() and WhatsApp receives no
        # component params → numbered placeholders show as literal text.
        if not template.placeholder_mapping:
            extracted = template._extract_placeholder_mapping()
            if extracted:
                template.placeholder_mapping = extracted
                template.save(update_fields=["placeholder_mapping"])
                logger.info(
                    f"send_template_message: Auto-populated placeholder_mapping for template {template_id}: {extracted}"
                )

        tenant = template.wa_app.tenant

        # ── Pre-send validation for CAROUSEL templates ───────────────
        # Each carousel card with headerType IMAGE/VIDEO MUST have a media
        # source (card_media M2M entry or example_media_url on the card
        # JSON).  Without it, the WhatsApp API rejects with #132012:
        # "header component parameter should not be empty".
        if template.template_type == TemplateType.CAROUSEL and template.cards:
            card_media_map = template.get_card_media_by_index()
            # Fallback: positional assignment from M2M
            if not card_media_map:
                all_cm = list(template.card_media.all().order_by("card_index", "created_at"))
                for idx, tm in enumerate(all_cm):
                    card_media_map[idx] = tm

            missing_cards = []
            for idx, card in enumerate(template.cards):
                header_type = (card.get("headerType") or "").upper()
                if header_type not in ("IMAGE", "VIDEO"):
                    continue
                has_tm = idx in card_media_map and card_media_map[idx].media
                has_example = bool(card.get("example_media_url"))
                has_override = False  # broadcasts created here won't have overrides
                if not has_tm and not has_example and not has_override:
                    missing_cards.append(idx)

            if missing_cards:
                result["error"] = (
                    f"Carousel template '{template.element_name}' is missing header "
                    f"media for card(s) {missing_cards}. Please re-sync the template "
                    f"or upload card images via the template editor."
                )
                result["status"] = "failed"
                logger.error(f"send_template_message: {result['error']} (template_id={template_id})")
                return result

        # Get or create TemplateNumber for this template
        template_number, _ = TemplateNumber.objects.get_or_create(
            gupshup_template=template, defaults={"name": template.element_name or template.name or ""}
        )

        # Create a Broadcast for this ChatFlow message with QUEUED status
        # Note: We create with DRAFT first, add recipients, then update to QUEUED
        # This ensures the signal fires after recipients are added
        broadcast = Broadcast.objects.create(
            tenant=tenant,
            name=f"ChatFlow: {template.element_name} to {contact.phone}",
            platform=BroadcastPlatformChoices.WHATSAPP,
            status=BroadcastStatusChoices.DRAFT,  # Start as DRAFT
            template_number=template_number,
            placeholder_data=params or {},
        )
        broadcast.recipients.add(contact)

        # Create BroadcastMessage BEFORE transitioning to QUEUED so the
        # record already exists when the Celery task (setup_broadcast_task)
        # runs BroadcastService.bulk_create with update_conflicts=True.
        broadcast_message, _ = BroadcastMessage.objects.get_or_create(
            broadcast=broadcast, contact=contact, defaults={"status": MessageStatusChoices.PENDING}
        )

        # Now update to QUEUED - this triggers the signal which schedules
        # the Celery task.  The task's bulk_create will upsert the
        # already-existing BroadcastMessage (no duplicate).
        broadcast.status = BroadcastStatusChoices.QUEUED
        broadcast.scheduled_time = broadcast._default_scheduled_time
        broadcast.save(update_fields=["status", "scheduled_time"])

        result["broadcast_id"] = broadcast.id
        result["broadcast_message_id"] = broadcast_message.id

        logger.info(
            f"Created ChatFlow broadcast {broadcast.id} for template "
            f"'{template.element_name}' to contact {contact.id} - signal will handle scheduling"
        )

        result["success"] = True
        result["status"] = "scheduled"

        logger.info(
            f"ChatFlow template scheduled for sending. "
            f"Broadcast: {broadcast.id}, BroadcastMessage: {broadcast_message.id}"
        )

    except WATemplate.DoesNotExist:
        result["error"] = f"Template {template_id} not found"
        result["status"] = "failed"
        logger.error(result["error"])
    except TenantContact.DoesNotExist:
        result["error"] = f"Contact {contact_id} not found"
        result["status"] = "failed"
        logger.error(result["error"])
    except Exception as e:
        result["error"] = f"Failed to send template: {str(e)}"
        result["status"] = "failed"
        logger.exception(result["error"])

    return result


# =============================================================================
# Session Persistence Helpers
# =============================================================================


def _make_json_safe(obj):
    """Recursively convert UUID (and other non-serializable types) to strings."""
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, _uuid.UUID):
        return str(obj)
    return obj


def save_session_to_db(state: FlowState) -> UserChatFlowSession:
    """
    Save or update the session state to UserChatFlowSession table.

    This is called after a template message is sent to persist the current
    position in the flow to the database.

    Args:
        state: Current FlowState with flow_id, contact_id, current_node_id, etc.

    Returns:
        The created or updated UserChatFlowSession instance
    """
    flow_id = state["flow_id"]
    contact_id = state["contact_id"]
    current_node_id = state["current_node_id"]
    is_complete = state.get("is_complete", False)

    # Store the entire state in context_data so we can fully restore on reload
    # This includes messages_sent, context, awaiting_input, error, etc.
    full_state_data = _make_json_safe(dict(state))

    # Deactivate any existing active sessions for this contact+flow
    UserChatFlowSession.objects.filter(contact_id=contact_id, flow_id=flow_id, is_active=True).exclude(
        current_node_id=current_node_id
    ).update(is_active=False, ended_at=timezone.now())

    # Resolve tenant from the flow for direct FK
    try:
        flow = ChatFlow.objects.only("tenant_id").get(pk=flow_id)
        tenant_id = flow.tenant_id
    except ChatFlow.DoesNotExist:
        tenant_id = None

    # Get or create the current session
    session, created = UserChatFlowSession.objects.update_or_create(
        contact_id=contact_id,
        flow_id=flow_id,
        is_active=True,
        defaults={
            "current_node_id": current_node_id,
            "is_complete": is_complete,
            "context_data": full_state_data,  # Store entire state for full restoration
            "ended_at": timezone.now() if is_complete else None,
            "tenant_id": tenant_id,
        },
    )

    if created:
        logger.info(
            f"Created new UserChatFlowSession {session.pk} for contact {contact_id} at node '{current_node_id}'"
        )
    else:
        logger.info(f"Updated UserChatFlowSession {session.pk} for contact {contact_id} to node '{current_node_id}'")

    return session


# =============================================================================
# Node Handlers
# =============================================================================


def create_start_node_handler(node: ChatFlowNode, edges: List[ChatFlowEdge]):
    """
    Create a handler function for a start node.

    Start nodes are ALWAYS passthrough - they act as a visual entry point only.
    They immediately route to the next connected node without sending any message
    or waiting for user input.

    The actual message sending (template or session message) happens at the
    subsequent nodes (template nodes, message nodes, etc.).

    This design:
    - Keeps the start node as a clean visual entry point in the flow editor
    - Separates flow entry from message logic
    - Makes flows easier to understand and maintain
    """
    node_id = node.node_id

    # Find the default next node (first outgoing edge)
    next_node_id = None
    for edge in edges:
        if edge.source_node.id == node.id:
            next_node_id = edge.target_node.node_id
            break

    def handler(state: FlowState) -> FlowState:
        """Execute start node - always passthrough to next node."""
        # ── Delay-resume skip: fast-forward without executing ──
        resume_target = state.get("_resume_target")
        if resume_target and resume_target != node_id:
            return {**state, "current_node_id": node_id, "user_input": "__PASSTHROUGH__"}

        existing_input = state.get("user_input")
        is_resuming = state.get("awaiting_input") and existing_input and existing_input != "__PASSTHROUGH__"

        if is_resuming:
            # Resuming a session with actual user input (e.g. button click).
            # Do NOT overwrite user_input — just pass through so the
            # template/message node and its router can use the real input.
            logger.info(
                f"Start node '{node_id}': Resuming session — preserving "
                f"user_input='{existing_input}', routing to {next_node_id}"
            )
            return {
                **state,
                "current_node_id": node_id,
                # Keep user_input and awaiting_input intact
            }

        logger.info(f"Start node '{node_id}': Passthrough mode -> routing to next node: {next_node_id}")

        # Set passthrough signal for router to immediately proceed to next node
        return {
            **state,
            "current_node_id": node_id,
            "user_input": "__PASSTHROUGH__",  # Special signal for router
            "awaiting_input": False,
        }

    return handler


def create_template_node_handler(node: ChatFlowNode, edges: List[ChatFlowEdge]):
    """
    Create a handler function for a template node.

    Template nodes have two execution modes:

    Mode 1: Interactive (has QUICK_REPLY buttons)
        Phase 1 (First visit - no user_input):
            - Send the template message to the contact
            - Save state to UserChatFlowSession
            - Set awaiting_input=True to signal waiting for user response
            - Graph execution pauses here (router returns END)

        Phase 2 (User replied - has user_input):
            - User has clicked a button
            - Don't re-send the template
            - Clear awaiting_input flag
            - Let the router determine the next node based on button clicked

    Mode 2: Passthrough (only PHONE_NUMBER, URL, or no buttons)
        - Send the template message
        - Immediately continue to next node (no waiting)
        - These button types don't trigger WhatsApp quick reply responses

    This returns a function that handles both modes.
    """
    template_id = node.template_id
    node_id = node.node_id
    node_data = node.node_data or {}

    # Determine if this template has interactive buttons (QUICK_REPLY)
    # that would trigger a user response we need to wait for
    template_buttons = node_data.get("buttons", [])
    has_quick_reply = any(btn.get("type") == "QUICK_REPLY" for btn in template_buttons)

    # Find the passthrough target (for non-interactive templates)
    passthrough_target = None
    if not has_quick_reply:
        for edge in edges:
            if edge.source_node.id == node.id:
                # Use 'bottom' handle edge or first available edge
                if edge.button_text in ("__PASSTHROUGH__", None, "") or not edge.button_text:
                    passthrough_target = edge.target_node.node_id
                    break
        # If no explicit passthrough, use first edge
        if not passthrough_target:
            for edge in edges:
                if edge.source_node.id == node.id:
                    passthrough_target = edge.target_node.node_id
                    break

    def handler(state: FlowState) -> FlowState:
        """Execute template node - send template and optionally wait for input."""
        # ── Resume skip: fast-forward without re-sending ──
        resume_target = state.get("_resume_target")
        if resume_target and resume_target != node_id:
            return {**state, "current_node_id": node_id, "user_input": "__PASSTHROUGH__"}
        if resume_target and resume_target == node_id:
            # Reached the target — clear the flag and restore any stashed button text
            pending = state.get("_pending_user_input")
            state = {**state, "_resume_target": None, "_pending_user_input": None}
            if pending:
                state["user_input"] = pending
                logger.info(f"Template node '{node_id}': Restored pending user input '{pending}'")

        user_input = state.get("user_input")
        current_node = state.get("current_node_id")
        awaiting = state.get("awaiting_input")

        # Mode 1: Interactive template - Phase 2 (User replied)
        # This is a resumption if:
        #   a) We're already AT this node (current_node == node_id), OR
        #   b) awaiting_input is True (session was waiting for input on this node
        #      but graph replay routed here via start node)
        # In either case, the template was already sent — don't re-send.
        is_resuming = (
            has_quick_reply and user_input and user_input != "__PASSTHROUGH__" and (current_node == node_id or awaiting)
        )

        if is_resuming:
            logger.info(
                f"Template node '{node_id}': Processing user input '{user_input}' "
                f"(current_node={current_node}, awaiting={awaiting})"
            )
            # Don't re-send template, just update state for router
            # The router will determine the next node based on user_input
            # IMPORTANT: Keep user_input for the router to use
            return {**state, "current_node_id": node_id, "awaiting_input": False}

        # First visit - send the template
        logger.info(f"Template node '{node_id}': Sending template {template_id} (interactive={has_quick_reply})")

        if template_id:
            # Merge session context with node-specific template variables.
            # node_data["variables"] may be:
            #   a) A flat dict  {"customer_name": "John"}  (user-configured),
            #   b) A nested placeholder_mapping  {"content": {"1": "first_name"}}
            #      (auto-saved by the FE from selectedTemplate.placeholder_mapping), or
            #   c) Empty / missing (legacy flows).
            #
            # For case (b), we flatten the mapping into named-var keys with
            # empty values so they DON'T override the contact's reserved vars
            # that the broadcast pipeline resolves automatically.
            # For case (c), we pass nothing — reserved vars resolution in
            # BroadcastMessage._build_template_components() handles it.
            raw_variables = node_data.get("variables") or {}

            # Detect nested placeholder_mapping format (has keys like 'content', 'header')
            _MAPPING_KEYS = {"content", "header", "footer", "buttons", "cards"}
            is_placeholder_mapping = bool(raw_variables and any(k in _MAPPING_KEYS for k in raw_variables))

            if is_placeholder_mapping:
                # Don't pass mapping values as placeholder_data — they would
                # override reserved vars.  The broadcast pipeline already
                # resolves named vars via _get_contact_reserved_vars().
                node_variables = {}
                logger.debug(
                    f"Template node '{node_id}': variables field is a "
                    f"placeholder_mapping, relying on reserved vars resolution"
                )
            else:
                node_variables = raw_variables

            template_params = {**state.get("context", {}), **node_variables}

            result = send_template_message(
                template_id=template_id, contact_id=state["contact_id"], params=template_params
            )

            # Track sent messages
            messages_sent = list(state.get("messages_sent", []))
            messages_sent.append(template_id)

            if has_quick_reply:
                # Mode 1: Interactive - wait for user response
                new_state = {
                    **state,
                    "current_node_id": node_id,
                    "messages_sent": messages_sent,
                    "user_input": None,  # Clear user_input for new node
                    "awaiting_input": True,  # Wait for user response
                    "error": None if result.get("success") else result.get("error"),
                }
            else:
                # Mode 2: Passthrough - continue immediately to next node
                logger.info(f"Template node '{node_id}': Non-interactive template, passthrough to next node")
                new_state = {
                    **state,
                    "current_node_id": node_id,
                    "messages_sent": messages_sent,
                    "user_input": "__PASSTHROUGH__",  # Signal router to continue
                    "awaiting_input": False,
                    "error": None if result.get("success") else result.get("error"),
                }
        else:
            # No template - passthrough
            new_state = {
                **state,
                "current_node_id": node_id,
                "user_input": "__PASSTHROUGH__" if not has_quick_reply else None,
                "awaiting_input": has_quick_reply,
            }

        # Save state to UserChatFlowSession database table
        save_session_to_db(new_state)

        return new_state

    return handler


def create_end_node_handler(node: ChatFlowNode):
    """
    Create a handler function for an end node.

    End nodes mark the completion of the flow and save the final state
    to the database. When the flow ends, the contact is also unassigned
    and a WebSocket event is broadcasted.
    """
    node_id = node.node_id
    flow_id = node.flow_id

    def handler(state: FlowState) -> FlowState:
        """Execute end node - mark flow as complete, unassign contact, and broadcast event."""
        logger.info(f"Executing end node '{node_id}' - flow complete")

        new_state = {
            **state,
            "current_node_id": node_id,
            "is_complete": True,
            "awaiting_input": False,
            "user_input": None,
        }

        # Save final state to database
        save_session_to_db(new_state)

        # Unassign the contact and broadcast event
        contact_id = state.get("contact_id")
        if contact_id:
            _unassign_contact_on_flow_end(contact_id, flow_id)

        return new_state

    return handler


def _unassign_contact_on_flow_end(contact_id: int, flow_id: int):
    """
    Unassign a contact when a ChatFlow ends and broadcast the event.

    This is called when the flow reaches an END node. It:
    1. Updates the contact's assignment to UNASSIGNED
    2. Creates an Event entry for the timeline
    3. Broadcasts via WebSocket

    Args:
        contact_id: ID of the TenantContact
        flow_id: ID of the ChatFlow that ended
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        from contacts.models import AssigneeTypeChoices, TenantContact
        from team_inbox.models import ActorTypeChoices, Event, EventTypeChoices, MessageEventIds

        contact = TenantContact.objects.filter(pk=contact_id).first()
        if not contact:
            logger.error(f"Contact {contact_id} not found for flow end unassignment")
            return

        # Only unassign if currently assigned to this ChatFlow
        if contact.assigned_to_type != AssigneeTypeChoices.CHATFLOW or contact.assigned_to_id != flow_id:
            logger.info(
                f"Contact {contact_id} not assigned to ChatFlow {flow_id}, "
                f"skipping unassignment (currently: {contact.assigned_to_type}/{contact.assigned_to_id})"
            )
            return

        # Get flow name for event note
        flow = ChatFlow.objects.filter(pk=flow_id).first()
        flow_name = flow.name if flow else f"ChatFlow #{flow_id}"

        # Skip signal to avoid duplicate events
        contact._skip_assignment_event = True

        # Update contact to unassigned
        contact.assigned_to_type = AssigneeTypeChoices.UNASSIGNED
        contact.assigned_to_id = None
        contact.assigned_to_user = None
        contact.assigned_by_type = AssigneeTypeChoices.CHATFLOW
        contact.assigned_by_id = flow_id
        contact.assigned_by_user = None
        contact.assignment_note = f"ChatFlow '{flow_name}' completed"
        contact.save()

        # Create MessageEventIds entry for timeline ordering
        event_id_entry = MessageEventIds.objects.create()

        # Create the unassignment Event entry
        event = Event.objects.create(
            event_id=event_id_entry,
            tenant=contact.tenant,
            contact=contact,
            event_type=EventTypeChoices.TICKET_UNASSIGNED,
            note=f"ChatFlow '{flow_name}' completed",
            icon="✅",
            color_background="#E8F5E9",
            color_text="#2E7D32",
            # Created by ChatFlow
            created_by_type=ActorTypeChoices.CHATFLOW,
            created_by_id=flow_id,
            created_by_user=None,
            # Unassigned by ChatFlow
            assigned_by_type=ActorTypeChoices.CHATFLOW,
            assigned_by_id=flow_id,
            assigned_by_user=None,
            # No new assignee (unassigned)
            assigned_to_type=None,
            assigned_to_id=None,
            assigned_to_user=None,
            event_data={
                "previous_assigned_to_type": AssigneeTypeChoices.CHATFLOW,
                "previous_assigned_to_id": flow_id,
                "chatflow_name": flow_name,
                "reason": "flow_completed",
            },
        )

        logger.info(
            f"Created flow-end unassignment event {event.pk} for contact {contact_id} "
            f"(ChatFlow '{flow_name}' completed)"
        )

        # Broadcast via WebSocket
        channel_layer = get_channel_layer()
        if channel_layer:
            room_group_name = f"team_inbox_{contact.tenant_id}"

            async_to_sync(channel_layer.group_send)(
                room_group_name,
                {
                    "type": "team_message",
                    "message": {
                        "type": "new_event",
                        "contact_id": contact.id,
                        "event": {
                            "id": event.pk,
                            "event_type": event.event_type,
                            "event_type_display": event.get_event_type_display(),
                            "note": event.note,
                            "created_by_name": event.created_by_name,
                            "assigned_by_name": event.assigned_by_name,
                            "assigned_to_name": None,
                            "assigned_to_type": None,
                            "assigned_to_id": None,
                            "icon": event.icon,
                            "color_background": event.color_background,
                            "color_text": event.color_text,
                            "event_data": event.event_data,
                            "created_at": event.created_at.isoformat() if event.created_at else None,
                        },
                    },
                },
            )
            logger.info(f"Broadcasted flow-end unassignment event for contact {contact.id}")

    except Exception as e:
        logger.exception(f"Error unassigning contact {contact_id} on flow end: {e}")


def create_api_call_node_handler(node: ChatFlowNode, edges: List[ChatFlowEdge]):
    """
    Create a handler function for an API Call node.

    API Call nodes make an HTTP request using the configuration stored in
    node_data (api_url, api_method, api_headers, api_body, api_params,
    api_timeout, api_retry_count).

    Execution:
    1. Substitute {{variable}} placeholders in url, headers, params, body
       using the flow context + contact fields.
    2. Make the HTTP request with retries.
    3. Store the full response body (as string or parsed JSON) in
       state["context"][variable_name] if variable_name is set.
    4. Route by setting user_input = "status-<code>"  which the edge
       router matches against button_text on the outgoing edges.

    DB edge format:  button_text = "status-200", "status-404", etc.
    """
    node_id = node.node_id
    flow_id = node.flow_id
    # Store the DB primary key so the handler can re-read fresh node_data
    _node_pk = node.pk

    def _read_node_config():
        """
        Read API config from the DB at execution time so that changes
        made after the graph was compiled are always picked up.
        """
        try:
            fresh_node = ChatFlowNode.objects.get(pk=_node_pk)
            nd = fresh_node.node_data or {}
        except ChatFlowNode.DoesNotExist:
            nd = node.node_data or {}

        cfg = {
            "api_url": nd.get("api_url", ""),
            "api_method": (nd.get("api_method") or "GET").upper(),
            "api_headers": nd.get("api_headers") or {},
            "api_params": nd.get("api_params") or {},
            "api_body": nd.get("api_body") or "",
            "api_body_type": nd.get("api_body_type", "json"),
            "api_timeout": int(nd.get("api_timeout") or 30),
            "api_retry_count": int(nd.get("api_retry_count") or 0),
            "api_response_codes": nd.get("api_response_codes") or [200],
        }

        # Response variable mappings
        raw_vars = nd.get("response_variables") or []
        if not raw_vars:
            legacy_vn = (nd.get("variable_name") or "").strip()
            if legacy_vn:
                raw_vars = [{"json_path": "", "variable_name": legacy_vn}]
        cfg["response_variable_mappings"] = [
            {
                "json_path": (m.get("json_path") or "").strip(),
                "variable_name": (m.get("variable_name") or "").strip(),
            }
            for m in raw_vars
            if (m.get("variable_name") or "").strip()
        ]
        return cfg

    def _substitute_vars(text: str, all_vars: dict) -> str:
        """Replace {{key}} placeholders in *text* with values from *all_vars*."""
        import re

        if not text:
            return text

        def _replacer(m):
            key = m.group(1).strip()
            return str(all_vars.get(key, m.group(0)))

        return re.sub(r"\{\{\s*(.+?)\s*\}\}", _replacer, text)

    def _build_vars(state: FlowState) -> dict:
        """Merge flow context + contact fields for placeholder substitution."""
        from contacts.models import TenantContact

        context = dict(state.get("context", {}))
        contact_id = state.get("contact_id")
        if contact_id:
            try:
                contact = TenantContact.objects.get(id=contact_id)
                context.setdefault("first_name", contact.first_name or "")
                context.setdefault("last_name", contact.last_name or "")
                context.setdefault("full_name", contact.full_name or "")
                context.setdefault("contact_name", contact.full_name or "")
                context.setdefault("phone", str(contact.phone) if contact.phone else "")
                context.setdefault("email", getattr(contact, "email", "") or "")
            except TenantContact.DoesNotExist:
                pass
        return context

    def handler(state: FlowState) -> FlowState:
        """Execute API call node — make HTTP request, store response, route by status code."""
        # ── Resume skip: fast-forward without re-calling ──
        resume_target = state.get("_resume_target")
        if resume_target and resume_target != node_id:
            return {**state, "current_node_id": node_id, "user_input": "__PASSTHROUGH__"}
        if resume_target and resume_target == node_id:
            pending = state.get("_pending_user_input")
            state = {**state, "_resume_target": None, "_pending_user_input": None}
            if pending:
                state["user_input"] = pending

        # ── Read config fresh from DB every execution ──
        cfg = _read_node_config()
        api_url = cfg["api_url"]
        api_method = cfg["api_method"]
        api_headers_raw = cfg["api_headers"]
        api_params_raw = cfg["api_params"]
        api_body_raw = cfg["api_body"]
        api_body_type = cfg["api_body_type"]
        api_timeout = cfg["api_timeout"]
        api_retry_count = cfg["api_retry_count"]
        configured_codes = cfg["api_response_codes"]
        response_variable_mappings = cfg["response_variable_mappings"]

        all_vars = _build_vars(state)

        # ── Substitute placeholders in URL, headers, params, body ──
        url = _substitute_vars(api_url, all_vars)
        headers = {k: _substitute_vars(v, all_vars) for k, v in api_headers_raw.items()}
        params = {k: _substitute_vars(v, all_vars) for k, v in api_params_raw.items()}
        body_str = _substitute_vars(api_body_raw, all_vars)

        logger.info(f"API node '{node_id}': {api_method} {url} (timeout={api_timeout}s, retries={api_retry_count})")

        # ── Build request kwargs ──
        req_kwargs: dict = {
            "method": api_method,
            "url": url,
            "headers": headers,
            "params": params,
            "timeout": api_timeout,
        }
        if api_method in ("POST", "PUT", "PATCH") and body_str:
            if api_body_type == "json":
                try:
                    req_kwargs["json"] = json.loads(body_str)
                except (json.JSONDecodeError, TypeError):
                    # Fall back to raw text if JSON parse fails
                    req_kwargs["data"] = body_str
                    logger.warning(f"API node '{node_id}': body is not valid JSON, sending as raw text")
            else:
                req_kwargs["data"] = body_str

        # ── Execute with retries ──
        response = None
        last_error = None
        attempts = 1 + max(0, api_retry_count)
        for attempt in range(1, attempts + 1):
            try:
                response = _requests.request(**req_kwargs)
                logger.info(
                    f"API node '{node_id}': Attempt {attempt}/{attempts} -> "
                    f"status {response.status_code}, length {len(response.text)}"
                )
                break  # Success — stop retrying
            except _requests.RequestException as exc:
                last_error = str(exc)
                logger.warning(f"API node '{node_id}': Attempt {attempt}/{attempts} failed: {exc}")

        # ── Determine status code & response body ──
        if response is not None:
            status_code = response.status_code
            try:
                response_body = response.json()
            except (ValueError, TypeError):
                response_body = response.text
        else:
            # All attempts failed — treat as 0 (network error)
            status_code = 0
            response_body = {"error": last_error or "Request failed"}

        # ── Extract & store response variables ──
        context = dict(state.get("context", {}))

        def _resolve_json_path(data, path: str):
            """Walk a dot-separated path into a dict/list, e.g. 'data.items.0.name'."""
            if not path:
                return data
            for segment in path.split("."):
                if isinstance(data, dict):
                    data = data.get(segment)
                elif isinstance(data, list):
                    try:
                        data = data[int(segment)]
                    except (ValueError, IndexError):
                        return None
                else:
                    return None
                if data is None:
                    return None
            return data

        for mapping in response_variable_mappings:
            json_path = mapping["json_path"]
            var_name = mapping["variable_name"]

            extracted = _resolve_json_path(response_body, json_path)

            # Store the raw extracted value
            context[var_name] = extracted

            # If extracted value is a dict, also flatten so {{var.field}} works
            if isinstance(extracted, dict):

                def _flatten(obj, prefix):
                    for k, v in obj.items():
                        full_key = f"{prefix}.{k}"
                        if isinstance(v, dict):
                            _flatten(v, full_key)
                        elif isinstance(v, list):
                            context[full_key] = json.dumps(v)
                        else:
                            context[full_key] = v

                _flatten(extracted, var_name)

            logger.info(
                f"API node '{node_id}': Stored context['{var_name}'] "
                f"(path='{json_path}', type={type(extracted).__name__})"
            )

        # Also store status code for potential condition checks
        context[f"_api_{node_id}_status"] = status_code

        # ── Check if status code has a matching edge ──
        route_key = f"status-{status_code}"

        if status_code not in configured_codes:
            # No matching edge — flow will END. Send error email to tenant.
            logger.error(
                f"API node '{node_id}': Status {status_code} not in "
                f"configured codes {configured_codes}. Flow will end. "
                f"Sending error notification to tenant."
            )
            _send_api_error_email(
                flow_id=flow_id,
                node_id=node_id,
                contact_id=state.get("contact_id"),
                api_url=url,
                api_method=api_method,
                status_code=status_code,
                response_preview=str(response_body)[:500],
                configured_codes=configured_codes,
            )

        logger.info(f"API node '{node_id}': Routing with '{route_key}'")

        new_state = {
            **state,
            "current_node_id": node_id,
            "context": context,
            "user_input": route_key,
        }

        save_session_to_db(new_state)
        return new_state

    return handler


def _send_api_error_email(
    flow_id: int,
    node_id: str,
    contact_id: int,
    api_url: str,
    api_method: str,
    status_code: int,
    response_preview: str,
    configured_codes: list,
):
    """Send an error email to the tenant's users when an API call returns
    a status code that has no matching edge in the flow."""
    try:
        from django.core.mail import send_mail

        from contacts.models import TenantContact

        flow = ChatFlow.objects.select_related("tenant").get(id=flow_id)
        tenant = flow.tenant

        # Resolve contact info for the email body
        contact_name = "Unknown"
        contact_phone = "N/A"
        try:
            contact = TenantContact.objects.get(id=contact_id)
            contact_name = contact.full_name or str(contact.phone)
            contact_phone = str(contact.phone)
        except TenantContact.DoesNotExist:
            pass

        # Get tenant user emails
        recipient_emails = [
            tu.user.email
            for tu in tenant.tenant_users.select_related("user").all()
            if tu.user.email and tu.user.is_active
        ]
        if not recipient_emails:
            logger.warning(f"API error email: No active users with email for tenant {tenant.pk}")
            return

        subject = f"⚠️ ChatFlow API Error — {flow.name or 'Unnamed Flow'}"

        plain_message = f"""ChatFlow API Call Failed

Flow: {flow.name or "Unnamed Flow"} (ID: {flow_id})
API Node: {node_id}
Contact: {contact_name} ({contact_phone})

Request: {api_method} {api_url}
Response Status: {status_code}
Configured Status Codes: {configured_codes}

The API returned status {status_code}, which has no matching route in the flow.
The flow has been stopped for this contact.

Response Preview:
{response_preview}

Please check the API endpoint or add a status code handler for {status_code} in the flow editor.

— Jina Connect
"""

        html_message = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #DC2626; color: white; padding: 16px 24px; border-radius: 8px 8px 0 0;">
                <h2 style="margin: 0;">⚠️ ChatFlow API Error</h2>
            </div>
            <div style="padding: 24px; background: #f9fafb; border: 1px solid #e5e7eb;">
                <p>An API call in your ChatFlow returned an unexpected status code.</p>
                <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
                    <tr><td style="padding: 8px; color: #6b7280;">Flow</td>
                        <td style="padding: 8px; font-weight: bold;">{flow.name or "Unnamed Flow"}</td></tr>
                    <tr style="background: white;"><td style="padding: 8px; color: #6b7280;">Contact</td>
                        <td style="padding: 8px;">{contact_name} ({contact_phone})</td></tr>
                    <tr><td style="padding: 8px; color: #6b7280;">Request</td>
                        <td style="padding: 8px; font-family: monospace;">{api_method} {api_url}</td></tr>
                    <tr style="background: white;"><td style="padding: 8px; color: #6b7280;">Status Code</td>
                        <td style="padding: 8px;"><span style="background: #FEE2E2; color: #991B1B; padding: 2px 8px; border-radius: 12px; font-weight: bold;">{status_code}</span></td></tr>
                    <tr><td style="padding: 8px; color: #6b7280;">Expected</td>
                        <td style="padding: 8px;">{", ".join(str(c) for c in configured_codes)}</td></tr>
                </table>
                <div style="background: #1F2937; color: #D1D5DB; padding: 12px; border-radius: 6px; font-family: monospace; font-size: 12px; max-height: 200px; overflow: auto; white-space: pre-wrap;">{response_preview}</div>
                <p style="margin-top: 16px; color: #6b7280; font-size: 13px;">The flow has been stopped for this contact. Please check the API endpoint or add a handler for status <strong>{status_code}</strong> in the flow editor.</p>
            </div>
            <div style="text-align: center; padding: 12px; color: #9CA3AF; font-size: 11px;">Jina Connect — {tenant.name}</div>
        </div>
        """

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@jinaconnect.com")

        send_mail(
            subject=subject,
            message=plain_message,
            from_email=from_email,
            recipient_list=recipient_emails,
            html_message=html_message,
            fail_silently=True,
        )
        logger.info(
            f"API error email sent to {len(recipient_emails)} users "
            f"for flow {flow_id}, node {node_id}, status {status_code}"
        )
    except Exception as e:
        logger.exception(f"Failed to send API error email: {e}")


def create_condition_node_handler(node: ChatFlowNode):
    """
    Create a handler function for a condition node.

    Condition nodes evaluate logic to determine the next path (IF TRUE / ELSE).

    Supports two formats:

    1. Grouped format (new):
       - node_data['condition_groups']: List of groups, each with:
           - logic: 'and' or 'or' — how rules WITHIN the group are combined
           - rules: List of condition rules (variable, operator, value)
       - node_data['outer_logic']: 'and' or 'or' — how GROUPS are combined
         e.g. (A AND B) OR (C) → two groups joined by outer_logic='or'

    2. Flat format (legacy, backward-compatible):
       - node_data['conditions']: Flat list of rules
       - node_data['condition_logic']: 'and' or 'or' for all rules

    The handler sets user_input to "IF TRUE" or "ELSE" which the router
    uses to select the correct outgoing edge.
    """
    node_id = node.node_id
    node_data = node.node_data or {}

    # Read grouped format first, fall back to flat
    condition_groups = node_data.get("condition_groups", [])
    outer_logic = node_data.get("outer_logic", "or")

    # Legacy fallback: wrap flat conditions into a single group
    if not condition_groups:
        flat_conditions = node_data.get("conditions", [])
        flat_logic = node_data.get("condition_logic", "and")
        if flat_conditions:
            condition_groups = [{"logic": flat_logic, "rules": flat_conditions}]

    def _resolve_variable(state: FlowState, variable: str) -> str:
        """Resolve a variable name to its runtime value."""
        from contacts.models import TenantContact

        # Check user's last message first
        if variable == "last_message":
            return str(state.get("user_input") or "")

        # Check context dict (may have been set by previous nodes)
        context = state.get("context", {})
        if variable in context:
            return str(context[variable])

        # Resolve from contact model
        contact_id = state.get("contact_id")
        if contact_id:
            try:
                contact = TenantContact.objects.get(id=contact_id)
                field_map = {
                    "contact_name": lambda c: c.full_name,
                    "first_name": lambda c: c.first_name or "",
                    "last_name": lambda c: c.last_name or "",
                    "phone": lambda c: str(c.phone) if c.phone else "",
                    "email": lambda c: getattr(c, "email", "") or "",
                    "tag": lambda c: c.tag or "",
                    "status": lambda c: c.status or "",
                    "assigned_team": lambda c: str(c.assigned_to_id) if c.assigned_to_type == "TEAM" else "",
                }
                resolver = field_map.get(variable)
                if resolver:
                    return resolver(contact)
            except TenantContact.DoesNotExist:
                logger.warning(f"Condition node '{node_id}': contact {contact_id} not found")

        return ""

    def _evaluate_condition(actual: str, operator: str, expected: str) -> bool:
        """Evaluate a single condition rule."""
        actual_lower = actual.lower().strip()
        expected_lower = expected.lower().strip() if expected else ""

        if operator == "equals":
            return actual_lower == expected_lower
        elif operator == "not_equals":
            return actual_lower != expected_lower
        elif operator == "contains":
            return expected_lower in actual_lower
        elif operator == "not_contains":
            return expected_lower not in actual_lower
        elif operator == "starts_with":
            return actual_lower.startswith(expected_lower)
        elif operator == "ends_with":
            return actual_lower.endswith(expected_lower)
        elif operator == "greater_than":
            try:
                return float(actual) > float(expected)
            except (ValueError, TypeError):
                return actual_lower > expected_lower
        elif operator == "less_than":
            try:
                return float(actual) < float(expected)
            except (ValueError, TypeError):
                return actual_lower < expected_lower
        elif operator == "is_empty":
            return not actual.strip()
        elif operator == "is_not_empty":
            return bool(actual.strip())
        else:
            logger.warning(f"Condition node '{node_id}': unknown operator '{operator}'")
            return False

    def _evaluate_group(state: FlowState, group: dict) -> bool:
        """Evaluate all rules in a single group using the group's inner logic."""
        rules = group.get("rules", [])
        inner_logic = group.get("logic", "and")

        if not rules:
            return True  # empty group is vacuously true

        results = []
        for rule in rules:
            variable = rule.get("variable", "")
            operator = rule.get("operator", "equals")
            value = rule.get("value", "")

            actual = _resolve_variable(state, variable)
            result = _evaluate_condition(actual, operator, value)
            results.append(result)

            logger.debug(f"Condition node '{node_id}': {variable}='{actual}' {operator} '{value}' → {result}")

        if inner_logic == "or":
            return any(results)
        else:
            return all(results)

    def handler(state: FlowState) -> FlowState:
        """Execute condition node — evaluate grouped conditions and route."""
        # ── Resume skip: fast-forward without evaluating ──
        resume_target = state.get("_resume_target")
        if resume_target and resume_target != node_id:
            return {**state, "current_node_id": node_id, "user_input": "__PASSTHROUGH__"}
        if resume_target and resume_target == node_id:
            pending = state.get("_pending_user_input")
            state = {**state, "_resume_target": None, "_pending_user_input": None}
            if pending:
                state["user_input"] = pending

        total_rules = sum(len(g.get("rules", [])) for g in condition_groups)
        logger.info(
            f"Executing condition node '{node_id}' with {len(condition_groups)} group(s), {total_rules} rule(s)"
        )

        if not condition_groups:
            # No conditions configured — take ELSE (default) path
            logger.info(f"Condition node '{node_id}': no conditions configured, taking ELSE path")
            return {
                **state,
                "current_node_id": node_id,
                "user_input": "ELSE",
            }

        # Evaluate each group, then combine with outer logic
        group_results = []
        for idx, group in enumerate(condition_groups):
            result = _evaluate_group(state, group)
            group_results.append(result)
            logger.debug(
                f"Condition node '{node_id}': group {idx + 1} (inner_logic={group.get('logic', 'and')}) → {result}"
            )

        if outer_logic == "and":
            overall = all(group_results)
        else:
            overall = any(group_results)

        branch = "IF TRUE" if overall else "ELSE"
        logger.info(
            f"Condition node '{node_id}': "
            f"outer_logic={outer_logic}, group_results={group_results}, "
            f"overall={overall} → {branch}"
        )

        return {
            **state,
            "current_node_id": node_id,
            "user_input": branch,
        }

    return handler


def create_delay_node_handler(node: ChatFlowNode, edges: List[ChatFlowEdge]):
    """
    Create a handler function for a delay node.

    Delay nodes pause the flow execution for a specified duration before
    continuing to the next node. Uses Celery for async scheduling.

    The delay configuration is stored in node_data (matching the frontend
    ``buildFlowPayload`` contract):
    - node_data['delay_duration']: Numeric amount (e.g. 5)
    - node_data['delay_unit']:     Unit string — 'seconds', 'minutes', or 'hours'

    After the delay, the Celery task ``continue_flow_after_delay`` resumes
    the graph from ``next_node_id``.

    IMPORTANT: The handler sets ``user_input = None`` so the conditional
    router returns ``END`` and the graph **pauses**.  The Celery task is
    responsible for resuming execution once the timer fires.
    """
    node_id = node.node_id
    node_data = node.node_data or {}

    # Find the default next node (first outgoing edge)
    next_node_id = None
    for edge in edges:
        if edge.source_node.id == node.id:
            next_node_id = edge.target_node.node_id
            break

    # ── Pre-compute delay in seconds from delay_duration + delay_unit ──
    _UNIT_MULTIPLIERS = {"seconds": 1, "minutes": 60, "hours": 3600}

    raw_duration = node_data.get("delay_duration")
    delay_unit = node_data.get("delay_unit", "seconds")

    try:
        duration_value = float(raw_duration) if raw_duration is not None else 0
    except (TypeError, ValueError):
        duration_value = 0

    multiplier = _UNIT_MULTIPLIERS.get(delay_unit, 1)
    total_delay_seconds = int(duration_value * multiplier)

    # Fallback: 60 s when nothing was configured
    if total_delay_seconds <= 0:
        total_delay_seconds = 60
        logger.warning(
            f"Delay node '{node_id}' has no valid delay_duration/delay_unit "
            f"(got duration={raw_duration!r}, unit={delay_unit!r}), "
            f"defaulting to 60 seconds"
        )

    def handler(state: FlowState) -> FlowState:
        """Execute delay node — record delay intent and pause the graph.

        IMPORTANT: We do NOT call Celery here.  If Celery is in eager mode
        (CELERY_ALWAYS_EAGER=True), apply_async runs synchronously inside
        graph.invoke(), which triggers a nested graph.invoke() and sends
        the post-delay message immediately (zero delay).

        Instead we store the scheduling info in ``delay_info`` so the
        caller (start_session / process_input) can schedule the Celery
        task AFTER graph.invoke() returns.
        """
        # ── Delay-resume skip: fast-forward without re-scheduling ──
        resume_target = state.get("_resume_target")
        if resume_target and resume_target != node_id:
            return {**state, "current_node_id": node_id, "user_input": "__PASSTHROUGH__"}

        logger.info(f"Delay node '{node_id}': Pausing graph, delay={total_delay_seconds}s, next_node='{next_node_id}'")

        # Set user_input = None so the conditional router returns END and
        # graph execution stops here.  delay_info is a non-TypedDict key
        # so LangGraph will strip it from state — that's fine because the
        # caller reads it from the DB via save_session_to_db.
        new_state = {
            **state,
            "current_node_id": node_id,
            "awaiting_input": False,  # Not waiting for user, waiting for timer
            "user_input": None,  # ← causes router to return END (pause)
            "delay_info": {
                "delay_seconds": total_delay_seconds,
                "next_node_id": next_node_id,
                "scheduled": False,  # Will be scheduled after graph.invoke()
            },
        }

        # Save state (includes delay_info) for recovery and post-invoke scheduling
        save_session_to_db(new_state)

        return new_state

    return handler


def create_message_node_handler(node: ChatFlowNode, edges: List[ChatFlowEdge]):
    """
    Create a handler function for a message node (session message).

    Message nodes send free-form text/media messages (not templates).
    These are only allowed within the 24-hour session window.

    Execution modes (mirrors the template handler pattern):

    Mode 1: Interactive (has QUICK_REPLY buttons)
        Phase 1 (First visit — no user_input):
            - Send the session message to the contact
            - Set awaiting_input=True, user_input=None → router returns END
            - Graph pauses, waiting for user button click
        Phase 2 (User replied — has user_input):
            - Don't re-send the message
            - Clear awaiting_input, let the router route by button text

    Mode 2: Passthrough (no QUICK_REPLY buttons)
        - Send the session message
        - Set user_input="__PASSTHROUGH__" → router continues immediately

    The message content is stored in node_data:
    - node_data['message_type']: 'text', 'image', 'video', etc.
    - node_data['message_content']: The message content (text body, media id, etc.)
    - node_data['buttons']: Optional list of buttons (QUICK_REPLY, URL, etc.)
    """
    node_id = node.node_id
    node_data = node.node_data or {}

    # ── Detect interactive buttons (QUICK_REPLY) ──
    message_buttons = node_data.get("buttons", []) or []
    has_quick_reply = any(btn.get("type") == "QUICK_REPLY" for btn in message_buttons)

    # Find the passthrough target (for non-interactive messages)
    passthrough_target = None
    if not has_quick_reply:
        for edge in edges:
            if edge.source_node.id == node.id:
                if edge.button_text in ("__PASSTHROUGH__", None, "") or not edge.button_text:
                    passthrough_target = edge.target_node.node_id
                    break
        if not passthrough_target:
            for edge in edges:
                if edge.source_node.id == node.id:
                    passthrough_target = edge.target_node.node_id
                    break

    def handler(state: FlowState) -> FlowState:
        """Execute message node - send session message, optionally wait for input."""
        # ── Resume skip: fast-forward without re-sending ──
        resume_target = state.get("_resume_target")
        if resume_target and resume_target != node_id:
            return {**state, "current_node_id": node_id, "user_input": "__PASSTHROUGH__"}
        if resume_target and resume_target == node_id:
            # Reached the target — clear the flag and restore any stashed button text
            pending = state.get("_pending_user_input")
            state = {**state, "_resume_target": None, "_pending_user_input": None}
            if pending:
                state["user_input"] = pending
                logger.info(f"Message node '{node_id}': Restored pending user input '{pending}'")

        user_input = state.get("user_input")
        current_node = state.get("current_node_id")
        awaiting = state.get("awaiting_input")

        # ── Mode 1 Phase 2: Resumption (user clicked a button) ──
        is_resuming = (
            has_quick_reply and user_input and user_input != "__PASSTHROUGH__" and (current_node == node_id or awaiting)
        )

        if is_resuming:
            logger.info(
                f"Message node '{node_id}': Processing user input '{user_input}' "
                f"(current_node={current_node}, awaiting={awaiting})"
            )
            # Message was already sent — don't re-send, just let the router decide
            return {**state, "current_node_id": node_id, "awaiting_input": False}

        # ── First visit — send the session message ──
        logger.info(f"Message node '{node_id}': Sending session message")

        contact_id = state["contact_id"]

        result = send_session_message(contact_id=contact_id, node_data=node_data, context=state.get("context", {}))

        if result.get("success"):
            logger.info(
                f"Message node '{node_id}': Session message sent successfully "
                f"(outgoing_message_id={result.get('outgoing_message_id')})"
            )
        else:
            logger.error(f"Message node '{node_id}': Failed to send session message: {result.get('error')}")

        # Track sent messages
        messages_sent = list(state.get("messages_sent", []))
        if result.get("outgoing_message_id"):
            messages_sent.append(f"session:{result.get('outgoing_message_id')}")

        if has_quick_reply:
            # Mode 1 Phase 1: Interactive — wait for user button click
            logger.info(f"Message node '{node_id}': Has QUICK_REPLY buttons, waiting for user input")
            new_state = {
                **state,
                "current_node_id": node_id,
                "messages_sent": messages_sent,
                "user_input": None,  # ← router returns END (pause)
                "awaiting_input": True,  # ← signals we're waiting
                "error": None if result.get("success") else result.get("error"),
            }
        else:
            # Mode 2: Passthrough — continue immediately
            new_state = {
                **state,
                "current_node_id": node_id,
                "messages_sent": messages_sent,
                "user_input": "__PASSTHROUGH__",  # Signal router to continue
                "awaiting_input": False,
                "error": None if result.get("success") else result.get("error"),
            }

        # Save state (in case of crash, we know where we were)
        save_session_to_db(new_state)

        return new_state

    return handler


def send_session_message(
    contact_id: int, node_data: Dict[str, Any], context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Send a WhatsApp session message (non-template) to a contact (BSP-aware).

    Creates a WAMessage (OUTBOUND, PENDING) which triggers the ``post_save``
    signal → ``send_wa_message`` Celery task → BSP-aware ``send_outgoing_message``.

    Session messages are only allowed within the 24-hour customer-care window
    (i.e. after the contact last messaged us).

    Args:
        contact_id: ID of the TenantContact to send to
        node_data: Node configuration containing message_type and message_content
        context: Optional context for variable substitution

    Returns:
        Dict with send result including outgoing_message_id, status, etc.
    """
    from contacts.models import TenantContact
    from wa.utility.data_model.gupshup.session_message_base import (
        ButtonReply,
        InteractiveBody,
        InteractiveButton,
        InteractiveButtonAction,
        InteractiveButtonContent,
        InteractiveButtonMessage,
        InteractiveFooter,
        InteractiveHeader,
        InteractiveOrderDetailsContent,
        InteractiveOrderDetailsMessage,
        OrderDetailsAction,
        OrderDetailsParameters,
        OrderSession,
        TextMessage,
        TextMessageInput,
    )
    from wa.utility.data_model.shared.order_models import OrderAmount, OrderItem, PaymentSettings

    result = {
        "success": False,
        "outgoing_message_id": None,
        "contact_id": contact_id,
        "status": "pending",
        "error": None,
    }

    try:
        # Load contact with tenant
        contact = TenantContact.objects.select_related("tenant").get(id=contact_id)

        platform = str((context or {}).get("platform", "WHATSAPP")).upper()

        # ── Telegram branch ─────────────────────────────────────────────────
        if platform == "TELEGRAM":
            from telegram.models import TelegramBotApp
            from telegram.services.message_sender import TelegramMessageSender

            chat_id = contact.telegram_chat_id
            if not chat_id:
                result["error"] = f"Contact {contact_id} has no telegram_chat_id"
                result["status"] = "failed"
                return result

            bot_app = TelegramBotApp.objects.filter(tenant=contact.tenant, is_active=True).first()
            if not bot_app:
                result["error"] = "No active Telegram bot found for tenant"
                result["status"] = "failed"
                return result

            sender = TelegramMessageSender(bot_app)

            message_content = node_data.get("message_content", "")
            body_text = node_data.get("body", "") or message_content
            buttons = node_data.get("buttons", [])
            has_quick_reply = any(
                (b.get("type", "QUICK_REPLY") or "").upper() in ("QUICK_REPLY", "QUICK-REPLY") for b in buttons
            )

            if has_quick_reply:
                tg_buttons = []
                for b in buttons:
                    b_type = (b.get("type", "QUICK_REPLY") or "").upper()
                    if b_type not in ("QUICK_REPLY", "QUICK-REPLY"):
                        continue
                    label = b.get("title") or b.get("text") or "Option"
                    value = b.get("id") or label
                    tg_buttons.append([{"text": label, "callback_data": str(value)[:64]}])

                send_result = sender.send_keyboard(
                    chat_id=str(chat_id),
                    text=body_text or "Please choose:",
                    keyboard=tg_buttons,
                    contact=contact,
                )
            else:
                send_result = sender.send_text(
                    chat_id=str(chat_id),
                    text=body_text or message_content,
                    contact=contact,
                )

            if send_result.get("success"):
                result["success"] = True
                result["status"] = "queued"
                result["outgoing_message_id"] = send_result.get("message_id")
            else:
                result["status"] = "failed"
                result["error"] = send_result.get("error") or "Failed to send Telegram session message"

            return result

        # ── SMS branch ──────────────────────────────────────────────────────
        if platform == "SMS":
            from sms.models import SMSApp
            from sms.services.message_sender import SMSMessageSender

            sms_app = SMSApp.objects.filter(tenant=contact.tenant, is_active=True).first()
            if not sms_app:
                result["error"] = "No active SMS app found for tenant"
                result["status"] = "failed"
                return result

            sender = SMSMessageSender(sms_app)

            message_content = node_data.get("message_content", "")
            body_text = node_data.get("body", "") or message_content
            buttons = node_data.get("buttons", [])
            has_quick_reply = any(
                (b.get("type", "QUICK_REPLY") or "").upper() in ("QUICK_REPLY", "QUICK-REPLY") for b in buttons
            )

            if has_quick_reply:
                send_result = sender.send_keyboard(
                    chat_id=str(contact.phone),
                    text=body_text or "Please choose:",
                    keyboard=buttons,
                    contact=contact,
                )
            else:
                send_result = sender.send_text(
                    chat_id=str(contact.phone),
                    text=body_text or message_content,
                    contact=contact,
                )

            if send_result.get("success"):
                result["success"] = True
                result["status"] = "queued"
                result["outgoing_message_id"] = send_result.get("message_id")
            else:
                result["status"] = "failed"
                result["error"] = send_result.get("error") or "Failed to send SMS session message"

            return result

        # Get the WA app for this tenant (first active app)
        from tenants.models import TenantWAApp
        from wa.models import MessageDirection, MessageStatus, MessageType, WAMessage

        wa_app = TenantWAApp.objects.select_related("waba_info").filter(tenant=contact.tenant, is_active=True).first()

        if not wa_app:
            result["error"] = "No active WA app found for tenant"
            logger.error(result["error"])
            return result

        # Extract message configuration from node_data
        message_type = node_data.get("message_type", "text")
        message_content = node_data.get("message_content", "")
        body_text = node_data.get("body", "") or message_content

        # ── Build contact-level reserved variables ──
        # These mirror what the broadcast pipeline resolves via
        # BroadcastMessage._get_contact_reserved_vars() so that
        # placeholders like {{first_name}}, {{contact_name}} work
        # in session messages, not just in template broadcasts.
        contact_vars = {}
        contact_name = (contact.full_name or "").strip() if hasattr(contact, "full_name") else ""
        if not contact_name:
            contact_name = "Customer"
        contact_vars.update(
            {
                "first_name": getattr(contact, "first_name", "") or "",
                "last_name": getattr(contact, "last_name", "") or "",
                "full_name": contact_name,
                "contact_name": contact_name,
                "name": contact_name,
                "phone": str(getattr(contact, "phone", "")),
                "email": getattr(contact, "email", "") or "",
            }
        )
        # Tenant/company variables
        tenant = getattr(contact, "tenant", None)
        if tenant:
            contact_vars["company_name"] = getattr(tenant, "name", "") or "Company"
            contact_vars["tenant_name"] = contact_vars["company_name"]

        # Merge: contact vars first, then explicit context (context wins)
        all_vars = {**contact_vars, **(context or {})}

        # Apply variable substitution to message fields
        if isinstance(message_content, str):
            for key, value in all_vars.items():
                message_content = message_content.replace(f"{{{{{key}}}}}", str(value))
        if isinstance(body_text, str):
            for key, value in all_vars.items():
                body_text = body_text.replace(f"{{{{{key}}}}}", str(value))
        # Also substitute header/footer
        header_raw = node_data.get("header", "")
        footer_raw = node_data.get("footer", "")
        if header_raw and isinstance(header_raw, str):
            for key, value in all_vars.items():
                header_raw = header_raw.replace(f"{{{{{key}}}}}", str(value))
        if footer_raw and isinstance(footer_raw, str):
            for key, value in all_vars.items():
                footer_raw = footer_raw.replace(f"{{{{{key}}}}}", str(value))

        # Build the Cloud API payload (works for both Gupshup and META)
        recipient = str(contact.phone)
        if recipient.startswith("+"):
            recipient = recipient[1:]  # Remove leading +

        is_interactive = message_type in ("interactive_button", "button")
        node_buttons = node_data.get("buttons", []) if is_interactive else []
        # Only QUICK_REPLY buttons are sent as interactive buttons
        quick_reply_buttons = [
            b for b in node_buttons if (b.get("type", "QUICK_REPLY")).upper() in ("QUICK_REPLY", "QUICK-REPLY")
        ]

        if is_interactive and quick_reply_buttons:
            # Build interactive button message
            buttons = []
            for btn in quick_reply_buttons[:3]:  # WhatsApp max 3 buttons
                btn_id = btn.get("id", btn.get("text", "btn"))[:256]
                btn_title = (btn.get("title") or btn.get("text") or "Button")[:20]
                buttons.append(InteractiveButton(reply=ButtonReply(id=btn_id, title=btn_title)))

            interactive_content = InteractiveButtonContent(
                body=InteractiveBody(text=body_text or message_content or "Please choose:"),
                action=InteractiveButtonAction(buttons=buttons),
            )
            # Optional header
            if header_raw and isinstance(header_raw, str) and header_raw.strip():
                from wa.utility.data_model.gupshup.session_message_base import InteractiveHeader

                interactive_content.header = InteractiveHeader(type="text", text=header_raw.strip()[:60])
            # Optional footer
            if footer_raw and isinstance(footer_raw, str) and footer_raw.strip():
                interactive_content.footer = InteractiveFooter(text=footer_raw.strip()[:60])

            message = InteractiveButtonMessage(
                to=recipient,
                interactive=interactive_content,
            )
            payload = message.model_dump(by_alias=True, exclude_none=True)
            wa_message_type = MessageType.INTERACTIVE
            stored_text = body_text or message_content

            logger.info(f"Session message: interactive_button with {len(buttons)} buttons for contact {contact_id}")

        elif message_type == "order_details":
            # ── Interactive order_details message ──
            # node_data must contain an 'order_details' dict with the full
            # order structure (reference_id, items, payment_settings, etc.).
            od = node_data.get("order_details", {})

            # Variable substitution on reference_id
            ref_id = str(od.get("reference_id", ""))
            for key, value in all_vars.items():
                ref_id = ref_id.replace(f"{{{{{key}}}}}", str(value))

            # Build items
            raw_items = od.get("order", {}).get("items", [])
            items = []
            for ri in raw_items:
                item_kwargs: Dict[str, Any] = {
                    "name": ri.get("name", "Item"),
                    "amount": OrderAmount(**ri["amount"])
                    if isinstance(ri.get("amount"), dict)
                    else OrderAmount(value=0),
                    "quantity": ri.get("quantity", 1),
                }
                if ri.get("retailer_id"):
                    item_kwargs["retailer_id"] = ri["retailer_id"]
                if ri.get("sale_amount") and isinstance(ri["sale_amount"], dict):
                    item_kwargs["sale_amount"] = OrderAmount(**ri["sale_amount"])
                if ri.get("image") and isinstance(ri["image"], dict):
                    item_kwargs["image"] = ri["image"]
                items.append(OrderItem(**item_kwargs))

            # Build order session
            order_kwargs: Dict[str, Any] = {
                "items": items,
                "subtotal": OrderAmount(**od["order"]["subtotal"])
                if isinstance(od.get("order", {}).get("subtotal"), dict)
                else OrderAmount(value=0),
                "tax": OrderAmount(**od["order"]["tax"])
                if isinstance(od.get("order", {}).get("tax"), dict)
                else OrderAmount(value=0),
            }
            if od.get("order", {}).get("shipping") and isinstance(od["order"]["shipping"], dict):
                order_kwargs["shipping"] = OrderAmount(**od["order"]["shipping"])
            if od.get("order", {}).get("discount") and isinstance(od["order"]["discount"], dict):
                order_kwargs["discount"] = OrderAmount(**od["order"]["discount"])
            if od.get("order", {}).get("catalog_id"):
                order_kwargs["catalog_id"] = od["order"]["catalog_id"]

            # Payment settings
            raw_ps = od.get("payment_settings", [])
            payment_settings = [PaymentSettings(**ps) if isinstance(ps, dict) else ps for ps in raw_ps]

            parameters = OrderDetailsParameters(
                reference_id=ref_id,
                type=od.get("type", "digital-goods"),
                currency=od.get("currency", "INR"),
                total_amount=OrderAmount(**od["total_amount"])
                if isinstance(od.get("total_amount"), dict)
                else OrderAmount(value=0),
                payment_settings=payment_settings,
                order=OrderSession(**order_kwargs),
            )

            interactive_content = InteractiveOrderDetailsContent(
                body=InteractiveBody(text=body_text or "Your order is ready for payment"),
                action=OrderDetailsAction(parameters=parameters),
            )
            # Optional header
            if header_raw and isinstance(header_raw, str) and header_raw.strip():
                interactive_content.header = InteractiveHeader(type="text", text=header_raw.strip()[:60])
            # Optional footer
            if footer_raw and isinstance(footer_raw, str) and footer_raw.strip():
                interactive_content.footer = InteractiveFooter(text=footer_raw.strip()[:60])

            message = InteractiveOrderDetailsMessage(
                to=recipient,
                interactive=interactive_content,
            )
            payload = message.model_dump(by_alias=True, exclude_none=True)
            wa_message_type = MessageType.INTERACTIVE
            stored_text = body_text or "Order details"

            logger.info(f"Session message: order_details ref={ref_id} items={len(items)} for contact {contact_id}")

        elif message_type in ("image", "video", "document", "audio"):
            # ── Media message (image / video / document / audio) ──
            # message_content holds the media URL (e.g. GCS signed URL).
            # body_text (from node_data['body']) is the optional caption.
            # WhatsApp Cloud API accepts: {"type":"image", "image":{"link":"…","caption":"…"}}
            media_url = message_content or ""
            caption = body_text if body_text and body_text != media_url else None

            # Map node message_type → Cloud API type key + MessageType enum
            _MEDIA_TYPE_MAP = {
                "image": (MessageType.IMAGE, "image"),
                "video": (MessageType.VIDEO, "video"),
                "document": (MessageType.DOCUMENT, "document"),
                "audio": (MessageType.AUDIO, "audio"),
            }
            wa_message_type, api_type_key = _MEDIA_TYPE_MAP[message_type]

            media_obj: Dict[str, Any] = {"link": media_url}
            # caption is supported on image, video, document — not audio
            if caption and message_type != "audio":
                media_obj["caption"] = caption

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": api_type_key,
                api_type_key: media_obj,
            }
            stored_text = caption or media_url

            logger.info(f"Session message: {message_type} for contact {contact_id}, url={media_url[:80]}...")

        elif message_type == "text" or not is_interactive:
            message = TextMessage(to=recipient, text=TextMessageInput(body=message_content))
            payload = message.model_dump(by_alias=True, exclude_none=True)
            wa_message_type = MessageType.TEXT
            stored_text = message_content
        else:
            # Interactive type but no quick-reply buttons → send as plain text
            message = TextMessage(to=recipient, text=TextMessageInput(body=body_text or message_content))
            payload = message.model_dump(by_alias=True, exclude_none=True)
            wa_message_type = MessageType.TEXT
            stored_text = body_text or message_content

        # Create WAMessage entry — post_save signal queues the Celery send task
        create_kwargs = dict(
            wa_app=wa_app,
            contact=contact,
            direction=MessageDirection.OUTBOUND,
            message_type=wa_message_type,
            status=MessageStatus.PENDING,
            text=stored_text,
            raw_payload=payload,
        )
        # Persist media_url for media messages so it shows in team-inbox / logs.
        # WAMessage.media_url is URLField(max_length=200) by default;
        # GCS signed URLs are much longer, so only store if it fits.
        if message_type in ("image", "video", "document", "audio") and message_content:
            if len(message_content) <= 200:
                create_kwargs["media_url"] = message_content

        outgoing_message = WAMessage.objects.create(**create_kwargs)

        result["outgoing_message_id"] = str(outgoing_message.id)

        logger.info(
            f"Created ChatFlow session message {outgoing_message.id} "
            f"for contact {contact.id} via {getattr(wa_app, 'bsp', 'unknown')} BSP"
        )

        # The WAMessage post_save signal automatically fires send_wa_message task
        result["success"] = True
        result["status"] = "queued"

    except TenantContact.DoesNotExist:
        result["error"] = f"Contact {contact_id} not found"
        result["status"] = "failed"
        logger.error(result["error"])
    except Exception as e:
        result["error"] = f"Failed to send session message: {str(e)}"
        result["status"] = "failed"
        logger.exception(result["error"])

    return result


# =============================================================================
# Router Functions
# =============================================================================


def create_button_router(node: ChatFlowNode, edges: List[ChatFlowEdge]):
    """
    Create a routing function based on button clicks.

    This function examines the user_input in state and routes to the
    appropriate next node based on which button was clicked.

    Special handling for passthrough nodes (start, message, non-interactive templates):
    - If user_input is "__PASSTHROUGH__", routes to the first connected node
    - Passthrough edges (button_text="__PASSTHROUGH__" or empty/null) are used for auto-routing
    - Templates with only PHONE_NUMBER/URL buttons use passthrough
    """
    node_id = node.node_id

    # Build mapping: button_text -> target_node_id
    # Handle both string and list formats for button_text (legacy data may have lists)
    button_routes = {}
    passthrough_target = None  # For passthrough routing

    for edge in edges:
        if edge.source_node.id == node.id:
            btn_text = edge.button_text

            # Handle case where button_text is stored as a list (data issue)
            if isinstance(btn_text, list):
                logger.warning(f"Edge {edge.edge_id}: button_text is a list {btn_text}, extracting first element")
                btn_text = btn_text[0] if btn_text else ""

            # Track passthrough target separately
            # Passthrough edges have: __PASSTHROUGH__, empty string, or None
            is_passthrough_edge = btn_text in ("__PASSTHROUGH__", "", None) or not btn_text

            if is_passthrough_edge:
                if passthrough_target is None:
                    passthrough_target = edge.target_node.node_id
            else:
                button_routes[btn_text] = edge.target_node.node_id
                # Use first non-passthrough edge as fallback passthrough target
                if passthrough_target is None:
                    passthrough_target = edge.target_node.node_id

    logger.debug(
        f"Router created for node '{node_id}': "
        f"button_routes = {button_routes}, passthrough_target = {passthrough_target}"
    )

    def router(state: FlowState) -> str:
        """Route to next node based on user input (button click)."""
        user_input = state.get("user_input")
        current_node = state.get("current_node_id")

        logger.info(
            f"Router '{node_id}' called: current_node={current_node}, "
            f"user_input='{user_input}', awaiting={state.get('awaiting_input')}"
        )

        # Handle passthrough signal from start/message nodes
        if user_input == "__PASSTHROUGH__":
            if passthrough_target:
                logger.info(f"Node '{node_id}': Passthrough -> routing to '{passthrough_target}'")
                return passthrough_target
            else:
                logger.warning(f"Node '{node_id}': Passthrough but no outgoing edges, ending flow")
                return END

        if not user_input:
            # No input yet - end execution, wait for button click
            logger.info(f"Node '{node_id}': No user input, waiting for button click...")
            return END

        # Find matching button - try exact match first
        target = button_routes.get(user_input)

        # If no exact match, try case-insensitive match
        if not target:
            user_input_lower = user_input.lower().strip()
            for btn_text, btn_target in button_routes.items():
                if btn_text and btn_text.lower().strip() == user_input_lower:
                    target = btn_target
                    logger.info(f"Node '{node_id}': Case-insensitive match found for '{user_input}' -> '{btn_text}'")
                    break

        if target:
            logger.info(f"Node '{node_id}': User clicked '{user_input}' -> routing to '{target}'")
            return target

        # No matching button — for nodes with NO button_routes (e.g. start
        # nodes, message nodes), fall back to passthrough_target so that
        # a resuming session with real user_input still advances.
        if not button_routes and passthrough_target:
            logger.info(
                f"Node '{node_id}': No button_routes defined, using "
                f"passthrough_target '{passthrough_target}' for input '{user_input}'"
            )
            return passthrough_target

        # No matching button - log and end
        logger.warning(
            f"Node '{node_id}': Unknown button '{user_input}'. Available buttons: {list(button_routes.keys())}"
        )
        return END

    return router


# =============================================================================
# Graph Builder
# =============================================================================


class ChatFlowExecutor:
    """
    Builds and executes LangGraph from ChatFlow models.

    Uses in-memory session storage for state management.
    Each contact gets a unique thread_id for their session.

    Usage:
        executor = ChatFlowExecutor(flow)

        # Start a new session
        result = executor.start_session(contact_id=123)

        # Process user input (button click)
        result = executor.process_input(contact_id=123, user_input="Get Started")

        # Get current session state
        state = executor.get_session_state(contact_id=123)

        # Reset a session (e.g., when flow is updated)
        executor.reset_session(contact_id=123)
    """

    def __init__(self, flow: ChatFlow):
        self.flow = flow
        self.flow_id = flow.id
        self._graph = None
        self._start_node_id = None

    @property
    def graph(self):
        """Lazily build and cache the compiled graph."""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph

    @property
    def start_node_id(self) -> str:
        """Get the start node ID (builds graph if needed)."""
        if self._start_node_id is None:
            _ = self.graph  # Triggers build which sets _start_node_id
        return self._start_node_id

    def _get_thread_id(self, contact_id: int) -> str:
        """Generate unique thread ID for a contact's session in this flow."""
        return f"flow_{self.flow_id}_contact_{contact_id}"

    def _build_graph(self) -> StateGraph:
        """
        Build a LangGraph StateGraph from the flow's nodes and edges.

        Returns:
            Compiled StateGraph ready for execution
        """
        logger.info(f"Building graph for flow {self.flow_id}")

        # Load nodes and edges from database
        nodes = list(ChatFlowNode.objects.filter(flow=self.flow).select_related("template"))
        edges = list(ChatFlowEdge.objects.filter(flow=self.flow).select_related("source_node", "target_node"))

        if not nodes:
            raise ValueError(f"Flow {self.flow_id} has no nodes")

        # Create graph
        graph = StateGraph(FlowState)

        # Find start node (first node or node marked as start)
        start_node = None
        for node in nodes:
            if node.node_type == "start" or node.node_data.get("is_start_node"):
                start_node = node
                break

        if not start_node:
            # Default to first node
            start_node = nodes[0]

        self._start_node_id = start_node.node_id

        # Add nodes to graph
        for node in nodes:
            handler = self._create_node_handler(node, edges)
            graph.add_node(node.node_id, handler)

        # Set entry point
        graph.set_entry_point(start_node.node_id)

        # Add edges with conditional routing
        for node in nodes:
            outgoing_edges = [e for e in edges if e.source_node.id == node.id]

            if node.node_type == "end":
                # End nodes go to END
                graph.add_edge(node.node_id, END)

            elif outgoing_edges:
                # Create conditional routing based on button clicks
                router = create_button_router(node, outgoing_edges)

                # Get all possible target nodes
                targets = {e.target_node.node_id for e in outgoing_edges}
                targets.add(END)  # Always allow END as fallback

                graph.add_conditional_edges(node.node_id, router, {target: target for target in targets})

            else:
                # No outgoing edges - this is effectively an end node
                graph.add_edge(node.node_id, END)

        # Compile WITHOUT checkpointer (using in-memory state)
        compiled = graph.compile()

        logger.info(f"Graph built for flow {self.flow_id} with {len(nodes)} nodes and {len(edges)} edges")

        return compiled

    def _get_thread_id(self, contact_id: int) -> str:
        """Generate a unique thread ID for session storage."""
        return f"flow_{self.flow_id}_contact_{contact_id}"

    def _create_node_handler(self, node: ChatFlowNode, edges: List[ChatFlowEdge]):
        """Create the appropriate handler based on node type."""
        node_type = node.node_type

        if node_type == "start":
            # Start nodes use special handler that supports passthrough
            return create_start_node_handler(node, edges)
        elif node_type == "template":
            # Template nodes need edges to determine if passthrough is needed
            return create_template_node_handler(node, edges)
        elif node_type == "message":
            # Message nodes send session messages (non-template) and passthrough
            return create_message_node_handler(node, edges)
        elif node_type == "end":
            return create_end_node_handler(node)
        elif node_type == "condition":
            return create_condition_node_handler(node)
        elif node_type == "delay":
            # Delay nodes schedule continuation via Celery
            return create_delay_node_handler(node, edges)
        elif node_type == "api":
            # API Call nodes make HTTP requests and route by status code
            return create_api_call_node_handler(node, edges)
        else:
            # Default to template handler
            return create_template_node_handler(node, edges)

    def start_session(self, contact_id: int, context: Optional[Dict[str, Any]] = None) -> FlowState:
        """
        Start a new flow session for a contact.

        This will execute from the start node and send the first template.
        If there's an existing session, it will be overwritten.

        Args:
            contact_id: ID of the contact starting the flow
            context: Optional initial context data

        Returns:
            FlowState after executing start node
        """
        thread_id = self._get_thread_id(contact_id)

        initial_state: FlowState = {
            "flow_id": self.flow_id,
            "contact_id": contact_id,
            "current_node_id": "",
            "user_input": None,
            "messages_sent": [],
            "context": context or {},
            "is_complete": False,
            "awaiting_input": False,
            "error": None,
            "_resume_target": None,
            "_pending_user_input": None,
        }

        result = self.graph.invoke(initial_state)

        # Store session state in memory (DB is saved by node handler)
        _session_store[thread_id] = result

        # Schedule any pending delay AFTER graph.invoke() has returned.
        # This avoids nested graph.invoke() when Celery is in eager mode.
        self._schedule_pending_delay(contact_id)

        logger.info(
            f"Started session for contact {contact_id} on flow {self.flow_id}. "
            f"Current node: {result.get('current_node_id')}, "
            f"Awaiting input: {result.get('awaiting_input')}"
        )

        return result

    def process_input(
        self,
        contact_id: int,
        user_input: str,
        additional_context: Optional[Dict[str, Any]] = None,
        resume_from: Optional[str] = None,
    ) -> FlowState:
        """
        Process user input (button click) and advance the flow.

        Loads existing session state from in-memory store or database and continues.

        Args:
            contact_id: ID of the contact
            user_input: Button text that was clicked
            additional_context: Optional context to merge
            resume_from: When resuming after a delay, the node_id to skip to.
                         Passed directly by the Celery task so we don't rely
                         on state (LangGraph drops non-TypedDict keys).

        Returns:
            Updated FlowState after processing
        """
        thread_id = self._get_thread_id(contact_id)

        # For delay resumption, ALWAYS load from DB because the in-memory
        # cache was populated by graph.invoke() which strips non-TypedDict
        # keys (like delay_info).  The DB has the full state.
        is_delay_resume = user_input == "__DELAY_CONTINUE__" and resume_from

        # Get current state from in-memory store
        current_state = None if is_delay_resume else _session_store.get(thread_id)

        # If in-memory state exists but is complete, a newer active session
        # may have been created (e.g. contact re-assigned via admin/signal).
        # Always verify against the DB in this case.
        if current_state and current_state.get("is_complete"):
            logger.info(
                f"In-memory state for contact {contact_id} is complete — checking DB for a newer active session"
            )
            current_state = None  # Force DB lookup below

        # If not in memory, try loading from database (UserChatFlowSession)
        if not current_state:
            db_session = UserChatFlowSession.objects.filter(
                contact_id=contact_id, flow_id=self.flow_id, is_active=True
            ).first()

            if db_session:
                # Restore full state from database
                # context_data now stores the entire FlowState
                logger.info(f"Loading session from database for contact {contact_id}")

                stored_state = db_session.context_data or {}

                # If context_data has full state, use it; otherwise rebuild minimally
                if "flow_id" in stored_state:
                    # Full state was stored
                    current_state = stored_state
                    # Ensure awaiting_input is True since we're processing input
                    current_state["awaiting_input"] = True
                    current_state["user_input"] = None
                else:
                    # Legacy: only context was stored, rebuild state
                    current_state = {
                        "flow_id": self.flow_id,
                        "contact_id": contact_id,
                        "current_node_id": db_session.current_node_id,
                        "user_input": None,
                        "messages_sent": [],
                        "context": stored_state,
                        "is_complete": db_session.is_complete,
                        "awaiting_input": True,
                        "error": None,
                    }

                # Store in memory for subsequent calls
                _session_store[thread_id] = current_state

        if not current_state:
            # No existing session - start new one
            # Note: If user_input exists but no session, it means we lost the session
            # (server restart, expired, etc). We start fresh - the user will get
            # the first template again and can respond to that.
            logger.info(f"No session found for contact {contact_id}, starting new session")
            result = self.start_session(contact_id, additional_context)
            return result

        # Check if flow is already complete
        if current_state.get("is_complete"):
            logger.info(f"Session for contact {contact_id} is already complete")
            return current_state

        # Update state with user input and invoke
        updated_state = {
            **current_state,
            "user_input": user_input,
            "_pending_user_input": None,  # default; may be overridden below
        }

        # ── Delay resume: skip all nodes before the target ──
        # resume_from is passed directly by the Celery task — we do NOT
        # rely on delay_info in state because LangGraph strips non-TypedDict
        # keys from the in-memory state after graph.invoke().
        if user_input == "__DELAY_CONTINUE__" and resume_from:
            updated_state["_resume_target"] = resume_from
            updated_state["user_input"] = "__PASSTHROUGH__"
            logger.info(f"Delay resume for contact {contact_id}: skip-to node '{resume_from}'")

        # ── Button-click replay: skip all nodes before the awaiting node ──
        # When the user clicks a QR button, the graph replays from START.
        # Without _resume_target, every handler re-executes: non-interactive
        # nodes (template w/o QR, delay) re-send messages / re-schedule.
        # Fix: set _resume_target so all handlers fast-forward to the node
        # that was waiting for this button click.  The real button text is
        # stashed in _pending_user_input and restored at the target node.
        elif (
            current_state.get("awaiting_input")
            and user_input
            and user_input not in ("__PASSTHROUGH__", "__DELAY_CONTINUE__")
            and current_state.get("current_node_id")
        ):
            awaiting_node = current_state["current_node_id"]
            updated_state["_resume_target"] = awaiting_node
            updated_state["_pending_user_input"] = user_input
            updated_state["user_input"] = "__PASSTHROUGH__"
            logger.info(
                f"Button-click replay for contact {contact_id}: "
                f"skip-to node '{awaiting_node}', "
                f"pending_input='{user_input}'"
            )

        if additional_context:
            existing_context = updated_state.get("context", {})
            updated_state["context"] = {**existing_context, **additional_context}

        logger.info(
            f"\n{'=' * 60}\n"
            f"PROCESS_INPUT DEBUG - Contact {contact_id}\n"
            f"{'=' * 60}\n"
            f"User input: '{user_input}'\n"
            f"Current node (before invoke): {current_state.get('current_node_id')}\n"
            f"Awaiting input: {current_state.get('awaiting_input')}\n"
            f"Flow ID: {self.flow_id}\n"
            f"State being passed to graph.invoke(): {updated_state}\n"
            f"{'=' * 60}"
        )

        try:
            result = self.graph.invoke(updated_state)
        except Exception as e:
            logger.exception(f"Graph invoke failed: {str(e)}")
            raise

        # Update session state in memory
        _session_store[thread_id] = result

        # Schedule any pending delay AFTER graph.invoke() has returned.
        self._schedule_pending_delay(contact_id)

        logger.info(
            f"\n{'=' * 60}\n"
            f"PROCESS_INPUT RESULT - Contact {contact_id}\n"
            f"{'=' * 60}\n"
            f"New node: {result.get('current_node_id')}\n"
            f"Complete: {result.get('is_complete')}\n"
            f"Awaiting input: {result.get('awaiting_input')}\n"
            f"Error: {result.get('error')}\n"
            f"{'=' * 60}"
        )

        return result

    def _schedule_pending_delay(self, contact_id: int) -> None:
        """Schedule Celery task for a pending delay, if any.

        The delay node handler does NOT call Celery itself (that would
        cause nested graph.invoke() in eager mode).  Instead it writes
        ``delay_info`` to the DB.  This method reads it back and fires
        the task.
        """
        from chat_flow.models import UserChatFlowSession

        session = UserChatFlowSession.objects.filter(
            contact_id=contact_id,
            flow_id=self.flow_id,
            is_active=True,
        ).first()

        if not session:
            return

        ctx = session.context_data or {}
        delay_info = ctx.get("delay_info")

        if not delay_info or delay_info.get("scheduled"):
            return  # No pending delay or already scheduled

        delay_seconds = delay_info.get("delay_seconds", 60)
        next_node_id = delay_info.get("next_node_id")

        if not next_node_id:
            logger.error(f"Delay info missing next_node_id for contact {contact_id}, flow {self.flow_id}")
            return

        try:
            from chat_flow.tasks import continue_flow_after_delay

            # If Celery is in eager mode, apply_async runs synchronously and
            # ignores countdown — the delay would be zero.  Fall back to a
            # background thread that sleeps then fires the task.
            is_eager = getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False) or getattr(
                settings, "CELERY_ALWAYS_EAGER", False
            )

            if is_eager:
                import threading

                flow_id = self.flow_id

                def _fire_after_delay():
                    """Run in a daemon thread so the main thread is not blocked."""
                    import time

                    logger.info(f"Eager-mode delay: sleeping {delay_seconds}s for flow={flow_id}, contact={contact_id}")
                    time.sleep(delay_seconds)
                    continue_flow_after_delay(flow_id, contact_id, next_node_id, context=ctx.get("context", {}))

                t = threading.Thread(target=_fire_after_delay, daemon=True)
                t.start()
            else:
                continue_flow_after_delay.apply_async(
                    args=[self.flow_id, contact_id, next_node_id],
                    kwargs={"context": ctx.get("context", {})},
                    countdown=delay_seconds,
                )

            # Mark as scheduled so we don't double-fire
            delay_info["scheduled"] = True
            session.context_data = ctx
            session.save(update_fields=["context_data"])

            logger.info(
                f"Scheduled delay continuation: flow={self.flow_id}, "
                f"contact={contact_id}, delay={delay_seconds}s, "
                f"next_node='{next_node_id}'"
                f"{' (eager-mode thread)' if is_eager else ''}"
            )
        except Exception as e:
            logger.exception(f"Failed to schedule delay for contact {contact_id}: {e}")

    def get_session_state(self, contact_id: int) -> Optional[FlowState]:
        """
        Get the current session state for a contact.

        Checks in-memory cache first, then falls back to the database
        (UserChatFlowSession) so state survives server restarts and is
        visible across workers.

        Args:
            contact_id: ID of the contact

        Returns:
            Current FlowState or None if no session exists
        """
        thread_id = self._get_thread_id(contact_id)
        state = _session_store.get(thread_id)
        if state:
            return state

        # Fallback: load from database
        db_session = UserChatFlowSession.objects.filter(
            contact_id=contact_id, flow_id=self.flow_id, is_active=True
        ).first()

        if db_session:
            stored_state = db_session.context_data or {}
            if "flow_id" in stored_state:
                state = stored_state
            else:
                # Legacy: only context was stored, rebuild state
                state = {
                    "flow_id": self.flow_id,
                    "contact_id": contact_id,
                    "current_node_id": db_session.current_node_id,
                    "user_input": None,
                    "messages_sent": [],
                    "context": stored_state,
                    "is_complete": db_session.is_complete,
                    "awaiting_input": False,
                    "error": None,
                }
            _session_store[thread_id] = state
            return state

        return None

    def reset_session(self, contact_id: int) -> bool:
        """
        Reset/delete a contact's session.

        Useful when:
        - Flow has been updated and we want to restart
        - User explicitly wants to restart
        - Session has expired

        Args:
            contact_id: ID of the contact

        Returns:
            True if a session was reset, False if no session existed
        """
        thread_id = self._get_thread_id(contact_id)

        # Clear in-memory cache
        memory_cleared = thread_id in _session_store
        if memory_cleared:
            del _session_store[thread_id]

        # Deactivate DB session so it won't be resurrected by the
        # DB-fallback logic in process_input() / get_session_state()
        db_updated = UserChatFlowSession.objects.filter(
            contact_id=contact_id, flow_id=self.flow_id, is_active=True
        ).update(is_active=False, ended_at=timezone.now())

        reset_happened = memory_cleared or db_updated > 0
        if reset_happened:
            logger.info(f"Reset session for contact {contact_id} on flow {self.flow_id}")
        return reset_happened

    def is_session_active(self, contact_id: int) -> bool:
        """Check if a contact has an active (non-complete) session."""
        state = self.get_session_state(contact_id)
        if state:
            return not state.get("is_complete", False)
        return False

    def display_graph(self, format: str = "mermaid"):
        """
        Display the graph visualization in IPython/Jupyter.

        Args:
            format: Output format - "mermaid", "png", or "ascii"

        Returns:
            For "mermaid": IPython display object (renders in notebook)
            For "png": PNG image bytes (displays in notebook)
            For "ascii": String representation

        Usage in Jupyter:
            from chat_flow.models import ChatFlow
            from chat_flow.services.graph_executor import get_executor

            flow = ChatFlow.objects.get(id=1)
            executor = get_executor(flow)
            executor.display_graph()  # Displays mermaid diagram
            executor.display_graph("png")  # Displays PNG image
            executor.display_graph("ascii")  # Returns ASCII art

        Note:
            - "mermaid" works out of the box in Jupyter notebooks
            - "png" requires either:
              a) pygraphviz installed (needs: apt-get install graphviz graphviz-dev)
              b) Falls back to mermaid.ink online API
            - "ascii" works anywhere but is basic
        """
        graph = self.graph

        if format == "mermaid":
            try:
                from IPython.display import Markdown, display

                # Get mermaid diagram
                mermaid_str = graph.get_graph().draw_mermaid()

                # Display as markdown with mermaid code block
                display(
                    Markdown(f"### ChatFlow: {self.flow.name} (ID: {self.flow_id})\n\n```mermaid\n{mermaid_str}\n```")
                )
                return mermaid_str

            except ImportError:
                logger.warning("IPython not available, returning mermaid string")
                return graph.get_graph().draw_mermaid()

        elif format == "png":
            try:
                from IPython.display import Image, display

                # Try native LangGraph PNG (requires pygraphviz)
                try:
                    png_bytes = graph.get_graph().draw_mermaid_png()
                    display(Image(png_bytes))
                    return png_bytes
                except Exception as native_error:
                    logger.warning(f"Native PNG failed ({native_error}), trying mermaid.ink API...")

                    # Fallback: Use mermaid.ink online service
                    import base64
                    import urllib.request

                    mermaid_str = graph.get_graph().draw_mermaid()

                    # Encode mermaid diagram for URL
                    mermaid_encoded = base64.urlsafe_b64encode(mermaid_str.encode()).decode()
                    url = f"https://mermaid.ink/img/{mermaid_encoded}"

                    # Fetch PNG from mermaid.ink
                    with urllib.request.urlopen(url, timeout=10) as response:  # nosec B310 — URL is constructed from internal mermaid graph data
                        png_bytes = response.read()

                    display(Image(png_bytes))
                    return png_bytes

            except ImportError:
                logger.warning("IPython not available, returning mermaid string instead")
                return graph.get_graph().draw_mermaid()
            except Exception as e:
                logger.error(f"Failed to generate PNG: {e}")
                # Final fallback to mermaid text
                print(f"PNG generation failed: {e}")
                print("Falling back to mermaid format...")
                return self.display_graph("mermaid")

        elif format == "ascii":
            try:
                ascii_art = graph.get_graph().draw_ascii()
                print(ascii_art)
                return ascii_art
            except Exception as e:
                logger.error(f"ASCII drawing failed: {e}")
                return f"ASCII drawing not available: {e}"

        else:
            raise ValueError(f"Unknown format: {format}. Use 'mermaid', 'png', or 'ascii'")

    def get_graph_info(self) -> dict:
        """
        Get detailed information about the graph structure.

        Useful for debugging flow configuration.

        Returns:
            dict with nodes, edges, and metadata
        """
        from ..models import ChatFlowEdge, ChatFlowNode

        nodes = list(
            ChatFlowNode.objects.filter(flow=self.flow).values(
                "id", "node_id", "node_type", "template__element_name", "position_x", "position_y"
            )
        )

        edges = list(
            ChatFlowEdge.objects.filter(flow=self.flow).values(
                "id", "edge_id", "source_node__node_id", "target_node__node_id", "button_text", "button_type"
            )
        )

        return {
            "flow_id": self.flow_id,
            "flow_name": self.flow.name,
            "start_node_id": self.start_node_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "nodes": nodes,
            "edges": edges,
        }

    def debug_session(self, contact_id: int) -> dict:
        """
        Get comprehensive debug info for a contact's session.

        Args:
            contact_id: ID of the contact

        Returns:
            dict with session state, node info, and history
        """
        from ..models import ChatFlowNode, UserChatFlowSession

        result = {
            "contact_id": contact_id,
            "flow_id": self.flow_id,
            "flow_name": self.flow.name,
            "thread_id": self._get_thread_id(contact_id),
            "has_session": False,
            "langgraph_state": None,
            "db_session": None,
            "current_node_info": None,
        }

        # Get LangGraph state
        state = self.get_session_state(contact_id)
        if state:
            result["has_session"] = True
            result["langgraph_state"] = state

            # Get current node details
            current_node_id = state.get("current_node_id")
            if current_node_id:
                try:
                    node = ChatFlowNode.objects.select_related("template").get(flow=self.flow, node_id=current_node_id)
                    result["current_node_info"] = {
                        "node_id": node.node_id,
                        "node_type": node.node_type,
                        "template_name": node.template.element_name if node.template else None,
                        "position": {"x": node.position_x, "y": node.position_y},
                        "outgoing_edges": list(node.outgoing_edges.values("button_text", "target_node__node_id")),
                    }
                except ChatFlowNode.DoesNotExist:
                    result["current_node_info"] = {"error": f"Node '{current_node_id}' not found"}

        # Get DB session
        try:
            db_session = UserChatFlowSession.objects.get(contact_id=contact_id, flow=self.flow, is_active=True)
            result["db_session"] = {
                "id": db_session.id,
                "current_node_id": db_session.current_node_id,
                "started_at": db_session.started_at.isoformat(),
                "is_complete": db_session.is_complete,
                "context_data": db_session.context_data,
            }
        except UserChatFlowSession.DoesNotExist:
            result["db_session"] = None

        return result


# =============================================================================
# Graph Cache (for production use)
# =============================================================================

_graph_cache: Dict[str, ChatFlowExecutor] = {}


def get_executor(flow: ChatFlow) -> ChatFlowExecutor:
    """
    Get or create a cached executor for a flow.

    Cache is invalidated when flow.updated_at changes.
    This ensures graph is rebuilt when flow is modified.

    Args:
        flow: ChatFlow instance

    Returns:
        ChatFlowExecutor instance

    Raises:
        ValueError: If the flow is inactive
    """
    if not flow.is_active:
        raise ValueError(f"ChatFlow {flow.id} is inactive and cannot be executed")

    cache_key = f"{flow.id}_{flow.updated_at.timestamp()}"

    if cache_key not in _graph_cache:
        # Clean old versions of this flow from cache
        old_keys = [k for k in _graph_cache if k.startswith(f"{flow.id}_")]
        for old_key in old_keys:
            del _graph_cache[old_key]

        _graph_cache[cache_key] = ChatFlowExecutor(flow)

    return _graph_cache[cache_key]


def clear_graph_cache(flow_id: Optional[int] = None):
    """
    Clear the graph cache.

    Args:
        flow_id: If provided, only clear cache for this flow.
                 If None, clear entire cache.
    """
    global _graph_cache

    if flow_id is None:
        _graph_cache = {}
    else:
        keys_to_remove = [k for k in _graph_cache if k.startswith(f"{flow_id}_")]
        for key in keys_to_remove:
            del _graph_cache[key]


# =============================================================================
# Convenience Functions
# =============================================================================


def start_flow_for_contact(flow_id: int, contact_id: int, context: Optional[Dict] = None) -> FlowState:
    """
    Start a ChatFlow for a contact.

    Args:
        flow_id: ID of the ChatFlow
        contact_id: ID of the TenantContact
        context: Optional initial context

    Returns:
        FlowState after executing start node
    """
    flow = ChatFlow.objects.get(id=flow_id)
    executor = get_executor(flow)
    return executor.start_session(contact_id, context)


def process_button_click(flow_id: int, contact_id: int, button_text: str) -> FlowState:
    """
    Process a button click in a ChatFlow.

    Args:
        flow_id: ID of the ChatFlow
        contact_id: ID of the TenantContact
        button_text: The button text that was clicked

    Returns:
        Updated FlowState
    """
    flow = ChatFlow.objects.get(id=flow_id)
    executor = get_executor(flow)
    return executor.process_input(contact_id, button_text)


def get_contact_flow_state(flow_id: int, contact_id: int) -> Optional[FlowState]:
    """
    Get a contact's current state in a flow.

    Args:
        flow_id: ID of the ChatFlow
        contact_id: ID of the TenantContact

    Returns:
        Current FlowState or None
    """
    flow = ChatFlow.objects.get(id=flow_id)
    executor = get_executor(flow)
    return executor.get_session_state(contact_id)


# =============================================================================
# Debug Utilities
# =============================================================================


def debug_chatflow(flow_id: int, contact_id: int = None) -> dict:
    """
    Comprehensive debug function for ChatFlow issues.

    Run in Django shell:
        from chat_flow.services.graph_executor import debug_chatflow
        debug_chatflow(flow_id=1)  # Debug flow structure
        debug_chatflow(flow_id=1, contact_id=123)  # Debug specific contact's session

    Args:
        flow_id: ID of the ChatFlow to debug
        contact_id: Optional contact ID to debug their session

    Returns:
        dict with complete debug information
    """

    from contacts.models import TenantContact

    result = {
        "flow_info": {},
        "nodes": [],
        "edges": [],
        "button_routes": {},
        "session_info": None,
        "contact_info": None,
    }

    try:
        flow = ChatFlow.objects.get(id=flow_id)
        result["flow_info"] = {
            "id": flow.id,
            "name": flow.name,
            "start_template": flow.start_template.element_name if flow.start_template else None,
        }
    except ChatFlow.DoesNotExist:
        return {"error": f"ChatFlow {flow_id} not found"}

    # Get all nodes
    nodes = ChatFlowNode.objects.filter(flow=flow).select_related("template")
    for node in nodes:
        result["nodes"].append(
            {
                "id": node.id,
                "node_id": node.node_id,
                "node_type": node.node_type,
                "template_id": node.template_id,
                "template_name": node.template.element_name if node.template else None,
            }
        )

    # Get all edges with button_text
    edges = ChatFlowEdge.objects.filter(flow=flow).select_related("source_node", "target_node")
    for edge in edges:
        result["edges"].append(
            {
                "edge_id": edge.edge_id,
                "source_node": edge.source_node.node_id,
                "target_node": edge.target_node.node_id,
                "button_text": edge.button_text,
                "button_text_repr": repr(edge.button_text),  # Show hidden chars
                "button_type": edge.button_type,
            }
        )

        # Build button routes for each node
        source_id = edge.source_node.node_id
        if source_id not in result["button_routes"]:
            result["button_routes"][source_id] = {}
        result["button_routes"][source_id][edge.button_text] = edge.target_node.node_id

    # Contact-specific debug
    if contact_id:
        try:
            contact = TenantContact.objects.get(id=contact_id)
            result["contact_info"] = {
                "id": contact.id,
                "phone": contact.phone,
                "assigned_to_type": contact.assigned_to_type,
                "assigned_to_id": contact.assigned_to_id,
            }
        except TenantContact.DoesNotExist:
            result["contact_info"] = {"error": f"Contact {contact_id} not found"}

        # Get session info
        session = UserChatFlowSession.objects.filter(contact_id=contact_id, flow_id=flow_id, is_active=True).first()

        if session:
            result["session_info"] = {
                "db_session": {
                    "id": session.id,
                    "current_node_id": session.current_node_id,
                    "started_at": str(session.started_at),
                    "is_complete": session.is_complete,
                    "context_data": session.context_data,
                }
            }

            # Check memory state (may be None if server restarted)
            thread_id = f"flow_{flow_id}_contact_{contact_id}"
            memory_state = _session_store.get(thread_id)
            result["session_info"]["memory_state"] = memory_state
            result["session_info"]["memory_cached"] = memory_state is not None
            result["session_info"]["thread_id"] = thread_id
        else:
            result["session_info"] = {"error": "No active session found"}

    # Print summary
    print("\n" + "=" * 70)
    print(f"CHATFLOW DEBUG: Flow {flow_id} - {flow.name}")
    print("=" * 70)

    print(f"\nNodes ({len(result['nodes'])}):")
    for node in result["nodes"]:
        print(f"  - {node['node_id']} ({node['node_type']}): template={node['template_name']}")

    print(f"\nEdges ({len(result['edges'])}):")
    for edge in result["edges"]:
        print(f"  - {edge['source_node']} --[{edge['button_text']!r}]--> {edge['target_node']}")

    print("\nButton Routes per Node:")
    for node_id, routes in result["button_routes"].items():
        print(f"  {node_id}:")
        for btn, target in routes.items():
            print(f"    '{btn}' -> {target}")

    if contact_id and result.get("session_info"):
        print(f"\nSession for Contact {contact_id}:")
        if "error" in result["session_info"]:
            print(f"  ERROR: {result['session_info']['error']}")
        else:
            db = result["session_info"].get("db_session", {})
            print(f"  DB Session ID: {db.get('id')}")
            print(f"  Current Node: {db.get('current_node_id')}")
            print(f"  Complete: {db.get('is_complete')}")

            mem = result["session_info"].get("memory_state")
            if mem:
                print(
                    f"  Memory State: current_node={mem.get('current_node_id')}, awaiting={mem.get('awaiting_input')}"
                )
            else:
                print("  Memory State: NOT IN MEMORY (will load from DB)")

    print("=" * 70 + "\n")

    return result
