"""
Celery tasks for ChatFlow execution.

These tasks handle asynchronous ChatFlow operations like starting sessions,
processing user input, and handling timeouts.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name='chat_flow.tasks.start_chatflow_session',
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def start_chatflow_session_task(self, chatflow_id: int, contact_id: int, context: dict = None):
    """
    Start a ChatFlow session for a contact.
    
    This task is triggered when a contact is assigned to a ChatFlow.
    It initializes the LangGraph executor and sends the first template message.
    
    Args:
        chatflow_id: ID of the ChatFlow to start
        contact_id: ID of the TenantContact
        context: Optional initial context data (e.g., variable values)
        
    Returns:
        dict with session state after starting
    """
    from chat_flow.models import ChatFlow
    from chat_flow.services.graph_executor import get_executor
    
    logger.info(
        f"[Task] Starting ChatFlow session: flow={chatflow_id}, contact={contact_id}"
    )
    
    try:
        # Load the ChatFlow
        flow = ChatFlow.objects.select_related('tenant', 'start_template').get(id=chatflow_id)
        
        if not flow.is_active:
            logger.warning(
                f"[Task] ChatFlow {chatflow_id} is inactive, skipping session start for contact {contact_id}"
            )
            return {
                'success': False,
                'error': f'ChatFlow {chatflow_id} is inactive',
                'chatflow_id': chatflow_id,
                'contact_id': contact_id,
            }
        
        # Get or create the executor
        executor = get_executor(flow)
        
        # Start the session (sends first template)
        result = executor.start_session(
            contact_id=contact_id,
            context=context or {}
        )
        
        logger.info(
            f"[Task] ChatFlow {chatflow_id} started for contact {contact_id}. "
            f"Current node: {result.get('current_node_id')}, "
            f"Awaiting input: {result.get('awaiting_input')}, "
            f"Messages sent: {len(result.get('messages_sent', []))}"
        )
        
        return {
            'success': True,
            'chatflow_id': chatflow_id,
            'contact_id': contact_id,
            'current_node_id': result.get('current_node_id'),
            'awaiting_input': result.get('awaiting_input'),
            'is_complete': result.get('is_complete'),
            'messages_sent_count': len(result.get('messages_sent', [])),
        }
        
    except ChatFlow.DoesNotExist:
        logger.error(f"[Task] ChatFlow {chatflow_id} not found")
        return {
            'success': False,
            'error': f'ChatFlow {chatflow_id} not found',
            'chatflow_id': chatflow_id,
            'contact_id': contact_id,
        }
    except Exception as e:
        logger.exception(
            f"[Task] Failed to start ChatFlow {chatflow_id} for contact {contact_id}: {e}"
        )
        try:
            from notifications.signals import create_automation_notification
            create_automation_notification(flow, 'failed')
        except Exception:
            pass
        # Let Celery handle retry via autoretry_for
        raise


@shared_task(
    bind=True,
    name='chat_flow.tasks.process_chatflow_input',
    max_retries=3,
    default_retry_delay=5,
)
def process_chatflow_input_task(
    self,
    chatflow_id: int,
    contact_id: int,
    user_input: str,
    additional_context: dict = None
):
    """
    Process user input (button click) in a ChatFlow.
    
    This task is triggered when a user responds to a ChatFlow message.
    It advances the flow based on the button clicked.
    
    Args:
        chatflow_id: ID of the ChatFlow
        contact_id: ID of the TenantContact
        user_input: The button text that was clicked
        additional_context: Optional context to merge
        
    Returns:
        dict with updated session state
    """
    from chat_flow.models import ChatFlow
    from chat_flow.services.graph_executor import get_executor
    
    logger.info(
        f"[Task] Processing ChatFlow input: flow={chatflow_id}, "
        f"contact={contact_id}, input='{user_input}'"
    )
    
    try:
        flow = ChatFlow.objects.get(id=chatflow_id)
        executor = get_executor(flow)
        
        result = executor.process_input(
            contact_id=contact_id,
            user_input=user_input,
            additional_context=additional_context
        )
        
        logger.info(
            f"[Task] ChatFlow {chatflow_id} processed input for contact {contact_id}. "
            f"New node: {result.get('current_node_id')}, "
            f"Complete: {result.get('is_complete')}"
        )
        
        return {
            'success': True,
            'chatflow_id': chatflow_id,
            'contact_id': contact_id,
            'user_input': user_input,
            'current_node_id': result.get('current_node_id'),
            'awaiting_input': result.get('awaiting_input'),
            'is_complete': result.get('is_complete'),
        }
        
    except ChatFlow.DoesNotExist:
        logger.error(f"[Task] ChatFlow {chatflow_id} not found")
        return {
            'success': False,
            'error': f'ChatFlow {chatflow_id} not found',
        }
    except Exception as e:
        logger.exception(
            f"[Task] Failed to process input for ChatFlow {chatflow_id}, "
            f"contact {contact_id}: {e}"
        )
        raise


@shared_task(name='chat_flow.tasks.reset_chatflow_session')
def reset_chatflow_session_task(chatflow_id: int, contact_id: int):
    """
    Reset a ChatFlow session for a contact.
    
    Used when:
    - Flow is updated and we want to restart
    - User explicitly wants to restart
    - Contact is unassigned from the ChatFlow
    
    Args:
        chatflow_id: ID of the ChatFlow
        contact_id: ID of the TenantContact
        
    Returns:
        dict with reset status
    """
    from chat_flow.models import ChatFlow, UserChatFlowSession
    from chat_flow.services.graph_executor import get_executor
    
    logger.info(
        f"[Task] Resetting ChatFlow session: flow={chatflow_id}, contact={contact_id}"
    )
    
    try:
        flow = ChatFlow.objects.get(id=chatflow_id)
        executor = get_executor(flow)
        
        # Reset in-memory state
        was_reset = executor.reset_session(contact_id)
        
        # Also deactivate DB session
        updated = UserChatFlowSession.objects.filter(
            flow_id=chatflow_id,
            contact_id=contact_id,
            is_active=True
        ).update(is_active=False)
        
        logger.info(
            f"[Task] Reset ChatFlow {chatflow_id} session for contact {contact_id}. "
            f"Memory reset: {was_reset}, DB sessions deactivated: {updated}"
        )
        
        return {
            'success': True,
            'chatflow_id': chatflow_id,
            'contact_id': contact_id,
            'memory_reset': was_reset,
            'db_sessions_deactivated': updated,
        }
        
    except ChatFlow.DoesNotExist:
        logger.error(f"[Task] ChatFlow {chatflow_id} not found")
        return {
            'success': False,
            'error': f'ChatFlow {chatflow_id} not found',
        }
    except Exception as e:
        logger.exception(
            f"[Task] Failed to reset ChatFlow {chatflow_id} session for contact {contact_id}: {e}"
        )
        return {
            'success': False,
            'error': str(e),
        }


@shared_task(
    bind=True,
    name='chat_flow.tasks.continue_flow_after_delay',
    max_retries=3,
    default_retry_delay=10,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def continue_flow_after_delay(self, flow_id: int, contact_id: int, next_node_id: str, context: dict = None):
    """
    Continue a ChatFlow execution after a delay node's timer expires.
    
    This task is scheduled by delay nodes to resume flow execution
    after the specified delay period.
    
    Args:
        flow_id: ID of the ChatFlow
        contact_id: ID of the TenantContact
        next_node_id: ID of the node to continue execution from
        context: Optional context data to pass along
        
    Returns:
        dict with execution result
    """
    from chat_flow.models import ChatFlow, UserChatFlowSession
    from chat_flow.services.graph_executor import get_executor
    
    logger.info(
        f"[Task] Continuing ChatFlow after delay: flow={flow_id}, contact={contact_id}, "
        f"next_node={next_node_id}"
    )
    
    try:
        # Load the ChatFlow
        flow = ChatFlow.objects.select_related('tenant', 'start_template').get(id=flow_id)
        
        # Check if session is still active
        session = UserChatFlowSession.objects.filter(
            flow_id=flow_id,
            contact_id=contact_id,
            is_active=True
        ).first()
        
        if not session:
            logger.warning(
                f"[Task] No active session found for flow={flow_id}, contact={contact_id}. "
                f"Delay continuation skipped (session may have been reset or ended)."
            )
            return {
                'success': False,
                'error': 'No active session found',
                'flow_id': flow_id,
                'contact_id': contact_id,
            }
        
        # Get the executor
        executor = get_executor(flow)
        
        # Continue execution from the next node.
        # resume_from tells process_input which node to skip to — we pass
        # it directly because LangGraph strips non-TypedDict keys (like
        # delay_info) from in-memory state after graph.invoke().
        result = executor.process_input(
            contact_id=contact_id,
            user_input="__DELAY_CONTINUE__",
            additional_context=context or {},
            resume_from=next_node_id
        )
        
        logger.info(
            f"[Task] ChatFlow continued after delay: flow={flow_id}, contact={contact_id}, "
            f"new_node={result.get('current_node_id')}"
        )
        
        return {
            'success': True,
            'flow_id': flow_id,
            'contact_id': contact_id,
            'current_node_id': result.get('current_node_id'),
            'awaiting_input': result.get('awaiting_input', False),
        }
        
    except ChatFlow.DoesNotExist:
        logger.error(f"[Task] ChatFlow {flow_id} not found for delay continuation")
        return {
            'success': False,
            'error': f'ChatFlow {flow_id} not found',
        }
    except Exception as e:
        logger.exception(
            f"[Task] Failed to continue ChatFlow {flow_id} after delay for contact {contact_id}: {e}"
        )
        raise  # Let Celery retry