"""
Sample Gupshup subscription response:
{
    "status": "success",
    "subscription": {
        "active": true,
        "appId": "bf9ee64c-xxxxxxxx-xxxx-xxxx577007c4",
        "createdOn": 1705574838954,
        "id": "8166",
        "mode": 2047,
        "meta": "{\"headers\": { \"Authorisation\":\"Bearer YOUR_TOKEN_HERE\"} }",
        "modifiedOn": 1705574838954,
        "showOnUI": false,
        "tag": "V3 Subscription",
        "url":"https://example.com/webhook",
        "version": 3
    }
}
"""

import logging
from datetime import datetime
from typing import Optional

from celery import shared_task
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

# Ensure team_inbox signals are loaded for broadcasting new messages
# This is needed because Celery workers may not run AppConfig.ready()
import team_inbox.signals  # noqa: F401
from wa.utility.apis.gupshup.subscription_api import SubscriptionAPI
from wa.utility.helper import (extract_message_input, extract_subscription_id,
                               get_subscription_data)

logger = logging.getLogger(__name__)


def _handle_chatflow_routing(contact, webhook_instance, message_content):
    """
    Route incoming message to ChatFlow if contact is assigned to one.
    
    This function:
    1. Checks if contact.assigned_to_type == 'CHATFLOW'
    2. Gets the ChatFlow ID from contact.assigned_to_id
    3. Loads the UserChatFlowSession for this contact
    4. Extracts user input (button text) from the message
    5. Invokes the LangGraph executor to process the input
    
    Args:
        contact: TenantContact instance
        webhook_instance: IncomingMessageWebHookDump instance
        message_content: Parsed message content dict
    """
    from contacts.models import AssigneeTypeChoices
    
    logger.info(
        f"\n{'#'*60}\n"
        f"CHATFLOW ROUTING CHECK - Contact {contact.id}\n"
        f"{'#'*60}\n"
        f"assigned_to_type: {contact.assigned_to_type}\n"
        f"assigned_to_id: {contact.assigned_to_id}\n"
        f"message_content type: '{message_content.get('type')}'\n"
        f"message_content body: '{message_content.get('body')}'\n"
        f"{'#'*60}"
    )
    
    # Check if contact is assigned to a ChatFlow
    # Primary: check assigned_to_type
    # Fallback: check for any active UserChatFlowSession (handles race conditions
    # or manual unassignment while session is still active)
    chatflow_id = None
    
    if contact.assigned_to_type == AssigneeTypeChoices.CHATFLOW:
        chatflow_id = contact.assigned_to_id
    
    if not chatflow_id:
        # Fallback: look for an active session even if contact is not assigned
        from chat_flow.models import UserChatFlowSession
        active_session = UserChatFlowSession.objects.filter(
            contact=contact,
            is_active=True,
            is_complete=False,
        ).select_related('flow').first()
        
        if active_session:
            chatflow_id = active_session.flow_id
            logger.info(
                f"Contact {contact.id} is {contact.assigned_to_type} but has "
                f"active session {active_session.id} for flow {chatflow_id} "
                f"— routing to chatflow anyway"
            )
        else:
            logger.info(
                f"Contact {contact.id} NOT assigned to CHATFLOW "
                f"(assigned_to_type={contact.assigned_to_type}) and no active session"
            )
            return
    
    if not chatflow_id:
        logger.warning(f"Contact {contact.id} is assigned to CHATFLOW but has no assigned_to_id")
        return
    
    try:
        from chat_flow.models import ChatFlow, UserChatFlowSession
        from chat_flow.services.graph_executor import get_executor

        # Load the ChatFlow
        try:
            flow = ChatFlow.objects.get(id=chatflow_id)
        except ChatFlow.DoesNotExist:
            logger.error(f"ChatFlow {chatflow_id} not found for contact {contact.id}")
            return
        
        # Get active session for this contact
        session = UserChatFlowSession.objects.filter(
            contact=contact,
            flow=flow,
            is_active=True
        ).first()
        
        if not session:
            logger.info(f"No active session for contact {contact.id} in flow {chatflow_id}")
            return
        
        # Extract user input from the message
        # For button clicks, use button text from message_content
        # For text messages, use the text content
        user_input = None
        msg_type = message_content.get('type', '')
        
        if msg_type == 'button_reply':
            # Button click — message_content looks like:
            # {'type': 'button_reply', 'body': {'text': 'Unsubscribe'}, 'button_id': '...'}
            user_input = (
                message_content.get('body', {}).get('text')
                or message_content.get('button_id')
            )
            logger.info(f"ChatFlow: Button click detected from content - '{user_input}'")
        elif msg_type == 'text':
            # Text message
            user_input = message_content.get('body', {}).get('text', '')
            logger.info(f"ChatFlow: Text message detected - '{user_input}'")
        elif msg_type == 'interactive':
            # Interactive list/button reply
            user_input = (
                message_content.get('body', {}).get('text')
                or message_content.get('title')
            )
            logger.info(f"ChatFlow: Interactive reply detected - '{user_input}'")
        else:
            logger.info(f"ChatFlow: Unhandled message type '{msg_type}' for contact {contact.id}")
        
        if not user_input:
            logger.info(f"No user input extracted from message for contact {contact.id}")
            return
        
        # Get the executor and process the input
        executor = get_executor(flow)
        
        logger.info(
            f"Processing ChatFlow input for contact {contact.id}: "
            f"flow={chatflow_id}, session={session.id}, input='{user_input}'"
        )
        
        # Process the input - this will:
        # 1. Invoke the graph from current node with user_input
        # 2. Route to next node based on button clicked
        # 3. Send next template message
        # 4. Update UserChatFlowSession
        result = executor.process_input(
            contact_id=contact.id,
            user_input=user_input
        )
        
        logger.info(
            f"ChatFlow processed for contact {contact.id}: "
            f"new_node={result.get('current_node_id')}, "
            f"complete={result.get('is_complete')}"
        )
        
        # If flow is complete, unassign contact from ChatFlow
        if result.get('is_complete'):
            logger.info(f"ChatFlow complete for contact {contact.id}, unassigning from flow")
            contact.assigned_to_type = AssigneeTypeChoices.UNASSIGNED
            contact.assigned_to_id = None
            contact.save(update_fields=['assigned_to_type', 'assigned_to_id'])
        
    except Exception as e:
        logger.exception(f"Error processing ChatFlow for contact {contact.id}: {str(e)}")


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_template_with_bsp_task(self, template_id: int, wa_id: int):
    """
    Celery task to sync a template with BSP (e.g., Gupshup) to get their internal ID.
    
    This is called after creating a template via META Direct API.
    The BSP needs to sync their database with META's template database.
    
    Args:
        template_id: WATemplate primary key
        wa_app_id: TenantWAApp primary key
    """
    from tenants.models import TenantWAApp
    from wa.models import WATemplate
    from wa.services.sync_templates import get_sync_service
    
    try:
        template = WATemplate.objects.get(pk=template_id)
        wa_app = TenantWAApp.objects.get(pk=wa_id)
        
        sync_service = get_sync_service(wa_app)
        if not sync_service:
            logger.warning(f"No BSP sync service available for app {wa_id}")
            return {"status": "skipped", "reason": f"no_sync_service for wa_id {wa_id}"}
        
        # Trigger sync and get BSP ID
        bsp_id = sync_service.sync_and_get_bsp_id(template.element_name)
        
        if bsp_id:
            # Use update() to avoid triggering save() validation
            WATemplate.objects.filter(pk=template.pk).update(bsp_id=bsp_id)
            logger.info(f"Template '{template.element_name}' synced with BSP, bsp_id: {bsp_id}")
            return {"status": "success", "bsp_id": bsp_id, "template_name": template.element_name}
        else:
            logger.warning(f"Could not get BSP ID for template '{template.element_name}', will retry")
            # Retry - BSP might not have synced with META yet
            raise self.retry(exc=Exception("BSP ID not found, retrying..."))
            
    except WATemplate.DoesNotExist:
        logger.error(f"Template {template_id} not found")
        return {"status": "error", "reason": "template_not_found"}
    except TenantWAApp.DoesNotExist:
        logger.error(f"TenantWAApp {wa_app_id} not found")
        return {"status": "error", "reason": "wa_app_not_found"}
    except self.MaxRetriesExceededError:
        logger.error(f"Max retries exceeded for template sync: {template_id}")
        return {"status": "error", "reason": "max_retries_exceeded"}
    except Exception as e:
        logger.exception(f"BSP sync failed for template {template_id}: {e}")
        raise self.retry(exc=e)


@shared_task
def trigger_waapi_subscription(pk: str):
    """
    Trigger a WhatsApp API subscription creation.
    
    Args:
        pk: UUID string of the WASubscription instance
    """
    try:
        from wa.models import SubscriptionStatus, WASubscription
        instance = WASubscription.objects.select_related('wa_app').get(pk=pk)
        
        waapi = SubscriptionAPI(appId=instance.wa_app.app_id, token=instance.wa_app.app_secret)
        
        subscription_data = {
            'url': instance.webhook_url,
            'event_types': instance.event_types,
        }
        
        if not instance.bsp_subscription_id:
            logger.debug("Creating new subscription for instance %s", instance.pk)
            response = waapi.create_subscription(subscription_data)
        else:
            raise Exception("Subscription already exists, cannot create a new one.")
        
        # Update the model with response data
        logger.debug("Subscription response: %s", response)
        _status = response.get("status", None) 
        status = SubscriptionStatus.ACTIVE if _status == "success" else SubscriptionStatus.FAILED
        error = response.get("message", "")
        sub_id = extract_subscription_id(response)
        
        logger.debug("status: %s, error: %s, sub_id: %s", status, error, sub_id)
        WASubscription.objects.filter(pk=instance.pk).update(
            status=status,
            error_message=error,
            bsp_subscription_id=sub_id if sub_id else instance.bsp_subscription_id
        )
    except WASubscription.DoesNotExist:
        raise Exception(f"WASubscription with pk={pk} does not exist.")
    except Exception as e:
        raise Exception(f"Failed to trigger WAAPI subscription: {str(e)}")

@shared_task(bind=True)
def submit_template_to_gupshup(self, template_id: int):
    """
    Celery task to submit a pending template to Gupshup API for approval.
    This task is triggered when a template status becomes PENDING.
    
    Returns:
        dict: Result containing status, template_id, curl_command for debugging
    """
    result = {
        'template_id': template_id,
        'status': 'unknown',
        'curl_command': None,
        'response': None,
        'error': None
    }
    
    try:
        from wa.models import StatusChoices, WATemplate
        from wa.utility.apis.gupshup.template_api import TemplateAPI

        # Get the template instance
        template = WATemplate.objects.get(id=template_id)
        result['template_name'] = template.element_name
        
        # Only process if status is still PENDING
        if template.status != StatusChoices.PENDING:
            logger.debug("Template %s is no longer PENDING, skipping submission", template.element_name)
            result['status'] = 'skipped'
            result['error'] = f"Template status is {template.status}, not PENDING"
            return result
        
        # Initialize the template API
        template_api = TemplateAPI(
            appId=template.wa_app.app_id, 
            token=template.wa_app.app_secret
        )
        
        # Prepare template data for submission
        template_data = template.to_gupshup_payload()
        
        # Submit template to Gupshup
        logger.debug("Submitting template %s to Gupshup API", template.element_name)
        logger.debug("Submitting template %s to Gupshup API", template_data)
        response = template_api.apply_for_template(template_data)
        
        # Capture the curl command that was used
        curl_command = template_api.last_curl_command
        result['curl_command'] = curl_command
        result['response'] = response
        
        # Build debug info string
        import json
        debug_info = f"=== TEMPLATE SUBMISSION DEBUG INFO ===\n"
        debug_info += f"Submitted at: {timezone.now().isoformat()}\n"
        debug_info += f"Template Data:\n{json.dumps(template_data, indent=2)}\n\n"
        debug_info += f"{curl_command}\n\n"
        debug_info += f"Response:\n{json.dumps(response, indent=2)}\n"
        
        # Process the response
        if response.get("status") == "success":
            result['status'] = 'success'
            # Template submitted successfully
            submitted_template_id = response.get("template", {}).get("id")
            if submitted_template_id:
                template.template_id = submitted_template_id
                template.submission_debug_info = debug_info
                # Status might remain PENDING until Gupshup approves it
                template.save(update_fields=['template_id', 'submission_debug_info'])
                logger.debug("Template %s submitted successfully with ID: %s", template.element_name, submitted_template_id)
            
            
        else:
            result['status'] = 'failed'
            # Handle submission failure
            error_message = response.get("message", "Unknown error during template submission")
            result['error'] = error_message
            logger.error("Failed to submit template %s: %s", template.element_name, error_message)
            
            # You might want to update the template status to REJECTED or keep it PENDING
            # template.status = StatusChoices.REJECTED
            # template.save(update_fields=['status'])
            template.error_message = error_message
            template.submission_debug_info = debug_info
            template.save(update_fields=['error_message', 'submission_debug_info'])
            logger.debug("Updated template %s with error message.", template.element_name)


            # You could also send a notification to admins about the failure
        
        return result
            
    except WATemplate.DoesNotExist:
        result['status'] = 'error'
        result['error'] = f"WATemplate with id={template_id} does not exist."
        raise Exception(result['error'])
    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)
        
        try:
            # Try to save error info to template
            template.error_message = str(e)
            template.submission_debug_info = f"=== ERROR DEBUG INFO ===\nError: {str(e)}\n\nCurl Command:\n{result.get('curl_command', 'Not captured')}"
            template.save(update_fields=['error_message', 'submission_debug_info'])
            template.status = StatusChoices.FAILED
            template.save(update_fields=['status'])
            logger.debug("Updated template %s with error message.", template.element_name)
        except:
            pass
        
        raise Exception(f"Failed to submit template to Gupshup: {str(e)}")


@shared_task
def process_message_webhook(pk: str):
    """
    Process incoming message webhooks from WAWebhookEvent.
    
    BSP-aware: dispatches to Gupshup or META Cloud API parsing based on
    ``instance.bsp``.  Both paths normalise data into the same
    ``extracted_data`` dict so the downstream team_inbox creation logic
    is shared.
    
    Creates team_inbox Message + Contact entries.
    Routes to ChatFlow if contact is assigned to one.
    
    Args:
        pk: UUID string of the WAWebhookEvent instance
    """
    try:
        from contacts.models import TenantContact
        from team_inbox.models import (AuthorChoices, MessageDirectionChoices,
                                       MessagePlatformChoices, Messages)
        from tenants.models import BSPChoices, TenantWAApp
        from wa.models import WAWebhookEvent
        
        instance: WAWebhookEvent = WAWebhookEvent.objects.get(pk=pk)
        
        # Only process MESSAGE type events
        if instance.event_type != 'MESSAGE':
            logger.debug("Skipping non-MESSAGE event %s", instance.pk)
            return
        
        payload = instance.payload

        # ── Defence-in-depth: catch mis-classified status webhooks ─────
        # META Cloud API sends status/read-receipt/billing webhooks with
        # field="messages" but value.statuses instead of value.messages.
        # If the classifier let one slip through, reclassify and reroute.
        try:
            _value = (
                payload.get('entry', [{}])[0]
                .get('changes', [{}])[0]
                .get('value', {})
            )
        except (IndexError, AttributeError):
            _value = {}

        if 'statuses' in _value and 'messages' not in _value:
            logger.info(
                "Reclassifying webhook %s from MESSAGE → STATUS "
                "(payload has statuses but no messages)",
                pk,
            )
            instance.event_type = 'STATUS'
            instance.save(update_fields=['event_type'])
            process_message_status_webhook(pk)
            return

        parsing_successful = False
        extracted_data = {}
        
        # ── BSP-aware parsing ─────────────────────────────────────────
        bsp = getattr(instance, 'bsp', None)
        
        if bsp == BSPChoices.META:
            # ── META Cloud API ────────────────────────────────────────
            try:
                # Pass wa_app so the parser can download+save media via
                # the Graph API (META webhooks only include media id,
                # not a persistent URL).
                extracted_data = _parse_meta_message_payload(
                    payload, wa_app=instance.wa_app
                )
                
                # wa_app is already set by MetaWebhookView; keep it.
                # If somehow missing, look up by waba_id.
                if not instance.wa_app_id:
                    waba_id = extracted_data.get('waba_id')
                    if waba_id:
                        wa_app = TenantWAApp.objects.get(
                            waba_id=waba_id, bsp=BSPChoices.META
                        )
                        instance.wa_app = wa_app
                
                parsing_successful = True
            except Exception as e:
                instance.error_message = f"Failed to parse META message: {str(e)}"
        else:
            # ── Gupshup (default / legacy) ─────────────────────────────
            try:
                extracted_data = _parse_gupshup_message_payload(payload)
                
                # Look up WA App by gs_app_id
                if not instance.wa_app_id:
                    gs_app_id = extracted_data.get('gs_app_id')
                    if gs_app_id:
                        wa_app = TenantWAApp.objects.get(app_id=gs_app_id)
                        instance.wa_app = wa_app
                
                parsing_successful = True
            except Exception as e:
                instance.error_message = f"Failed to parse message input: {str(e)}"
        
        # Save and mark as processed
        instance.is_processed = True
        try:
            instance.save(update_fields=['wa_app', 'is_processed', 'error_message'])
            logger.debug("Successfully processed message webhook for instance %s", instance.pk)
        except Exception as e:
            logger.error("Error saving WAWebhookEvent: %s", str(e))
        
        # ── Create Messages entry in team_inbox ───────────────────────
        if parsing_successful and instance.wa_app:
            try:
                tenant = instance.wa_app.tenant
                contact_phone = extracted_data.get('contact_phone')
                contact_name = extracted_data.get('contact_name', '')

                # Validate phone number — reject obviously invalid values
                # that would create phantom "+None" contacts.
                if (
                    not contact_phone
                    or contact_phone in ('+None', 'None', '+', '+null')
                    or len(contact_phone) < 4
                ):
                    logger.warning(
                        "Webhook %s: invalid contact_phone=%r — skipping "
                        "contact/message creation (likely status webhook "
                        "mis-classified as MESSAGE)",
                        pk, contact_phone,
                    )
                    instance.error_message = (
                        f"Invalid contact_phone: {contact_phone!r}"
                    )
                    instance.save(update_fields=['error_message'])
                    return
                
                # Get or create contact by phone number
                contact, created = TenantContact.objects.get_or_create(
                    tenant=tenant,
                    phone=contact_phone,
                    defaults={
                        'first_name': contact_name or '',
                        'last_name': '',
                        'source': MessagePlatformChoices.WHATSAPP,
                    }
                )
                if created:
                    logger.debug("Created new contact: %s", contact.phone)
                
                # Build message content according to team_inbox validator schema
                content = _build_team_inbox_content(extracted_data, instance)
                
                # Create a MessageEventIds entry for timeline ordering
                from team_inbox.models import MessageEventIds
                message_event_id = MessageEventIds.objects.create()
                
                # Create the Messages entry
                message = Messages.objects.create(
                    tenant=tenant,
                    message_id=message_event_id,
                    content=content,
                    direction=MessageDirectionChoices.INCOMING,
                    platform=MessagePlatformChoices.WHATSAPP,
                    author=AuthorChoices.CONTACT,
                    contact=contact,
                )
                
                # Update timestamp to message_actual_time
                if extracted_data.get('message_actual_time'):
                    from django.utils.dateparse import parse_datetime
                    actual_time = parse_datetime(extracted_data['message_actual_time'])
                    if actual_time:
                        Messages.objects.filter(pk=message.pk).update(timestamp=actual_time)
                
                logger.debug("Created team_inbox Message %s for webhook %s", message.pk, instance.pk)
                
                # Route to ChatFlow if contact is assigned to a ChatFlow
                _handle_chatflow_routing(contact, instance, content)
                
            except Exception as e:
                logger.error("Error creating team_inbox Message: %s", str(e))
        
    except WAWebhookEvent.DoesNotExist:
        raise Exception(f"WAWebhookEvent with pk={pk} does not exist.")
    except Exception as e:
        raise Exception(f"Failed to process message webhook: {str(e)}")


# ── BSP-specific message payload parsers ──────────────────────────────────────

def _parse_gupshup_message_payload(payload: dict) -> dict:
    """
    Parse a Gupshup webhook payload into a normalised ``extracted_data`` dict.
    Uses the existing ``extract_message_input`` helper that returns a
    ``MessageInput`` Pydantic model.
    """
    from wa.utility.data_model.gupshup.message_input import MessageInput
    
    processed_data: MessageInput = extract_message_input(payload)
    
    # Guard: if the parser returned no contact phone or no message data,
    # the payload is almost certainly a status/misc webhook that was
    # mis-classified as MESSAGE.  Raise so the caller marks it as an error
    # instead of creating a "+None" phantom contact.
    if not processed_data.contact_phone:
        raise ValueError(
            "Gupshup payload has no contact_phone — likely a status/misc "
            "webhook mis-classified as MESSAGE.  Skipping."
        )
    
    extracted_data = {
        'contact_name': processed_data.contact_profile_name,
        'contact_phone': f"+{processed_data.contact_phone}",
        'message_id': processed_data.message_id,
        'message_type': processed_data.message_type,
        'gs_app_id': processed_data.gs_app_id,
    }
    
    # Convert Unix timestamp to ISO string
    if processed_data.timestamp:
        extracted_data['message_actual_time'] = timezone.datetime.fromtimestamp(
            processed_data.timestamp,
            tz=timezone.get_current_timezone()
        ).isoformat()
    
    # Extract type-specific fields
    if processed_data.message_type == 'text':
        extracted_data['text'] = processed_data.text
    elif processed_data.message_type == 'image':
        extracted_data['image_link'] = processed_data.image_url
        extracted_data['text'] = processed_data.caption
        extracted_data['mime_type'] = processed_data.mime_type
    elif processed_data.message_type == 'video':
        extracted_data['video_link'] = processed_data.video_url
        extracted_data['text'] = processed_data.caption
        extracted_data['mime_type'] = processed_data.mime_type
    elif processed_data.message_type == 'audio':
        extracted_data['audio_link'] = processed_data.audio_url
        extracted_data['mime_type'] = processed_data.mime_type
    elif processed_data.message_type == 'document':
        extracted_data['document_link'] = processed_data.document_url
        extracted_data['text'] = processed_data.caption if processed_data.caption else processed_data.file_name
        extracted_data['mime_type'] = processed_data.mime_type
    elif processed_data.message_type == 'interactive':
        if processed_data.interactive_type == 'button_reply':
            extracted_data['text'] = f"[Button: {processed_data.button_title}]"
            extracted_data['button_id'] = processed_data.button_id
            extracted_data['button_title'] = processed_data.button_title
        elif processed_data.interactive_type == 'list_reply':
            extracted_data['text'] = f"[List: {processed_data.list_title}]"
            extracted_data['button_id'] = processed_data.list_id
            extracted_data['button_title'] = processed_data.list_title
    elif processed_data.message_type == 'button':
        extracted_data['text'] = f"[Button: {processed_data.button_title}]"
        extracted_data['button_id'] = processed_data.button_id
        extracted_data['button_title'] = processed_data.button_title
    elif processed_data.message_type == 'order':
        # BE-18: Incoming order from customer (catalog order)
        extracted_data['message_type'] = 'order'
        extracted_data['text'] = getattr(processed_data, 'text', '') or ''
        # order data is in the raw payload, pass through
        extracted_data['order'] = getattr(processed_data, 'order', None) or {}
    else:
        extracted_data['text'] = f"[{processed_data.message_type} message]"
    
    return extracted_data


def _download_and_save_meta_media(wa_app, media_id: str, mime_type: str = None) -> str:
    """
    Download an incoming media file from META Cloud API and persist it
    to Django's default storage (``MEDIA_ROOT``), returning an absolute
    URL that the frontend can render directly.

    Flow:
        1. ``MetaMediaAPI.get_media_url(media_id)`` → temporary URL (5 min)
        2. ``MetaMediaAPI.download_media(url)``     → binary bytes
        3. Save to ``incoming_media/<tenant_id>/<uuid>.<ext>``
        4. Return absolute URL

    If any step fails the function logs a warning and returns the raw
    ``media_id`` string so the caller can still store *something*.

    Args:
        wa_app: ``TenantWAApp`` instance (must have META credentials)
        media_id: META media ID from the webhook payload
        mime_type: Optional MIME type (used to derive the file extension)

    Returns:
        Absolute URL string to the saved file, or the original
        ``media_id`` if download failed.
    """
    import mimetypes
    import uuid

    from django.conf import settings
    from django.contrib.sites.models import Site
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    try:
        from wa.utility.apis.meta.media_api import MetaMediaAPI

        # ── Resolve credentials ───────────────────────────────────
        creds = wa_app.bsp_credentials or {}
        token = creds.get('access_token') or getattr(settings, 'META_PERM_TOKEN', None)
        if not token:
            logger.warning("[_download_and_save_meta_media] No META token for wa_app %s", wa_app.pk)
            return media_id

        phone_number_id = wa_app.phone_number_id or ''

        api = MetaMediaAPI(token=token, phone_number_id=phone_number_id)

        # 1. Get temporary download URL
        media_info = api.get_media_url(media_id)
        download_url = media_info.get('url')
        if not download_url:
            logger.warning("[_download_and_save_meta_media] get_media_url returned no url for %s", media_id)
            return media_id

        # Use MIME from API response if we didn't get one from the webhook
        if not mime_type:
            mime_type = media_info.get('mime_type', 'application/octet-stream')

        # 2. Download binary content
        content_bytes = api.download_media(download_url)
        if not content_bytes:
            logger.warning("[_download_and_save_meta_media] download_media returned empty for %s", media_id)
            return media_id

        # 3. Determine file extension from MIME type
        # Strip codec params (e.g. "audio/ogg; codecs=opus" → "audio/ogg")
        clean_mime = mime_type.split(';')[0].strip().lower()
        ext = mimetypes.guess_extension(clean_mime) or ''
        # mimetypes sometimes returns .jpe for image/jpeg — normalise
        if ext in ('.jpe', '.jpeg'):
            ext = '.jpg'

        # 4. Build storage path
        tenant_id = wa_app.tenant_id or 'unknown'
        filename = f"{uuid.uuid4().hex}{ext}"
        storage_path = f"incoming_media/{tenant_id}/{filename}"

        # 5. Save to default storage
        saved_path = default_storage.save(storage_path, ContentFile(content_bytes))

        # 6. Build absolute URL
        relative_url = default_storage.url(saved_path)
        if relative_url.startswith('http'):
            absolute_url = relative_url
        else:
            try:
                domain = Site.objects.get(id=1).domain
                absolute_url = f"https://{domain}{relative_url}"
            except Exception:
                base = getattr(settings, 'BASE_URL', '')
                absolute_url = f"{base}{relative_url}" if base else relative_url

        logger.debug(
            f"[_download_and_save_meta_media] Saved {len(content_bytes):,} bytes "
            f"({clean_mime}) -> {saved_path}"
        )
        return absolute_url

    except Exception as exc:
        logger.error("[_download_and_save_meta_media] Failed for media_id=%s: %s", media_id, exc)
        return media_id


def _parse_meta_message_payload(payload: dict, wa_app=None) -> dict:
    """
    Parse a META Cloud API webhook payload into the same normalised
    ``extracted_data`` dict that ``_parse_gupshup_message_payload`` produces.

    When *wa_app* is provided and the message contains media, the function
    downloads the binary via ``MetaMediaAPI`` and saves it to local storage
    so the resulting URL is persistent (META webhook media IDs expire after
    7 days, temporary URLs after 5 minutes).
    
    META payload structure::
    
        {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "<waba_id>",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": { "phone_number_id": "..." },
                        "contacts": [{ "wa_id": "...", "profile": { "name": "..." } }],
                        "messages": [{
                            "id": "wamid.xxx",
                            "from": "91...",
                            "timestamp": "...",
                            "type": "text",
                            "text": { "body": "Hi" },
                            ...
                        }]
                    }
                }]
            }]
        }
    """
    extracted_data = {}
    
    entry = payload.get('entry', [])
    if not entry:
        raise ValueError("META payload has no 'entry' array")
    
    # WABA ID for app identification
    extracted_data['waba_id'] = str(entry[0].get('id', ''))
    
    changes = entry[0].get('changes', [])
    if not changes:
        raise ValueError("META payload has no 'changes' array")
    
    value = changes[0].get('value', {})
    
    # Extract phone_number_id from metadata
    metadata = value.get('metadata', {})
    extracted_data['phone_number_id'] = metadata.get('phone_number_id')
    
    # Extract contact information
    contacts = value.get('contacts', [])
    if contacts:
        contact = contacts[0]
        contact_phone = contact.get('wa_id', '')
        extracted_data['contact_phone'] = f"+{contact_phone}" if contact_phone else ''
        profile = contact.get('profile', {})
        extracted_data['contact_name'] = profile.get('name', '')
    
    # Extract message data
    messages = value.get('messages', [])
    if not messages:
        raise ValueError("META payload has no messages in value")
    
    message = messages[0]
    extracted_data['message_id'] = message.get('id')
    extracted_data['message_type'] = message.get('type')
    
    # Fallback phone from message.from
    if not extracted_data.get('contact_phone'):
        from_phone = message.get('from', '')
        extracted_data['contact_phone'] = f"+{from_phone}" if from_phone else ''
    
    # Convert Unix timestamp to ISO string
    timestamp_str = message.get('timestamp')
    if timestamp_str:
        try:
            ts = int(timestamp_str)
            extracted_data['message_actual_time'] = timezone.datetime.fromtimestamp(
                ts, tz=timezone.get_current_timezone()
            ).isoformat()
        except (ValueError, TypeError):
            pass
    
    # Extract content based on type — same structure as Gupshup since
    # both use the META Cloud API message format.
    msg_type = extracted_data['message_type']
    
    # Helper to resolve media: download+save if wa_app available,
    # otherwise fall back to whatever the payload gives us.
    def _resolve_media(media_obj: dict) -> str:
        """Return a persistent URL for the media, or the raw id as fallback."""
        # If the payload already contains a URL (e.g. Gupshup-proxied), use it.
        if media_obj.get('url'):
            return media_obj['url']
        media_id = media_obj.get('id', '')
        if wa_app and media_id:
            return _download_and_save_meta_media(
                wa_app, media_id, mime_type=media_obj.get('mime_type')
            )
        return media_id
    
    if msg_type == 'text':
        text_obj = message.get('text', {})
        extracted_data['text'] = text_obj.get('body', '')
    
    elif msg_type == 'image':
        image_obj = message.get('image', {})
        extracted_data['image_link'] = _resolve_media(image_obj)
        extracted_data['text'] = image_obj.get('caption')
        extracted_data['mime_type'] = image_obj.get('mime_type')
    
    elif msg_type == 'video':
        video_obj = message.get('video', {})
        extracted_data['video_link'] = _resolve_media(video_obj)
        extracted_data['text'] = video_obj.get('caption')
        extracted_data['mime_type'] = video_obj.get('mime_type')
    
    elif msg_type == 'audio':
        audio_obj = message.get('audio', {})
        extracted_data['audio_link'] = _resolve_media(audio_obj)
        extracted_data['mime_type'] = audio_obj.get('mime_type')
    
    elif msg_type == 'document':
        doc_obj = message.get('document', {})
        extracted_data['document_link'] = _resolve_media(doc_obj)
        extracted_data['text'] = doc_obj.get('caption')
        extracted_data['mime_type'] = doc_obj.get('mime_type')
        if doc_obj.get('filename'):
            extracted_data['file_name'] = doc_obj['filename']
            if not extracted_data.get('text'):
                extracted_data['text'] = doc_obj['filename']
    
    elif msg_type == 'interactive':
        interactive_obj = message.get('interactive', {})
        interactive_type = interactive_obj.get('type')
        if interactive_type == 'button_reply':
            btn = interactive_obj.get('button_reply', {})
            extracted_data['text'] = f"[Button: {btn.get('title', '')}]"
            extracted_data['button_id'] = btn.get('id')
            extracted_data['button_title'] = btn.get('title')
        elif interactive_type == 'list_reply':
            lst = interactive_obj.get('list_reply', {})
            extracted_data['text'] = f"[List: {lst.get('title', '')}]"
            extracted_data['button_id'] = lst.get('id')
            extracted_data['button_title'] = lst.get('title')
    
    elif msg_type == 'button':
        button_obj = message.get('button', {})
        extracted_data['text'] = f"[Button: {button_obj.get('text', '')}]"
        extracted_data['button_id'] = button_obj.get('payload')
        extracted_data['button_title'] = button_obj.get('text')
    
    elif msg_type == 'location':
        loc = message.get('location', {})
        extracted_data['text'] = f"[Location: {loc.get('name', loc.get('address', 'Shared location'))}]"
    
    elif msg_type == 'contacts':
        extracted_data['text'] = '[Contact card]'
    
    elif msg_type == 'sticker':
        sticker_obj = message.get('sticker', {})
        extracted_data['image_link'] = _resolve_media(sticker_obj)
        extracted_data['mime_type'] = sticker_obj.get('mime_type')
        # Treat stickers as images for team_inbox rendering
        extracted_data['message_type'] = 'image'

    elif msg_type == 'order':
        # BE-18: Incoming order from customer (catalog order)
        order_data = message.get('order', {})
        extracted_data['message_type'] = 'order'
        extracted_data['text'] = order_data.get('text', '')
        extracted_data['order'] = order_data

    else:
        extracted_data['text'] = f"[{msg_type} message]"
    
    return extracted_data


def _build_team_inbox_content(extracted_data: dict, instance) -> dict:
    """
    Build the ``content`` JSONField value for a team_inbox ``Messages`` entry
    from the normalised ``extracted_data`` dict.
    
    This is shared between Gupshup and META paths.
    """
    content = {}
    
    if extracted_data.get('image_link'):
        content['type'] = 'image'
        content['image'] = {'url': extracted_data['image_link']}
        if extracted_data.get('mime_type'):
            content['image']['mime_type'] = extracted_data['mime_type']
        if extracted_data.get('text'):
            content['image']['caption'] = extracted_data['text']
    elif extracted_data.get('video_link'):
        content['type'] = 'video'
        content['video'] = {'url': extracted_data['video_link']}
        if extracted_data.get('mime_type'):
            content['video']['mime_type'] = extracted_data['mime_type']
        if extracted_data.get('text'):
            content['video']['caption'] = extracted_data['text']
    elif extracted_data.get('document_link'):
        content['type'] = 'document'
        content['document'] = {'url': extracted_data['document_link']}
        if extracted_data.get('mime_type'):
            content['document']['mime_type'] = extracted_data['mime_type']
        if extracted_data.get('text'):
            content['document']['caption'] = extracted_data['text']
        if extracted_data.get('file_name'):
            content['document']['filename'] = extracted_data['file_name']
    elif extracted_data.get('audio_link'):
        content['type'] = 'audio'
        content['audio'] = {'url': extracted_data['audio_link']}
        if extracted_data.get('mime_type'):
            content['audio']['mime_type'] = extracted_data['mime_type']
    elif extracted_data.get('button_title'):
        content['type'] = 'button_reply'
        content['body'] = {'text': extracted_data['button_title']}
        if extracted_data.get('button_id'):
            content['button_id'] = extracted_data['button_id']
    elif extracted_data.get('message_type') == 'order':
        # BE-18: Incoming order from customer (catalog order)
        content['type'] = 'order'
        order_data = extracted_data.get('order', {})
        content['order'] = order_data
        catalog_id = order_data.get('catalog_id', '')
        product_items = order_data.get('product_items', [])
        item_count = len(product_items)
        content['body'] = {
            'text': extracted_data.get('text') or f"Order with {item_count} item(s) from catalog {catalog_id}"
        }
    elif extracted_data.get('text'):
        content['type'] = 'text'
        content['body'] = {'text': extracted_data['text']}
    else:
        content['type'] = 'text'
        content['body'] = {'text': ''}
    
    # Add metadata for traceability
    content['_meta'] = {
        'webhook_event_id': str(instance.pk),
        'wa_message_id': extracted_data.get('message_id'),
    }
    
    return content


# ── META template webhook processor ──────────────────────────────────────────

def _process_meta_template_webhook(instance, payload: dict):
    """
    Process a META Cloud API template status/category webhook.

    META payload examples::

        message_template_status_update:
        {
            "entry": [{"id": "<WABA_ID>", "changes": [{"value": {
                "event": "APPROVED",
                "message_template_id": 12345,
                "message_template_name": "my_template",
                "message_template_language": "en_US",
                "reason": null
            }, "field": "message_template_status_update"}]}]
        }

        template_category_update:
        {
            "entry": [{"id": "<WABA_ID>", "changes": [{"value": {
                "message_template_id": 12345,
                "message_template_name": "my_template",
                "new_category": "UTILITY",
                "previous_category": "MARKETING"
            }, "field": "template_category_update"}]}]
        }
    """
    from tenants.models import BSPChoices, TenantWAApp
    from wa.models import TemplateCategory, TemplateStatus, WATemplate
    from wa.services.template_notifications import TemplateNotificationService

    # ── Parse payload ─────────────────────────────────────────────────
    try:
        entry = payload.get("entry", [{}])[0]
        waba_id = str(entry.get("id", ""))
        change = entry.get("changes", [{}])[0]
        field = change.get("field", "")
        value = change.get("value", {})
    except (IndexError, KeyError, TypeError):
        instance.error_message = "META template webhook: malformed payload"
        instance.is_processed = True
        instance.save(update_fields=["error_message", "is_processed"])
        return

    # ── Resolve WAApp by waba_id ──────────────────────────────────────
    if not instance.wa_app_id:
        try:
            wa_app = TenantWAApp.objects.get(waba_id=waba_id, bsp=BSPChoices.META)
            instance.wa_app = wa_app
        except TenantWAApp.DoesNotExist:
            instance.error_message = f"No META WAApp with waba_id={waba_id}"
            instance.is_processed = True
            instance.save(update_fields=["error_message", "is_processed"])
            return

    wa_app = instance.wa_app
    instance.is_processed = True
    instance.save(update_fields=["wa_app", "is_processed"])

    # ── Extract common fields from value ──────────────────────────────
    meta_template_id = str(value.get("message_template_id", "")) or None
    template_name = value.get("message_template_name")
    language_code = value.get("message_template_language") or "en"
    event = value.get("event", "").upper()
    reason = value.get("reason")
    if reason and reason.upper() == "NONE":
        reason = None

    # ── Helper: find the local WATemplate ─────────────────────────────
    def _find_template():
        if meta_template_id:
            t = WATemplate.objects.filter(
                meta_template_id=meta_template_id, wa_app=wa_app
            ).first()
            if t:
                return t
        if template_name:
            return WATemplate.objects.filter(
                element_name=template_name,
                language_code=language_code,
                wa_app=wa_app,
            ).first()
        return None

    status_map = {
        "APPROVED": TemplateStatus.APPROVED,
        "REJECTED": TemplateStatus.REJECTED,
        "FAILED": TemplateStatus.REJECTED,
        "PENDING": TemplateStatus.PENDING,
        "DISABLED": TemplateStatus.DISABLED,
        "PAUSED": TemplateStatus.PAUSED,
    }
    category_map = {
        "MARKETING": TemplateCategory.MARKETING,
        "UTILITY": TemplateCategory.UTILITY,
        "AUTHENTICATION": TemplateCategory.AUTHENTICATION,
    }

    try:
        if field == "template_category_update":
            # ── Category change ───────────────────────────────────────
            new_category_str = (value.get("new_category") or "").upper()
            template = _find_template()
            if template and new_category_str:
                old_category = template.category
                old_status = template.status
                template.category = category_map.get(new_category_str, template.category)
                template.status = TemplateStatus.PENDING
                template.save(update_fields=["category", "status"])
                logger.info(
                    "META template %s category: %s→%s, status→PENDING",
                    template.element_name, old_category, new_category_str,
                )
                TemplateNotificationService.send_category_change_notification(
                    template=template,
                    old_category=old_category,
                    new_category=new_category_str,
                )
            else:
                logger.warning(
                    "META template webhook: template not found "
                    "(meta_id=%s name=%s) for category update",
                    meta_template_id, template_name,
                )

        elif field == "message_template_status_update" and event:
            # ── Status change ─────────────────────────────────────────
            template = _find_template()
            new_status = status_map.get(event)

            if template and new_status:
                old_status = template.status
                template.status = new_status
                update_fields = ["status"]
                if reason:
                    template.error_message = reason
                    update_fields.append("error_message")
                template.save(update_fields=update_fields)
                logger.info(
                    "META template %s status: %s→%s",
                    template.element_name, old_status, new_status,
                )
                if event in ("APPROVED", "REJECTED", "FAILED", "DISABLED"):
                    TemplateNotificationService.send_status_change_notification(
                        template=template,
                        old_status=old_status,
                        new_status=event,
                        reason=reason,
                    )
                    try:
                        from notifications.signals import \
                            create_template_notification
                        create_template_notification(template, old_status, event, reason)
                    except Exception:
                        logger.exception('Failed to create in-app template notification')
            elif not template:
                # Auto-create stub for template not found locally
                new_status = status_map.get(event, TemplateStatus.PENDING)
                cat_str = (value.get("message_template_category") or "").upper()
                category = category_map.get(cat_str, TemplateCategory.MARKETING)
                safe_name = template_name or f"unknown_{meta_template_id or pk}"

                template = WATemplate.objects.create(
                    wa_app=wa_app,
                    name=safe_name,
                    element_name=safe_name,
                    language_code=language_code,
                    meta_template_id=meta_template_id,
                    status=new_status,
                    category=category,
                    error_message=reason,
                    needs_sync=True,
                )
                logger.info(
                    "META auto-created template '%s' (meta_id=%s) status=%s",
                    safe_name, meta_template_id, new_status,
                )
                if event in ("APPROVED", "REJECTED", "FAILED", "DISABLED"):
                    TemplateNotificationService.send_status_change_notification(
                        template=template,
                        old_status=None,
                        new_status=event,
                        reason=reason,
                    )
            else:
                logger.warning(
                    "META template webhook: unknown event '%s' for %s",
                    event, template.element_name,
                )
        else:
            logger.info(
                "META template webhook: unhandled field=%s event=%s",
                field, event,
            )
    except Exception as e:
        logger.error("Error processing META template webhook: %s", str(e))


@shared_task    
def process_template_webhook(pk: int):
    """
    Process template webhooks from Gupshup **or** META Direct.
    
    BSP-aware: routes to Gupshup or META parsing based on
    ``instance.bsp``.  Both paths normalise data and update the
    ``WATemplate`` record identically.
    
    Handles two types of webhooks:
    1. message_template_status_update - Template approval/rejection status changes
    2. template_category_update - Template category changes (affects pricing)
    
    Sends email notifications to tenant users for important changes.
    
    Args:
        pk: UUID string of the WAWebhookEvent instance
    """
    try:
        from tenants.models import BSPChoices, TenantWAApp
        from wa.models import TemplateStatus, WATemplate, WAWebhookEvent
        from wa.services.template_notifications import \
            TemplateNotificationService
        
        instance: WAWebhookEvent = WAWebhookEvent.objects.get(pk=pk)
        
        # Only process TEMPLATE type events
        if instance.event_type != 'TEMPLATE':
            logger.debug("Skipping non-TEMPLATE event %s", instance.pk)
            return
        
        payload = instance.payload
        bsp = getattr(instance, 'bsp', None)

        if bsp == BSPChoices.META:
            _process_meta_template_webhook(instance, payload)
            return
        
        # ── Gupshup path (legacy) ────────────────────────────────────
        from wa.utility.data_model.gupshup.template_input import TemplateInput
        
        try:
            processed_data: TemplateInput = TemplateInput.from_webhook_payload(payload)
            wa_app = TenantWAApp.objects.get(app_id=processed_data.gs_app_id)
            instance.wa_app = wa_app
            instance.is_processed = True
            instance.save(update_fields=['wa_app', 'is_processed'])
            
            # Handle template category update - update the template's category
            if processed_data.event == "CATEGORY_UPDATE" and processed_data.gs_template_id:
                try:
                    # Look up by bsp_template_id first, then fall back to name+language
                    template = WATemplate.objects.filter(
                        bsp_template_id=processed_data.gs_template_id,
                        wa_app=wa_app
                    ).first()
                    if not template and processed_data.message_template_name:
                        template = WATemplate.objects.filter(
                            element_name=processed_data.message_template_name,
                            language_code=processed_data.message_template_language or "en",
                            wa_app=wa_app
                        ).first()
                    
                    if template and processed_data.new_category:
                        old_category = template.category
                        old_status = template.status
                        template.category = processed_data.new_category
                        template.status = TemplateStatus.PENDING
                        template.save(update_fields=['category', 'status'])
                        logger.debug(f"Updated template {template.element_name} category: {old_category} -> {processed_data.new_category}, status: {old_status} -> PENDING")
                        
                        # Send email notification about category change
                        TemplateNotificationService.send_category_change_notification(
                            template=template,
                            old_category=old_category,
                            new_category=processed_data.new_category
                        )
                    else:
                        logger.warning("Template not found for gs_template_id: %s", processed_data.gs_template_id)
                except Exception as e:
                    logger.error("Error updating template category: %s", str(e))
            
            # Handle template status update - update the template's status
            elif processed_data.event and processed_data.gs_template_id:
                try:
                    # Look up by bsp_template_id first, then fall back to name+language
                    template = WATemplate.objects.filter(
                        bsp_template_id=processed_data.gs_template_id,
                        wa_app=wa_app
                    ).first()
                    if not template and processed_data.message_template_name:
                        template = WATemplate.objects.filter(
                            element_name=processed_data.message_template_name,
                            language_code=processed_data.message_template_language or "en",
                            wa_app=wa_app
                        ).first()
                    
                    if template:
                        old_status = template.status
                        status_map = {
                            'APPROVED': TemplateStatus.APPROVED,
                            'REJECTED': TemplateStatus.REJECTED,
                            'FAILED': TemplateStatus.REJECTED,   # GS FAILED → REJECTED (no FAILED in our enum)
                            'PENDING': TemplateStatus.PENDING,
                            'DISABLED': TemplateStatus.DISABLED,
                            'PAUSED': TemplateStatus.PAUSED,
                        }
                        new_status = status_map.get(processed_data.event.upper())
                        if new_status:
                            template.status = new_status
                            if processed_data.reason:
                                template.error_message = processed_data.reason
                                template.save(update_fields=['status', 'error_message'])
                            else:
                                template.save(update_fields=['status'])
                            logger.debug("Updated template %s status to %s", template.element_name, new_status)
                            
                            # Send email notification about status change (only for important statuses)
                            if processed_data.event.upper() in ['APPROVED', 'REJECTED', 'FAILED', 'DISABLED']:
                                TemplateNotificationService.send_status_change_notification(
                                    template=template,
                                    old_status=old_status,
                                    new_status=processed_data.event.upper(),
                                    reason=processed_data.reason
                                )
                        else:
                            logger.warning(
                                f"Unknown webhook event '{processed_data.event}' "
                                f"for template {template.element_name} -- no status mapping"
                            )
                    else:
                        # Template not found locally — auto-create a stub record.
                        # This handles templates created directly on Gupshup/META
                        # dashboard or deleted locally before the webhook arrived.
                        status_map = {
                            'APPROVED': TemplateStatus.APPROVED,
                            'REJECTED': TemplateStatus.REJECTED,
                            'FAILED': TemplateStatus.REJECTED,
                            'PENDING': TemplateStatus.PENDING,
                            'DISABLED': TemplateStatus.DISABLED,
                            'PAUSED': TemplateStatus.PAUSED,
                        }
                        new_status = status_map.get(
                            processed_data.event.upper(), TemplateStatus.PENDING
                        )
                        
                        # Map webhook category to TemplateCategory
                        from wa.models import TemplateCategory
                        category_map = {
                            'MARKETING': TemplateCategory.MARKETING,
                            'UTILITY': TemplateCategory.UTILITY,
                            'AUTHENTICATION': TemplateCategory.AUTHENTICATION,
                        }
                        category = category_map.get(
                            (processed_data.message_template_category or '').upper(),
                            TemplateCategory.MARKETING
                        )
                        
                        template_name = processed_data.message_template_name or f"unknown_{processed_data.gs_template_id[:8]}"
                        lang_code = processed_data.message_template_language or "en"
                        
                        template = WATemplate.objects.create(
                            wa_app=wa_app,
                            name=template_name,
                            element_name=template_name,
                            language_code=lang_code,
                            bsp_template_id=processed_data.gs_template_id,
                            meta_template_id=str(processed_data.message_template_id) if processed_data.message_template_id else None,
                            status=new_status,
                            category=category,
                            error_message=processed_data.reason if processed_data.reason and processed_data.reason != "NONE" else None,
                            needs_sync=True,
                        )
                        logger.debug(
                            f"Auto-created template '{template.element_name}' "
                            f"(bsp_id={processed_data.gs_template_id}) "
                            f"with status={new_status}, category={category}"
                        )
                        
                        # Send email notification for the new template
                        if processed_data.event.upper() in ['APPROVED', 'REJECTED', 'FAILED', 'DISABLED']:
                            TemplateNotificationService.send_status_change_notification(
                                template=template,
                                old_status=None,
                                new_status=processed_data.event.upper(),
                                reason=processed_data.reason
                            )
                except Exception as e:
                    logger.error("Error updating template status: %s", str(e))
                    
        except Exception as e:
            instance.error_message = f"Failed to parse template input: {str(e)}"
            instance.is_processed = True
            instance.save()
            
    except WAWebhookEvent.DoesNotExist:
        raise Exception(f"WAWebhookEvent with pk={pk} does not exist.")
    except Exception as e:
        raise Exception(f"Failed to process template webhook: {str(e)}")


@shared_task
def send_outgoing_message(pk: str):
    """
    Celery task to send an outgoing WhatsApp session message via API.
    
    Args:
        pk: UUID string of the WAMessage instance
        
    Updates the message status to:
        - SENT: If API returns 200
        - FAILED: If API returns error or exception occurs
        
    Also creates a Messages entry in team_inbox after sending (regardless of status).
    
    Returns:
        dict: Status of the operation with keys:
            - status: 'sent', 'failed', 'skipped', 'error'
            - message_id: BSP message ID (if sent)
            - team_inbox_created: bool
            - team_inbox_message_id: int (if created)
            - team_inbox_error: str (if failed to create)
            - error: str (if any error occurred)
    """
    from django.conf import settings

    from contacts.models import TenantContact
    from team_inbox.models import (AuthorChoices, MessageDirectionChoices,
                                   MessageEventIds, MessagePlatformChoices,
                                   Messages)
    from tenants.models import BSPChoices
    from wa.models import MessageStatus, WAMessage
    
    result = {
        'outgoing_message_pk': str(pk),
        'status': 'unknown',
        'message_id': None,
        'team_inbox_created': False,
        'team_inbox_message_id': None,
        'team_inbox_error': None,
        'error': None
    }
    
    try:
        instance = WAMessage.objects.select_related('wa_app', 'wa_app__tenant').get(pk=pk)
        
        # Skip if already sent or failed
        if instance.status in [MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.READ]:
            logger.debug("Message %s already sent, skipping", pk)
            result['status'] = 'skipped'
            result['error'] = f"Message already in status: {instance.status}"
            return result
        
        # Update status to SENT (as sending indicator)
        instance.status = MessageStatus.SENT
        instance.save(update_fields=['status'])
        
        # Get WA app credentials
        wa_app = instance.wa_app
        if not wa_app:
            raise Exception("No WA app associated with this message")
        
        # Initialize the Session Message API (BSP-aware)
        bsp = getattr(wa_app, 'bsp', None)

        if bsp == BSPChoices.META:
            from wa.utility.apis.meta.session_message_api import \
                SessionMessageAPI as MetaSessionMessageAPI

            creds = wa_app.bsp_credentials or {}
            token = creds.get('access_token') or getattr(
                settings, 'META_PERM_TOKEN', None
            )
            if not token:
                raise Exception(
                    "META access token not configured. Set "
                    "bsp_credentials.access_token on the WAApp or "
                    "META_PERM_TOKEN in settings."
                )
            phone_number_id = wa_app.phone_number_id
            if not phone_number_id:
                raise Exception(
                    "phone_number_id not configured on the WAApp. "
                    "Required for META Cloud API message sending."
                )
            api = MetaSessionMessageAPI(
                token=token,
                phone_number_id=phone_number_id,
            )
        else:
            # Default: Gupshup (covers BSPChoices.GUPSHUP and legacy apps)
            from wa.utility.apis.gupshup.session_message_api import \
                SessionMessageAPI as GupshupSessionMessageAPI

            if not wa_app.app_id or not wa_app.app_secret:
                raise Exception(
                    f"Gupshup credentials (app_id/app_secret) missing "
                    f"on WAApp {wa_app.pk}"
                )
            api = GupshupSessionMessageAPI(
                appId=wa_app.app_id,
                token=wa_app.app_secret,
            )

        # Send the message
        payload = instance.raw_payload
        if not payload:
            raise Exception("Message payload is empty")
        
        logger.debug("Sending message %s with payload: %s", pk, payload)
        
        try:
            response = api.send_message(payload)
            logger.debug("API response for message %s: %s", pk, response)
            
            # If we reach here, API returned 200/201 (success)
            instance.status = MessageStatus.SENT
            
            # Extract message_id from response.
            # Cloud API (META Direct) returns: {"messages": [{"id": "wamid.XXX"}]}
            # Gupshup v3 Partner API returns: {"messageId": "UUID", ...}
            # We may get both when Gupshup wraps Cloud API — store each
            # in the appropriate field so status webhook lookups work
            # regardless of which ID the webhook carries.
            cloud_api_id = None
            messages = response.get('messages', [])
            if messages and len(messages) > 0:
                cloud_api_id = messages[0].get('id')
            
            gupshup_id = (
                response.get('gs_id') or
                response.get('messageId') or
                response.get('message_id')
            )

            # Primary: prefer Cloud API id (wamid) since that's what
            # webhooks normally carry in the 'id' field.
            # If only the Gupshup UUID is available, use that as primary.
            primary_id = cloud_api_id or gupshup_id or response.get('id')
            secondary_id = gupshup_id if (cloud_api_id and gupshup_id and cloud_api_id != gupshup_id) else None

            instance.wa_message_id = primary_id
            if secondary_id:
                instance.gs_message_id = secondary_id

            save_fields = ['status', 'wa_message_id', 'sent_at']
            if secondary_id:
                save_fields.append('gs_message_id')

            instance.sent_at = timezone.now()
            instance.save(update_fields=save_fields)
            logger.debug(f"Message {pk} sent successfully with wa_message_id: {instance.wa_message_id}, gs_message_id: {instance.gs_message_id}")
            result['status'] = 'sent'
            result['message_id'] = instance.wa_message_id

            # ── BE-10: Create WAOrder if this is an order_details message ──
            try:
                raw = instance.raw_payload or {}
                interactive = raw.get('interactive', {})
                if interactive.get('type') == 'order_details':
                    _create_wa_order_from_message(instance)
            except Exception as order_err:
                # Order creation failure must NOT break message sending
                logger.warning(
                    "Failed to create WAOrder for message %s: %s",
                    pk, order_err,
                )

        except Exception as api_error:
            # API returned 400/500 or other error
            error_message = str(api_error)
            instance.status = MessageStatus.FAILED
            instance.failed_at = timezone.now()
            instance.error_message = error_message
            instance.save(update_fields=['status', 'error_message', 'failed_at'])
            logger.error("Message %s failed: %s", pk, error_message)
            result['status'] = 'failed'
            result['error'] = error_message
        
        # Create Messages entry in team_inbox (regardless of send status)
        team_inbox_result = _create_team_inbox_message_v2(instance, wa_app)
        result['team_inbox_created'] = team_inbox_result.get('created', False)
        result['team_inbox_message_id'] = team_inbox_result.get('message_id')
        
        # Broadcast status update via WebSocket
        _broadcast_message_status_update_v2(instance, result['status'])
        result['team_inbox_error'] = team_inbox_result.get('error')
        
        return result
            
    except WAMessage.DoesNotExist:
        result['status'] = 'error'
        result['error'] = f"WAMessage with pk={pk} does not exist"
        return result
    except Exception as e:
        logger.error("Error sending message %s: %s", pk, str(e))
        result['status'] = 'error'
        result['error'] = str(e)
        
        try:
            instance = WAMessage.objects.get(pk=pk)
            instance.status = MessageStatus.FAILED
            instance.failed_at = timezone.now()
            instance.error_message = str(e)
            instance.save(update_fields=['status', 'error_message', 'failed_at'])
            
            # Still create Messages entry even on failure
            wa_app = instance.wa_app
            if wa_app:
                team_inbox_result = _create_team_inbox_message_v2(instance, wa_app)
                result['team_inbox_created'] = team_inbox_result.get('created', False)
                result['team_inbox_message_id'] = team_inbox_result.get('message_id')
                result['team_inbox_error'] = team_inbox_result.get('error')
        except Exception as inner_e:
            result['team_inbox_error'] = str(inner_e)
        
        return result


def _broadcast_message_status_update_v2(instance, status):
    """
    Broadcast message status update via WebSocket for WAMessage (BSP-agnostic).
    
    Uses ``instance.wa_app.tenant`` to determine the channel room,
    so this works for both Gupshup and META Direct messages.
    
    Args:
        instance: WAMessage instance (must have wa_app with tenant)
        status: Status string ('sent', 'delivered', 'read', 'failed')
    """
    import json

    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from django.core.serializers.json import DjangoJSONEncoder

    from team_inbox.models import Messages
    
    try:
        # Get tenant via wa_app (BSP-agnostic)
        wa_app = instance.wa_app
        if not wa_app or not wa_app.tenant:
            logger.warning("[_broadcast_message_status_update_v2] No tenant for WAMessage %s", instance.pk)
            return
        
        tenant_id = wa_app.tenant.pk
        contact_id = None
        team_inbox_message_id = None
        message_event_id = None
        
        # Find the team_inbox Message linked to this WAMessage
        team_inbox_msg = (
            Messages.objects
            .select_related('message_id', 'contact')
            .filter(outgoing_message=instance)
            .first()
        )
        if team_inbox_msg:
            team_inbox_message_id = team_inbox_msg.pk
            if team_inbox_msg.message_id:
                message_event_id = team_inbox_msg.message_id.pk
            if team_inbox_msg.contact:
                contact_id = team_inbox_msg.contact.pk
        
        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning("[_broadcast_message_status_update_v2] Channel layer not available")
            return
        
        room_group_name = f"team_inbox_{tenant_id}"
        
        def format_dt(dt):
            return dt.isoformat() if dt else None
        
        # Build the event payload and pre-serialize through DjangoJSONEncoder
        # to convert UUID, datetime, and other non-primitive types to strings.
        # channels-redis uses msgpack which cannot serialize these types.
        event_data = {
            'outgoing_message_id': instance.pk,
            'id': team_inbox_message_id,
            'message_id': message_event_id,
            'contact_id': contact_id,
            'status': status,
            'outgoing_status': status,
            'sent_at': format_dt(instance.sent_at),
            'delivered_at': format_dt(instance.delivered_at),
            'read_at': format_dt(instance.read_at),
            'failed_at': format_dt(instance.failed_at),
            'outgoing_sent_at': format_dt(instance.sent_at),
            'outgoing_delivered_at': format_dt(instance.delivered_at),
            'outgoing_read_at': format_dt(instance.read_at),
            'outgoing_failed_at': format_dt(instance.failed_at),
        }
        safe_data = json.loads(json.dumps(event_data, cls=DjangoJSONEncoder))
        
        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                'type': 'message_status_update',
                **safe_data,
            }
        )
        
        logger.debug(f"[_broadcast_message_status_update_v2] Broadcasted status '{status}' for WAMessage {instance.pk} to {room_group_name}")
        
    except Exception as e:
        logger.error("[_broadcast_message_status_update_v2] Error: %s", str(e))


# ── BE-17: WebSocket broadcast — payment_status_update ───────────────────

def _broadcast_payment_status_update(wa_order):
    """
    Broadcast a payment_status_update event via WebSocket for a WAOrder.

    Follows the same pattern as ``_broadcast_message_status_update_v2``.
    The FE listens for ``payment_status_update`` to live-update order cards.
    """
    import json

    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from django.core.serializers.json import DjangoJSONEncoder

    try:
        tenant_id = wa_order.tenant_id
        if not tenant_id:
            logger.warning("[_broadcast_payment_status_update] No tenant for WAOrder %s", wa_order.pk)
            return

        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning("[_broadcast_payment_status_update] Channel layer not available")
            return

        room_group_name = f"team_inbox_{tenant_id}"

        def format_dt(dt):
            return dt.isoformat() if dt else None

        event_data = {
            "event_type": "payment_status_update",
            "reference_id": wa_order.reference_id,
            "order_id": str(wa_order.pk),
            "order_status": wa_order.order_status,
            "payment_status": wa_order.payment_status,
            "transaction_id": wa_order.transaction_id or "",
            "pg_transaction_id": wa_order.pg_transaction_id or "",
            "payment_captured_at": format_dt(wa_order.payment_captured_at),
            "contact_id": wa_order.contact_id,
        }
        safe_data = json.loads(json.dumps(event_data, cls=DjangoJSONEncoder))

        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                "type": "payment_status_update",
                **safe_data,
            },
        )

        logger.debug(
            "[_broadcast_payment_status_update] Broadcasted payment_status=%s for order %s to %s",
            wa_order.payment_status, wa_order.pk, room_group_name,
        )

    except Exception as e:
        logger.error("[_broadcast_payment_status_update] Error: %s", str(e))


# ── BE-10: WAOrder creation from outgoing order_details message ──────────

def _create_wa_order_from_message(wa_message):
    """
    Create a WAOrder record from a successfully-sent order_details WAMessage.

    Extracts order data from the message's raw_payload.interactive.action.parameters
    and persists it to the WAOrder table. Safe to call multiple times — uses
    get_or_create keyed on (tenant, reference_id).
    """
    from wa.models import WAOrder

    raw = wa_message.raw_payload or {}
    params = (
        raw.get('interactive', {})
        .get('action', {})
        .get('parameters', {})
    )
    if not params:
        logger.warning(
            "[_create_wa_order_from_message] No parameters in raw_payload for %s",
            wa_message.pk,
        )
        return None

    reference_id = params.get('reference_id', '')
    if not reference_id:
        logger.warning(
            "[_create_wa_order_from_message] Missing reference_id for %s",
            wa_message.pk,
        )
        return None

    wa_app = wa_message.wa_app
    tenant = wa_app.tenant if wa_app else None

    if not tenant:
        logger.warning(
            "[_create_wa_order_from_message] No tenant resolved for %s",
            wa_message.pk,
        )
        return None

    # Parse order type
    order_type = params.get('type', 'digital-goods')

    # Parse financial fields (amounts are in offset units — e.g. paisa)
    order_obj = params.get('order', {})
    items = order_obj.get('items', [])
    subtotal = order_obj.get('subtotal', {}).get('value', 0)
    tax = order_obj.get('tax', {}).get('value', 0)
    shipping = order_obj.get('shipping', {}).get('value', 0)
    discount = order_obj.get('discount', {}).get('value', 0)

    # Calculate total — sum of subtotal + tax + shipping - discount
    total = subtotal + tax + shipping - discount

    payment_config = params.get('payment_configuration', '')

    wa_order, created = WAOrder.objects.get_or_create(
        tenant=tenant,
        reference_id=reference_id,
        defaults={
            'wa_app': wa_app,
            'contact': wa_message.contact,
            'outgoing_message': wa_message,
            'order_type': order_type,
            'total_amount': total,
            'subtotal': subtotal,
            'tax': tax,
            'shipping': shipping,
            'discount': discount,
            'items': items,
            'currency': params.get('currency', 'INR'),
            'payment_gateway': getattr(wa_app, 'payment_gateway', '') or '',
            'configuration_name': payment_config,
            'order_details_payload': params,
        },
    )

    if created:
        logger.info(
            "[_create_wa_order_from_message] Created WAOrder %s (ref=%s) for message %s",
            wa_order.pk, reference_id, wa_message.pk,
        )
    else:
        logger.debug(
            "[_create_wa_order_from_message] WAOrder %s already exists for ref=%s",
            wa_order.pk, reference_id,
        )

    return wa_order


# ── BE-14: process_payment_webhook ──────────────────────────────────────

def process_payment_webhook(pk: str):
    """
    Process a PAYMENT-type webhook event.

    Flow:
    a. Load WAWebhookEvent
    b. Parse payment status from webhook payload
    c. Resolve tenant via phone_number_id → TenantWAApp
    d. Lookup WAOrder by (tenant, reference_id)
    e. Create WAPaymentEvent (audit trail)
    f. Update WAOrder payment_status (only if higher priority)
    g. If captured → set timestamps, optionally auto-send order_status
    h. Broadcast via WebSocket
    i. Mark WAWebhookEvent as processed
    """
    from django.utils import timezone

    from tenants.models import TenantWAApp
    from wa.models import (PAYMENT_STATUS_PRIORITY, PaymentStatus, WAOrder,
                           WAPaymentEvent, WAWebhookEvent)

    try:
        instance = WAWebhookEvent.objects.get(pk=pk)
        payload = instance.payload or {}

        # ── b. Parse payment status from Cloud API webhook ──
        # Cloud API payment webhooks:
        #   entry[0].changes[0].value.statuses[0]
        # The event is already pre-parsed into payload by the webhook view,
        # so payload = entry[0].changes[0].value
        statuses = payload.get('statuses', [])
        if not statuses:
            instance.is_processed = True
            instance.error_message = "No statuses in payment webhook"
            instance.save(update_fields=['is_processed', 'error_message'])
            logger.warning("[process_payment_webhook] No statuses in %s", pk)
            return

        status_obj = statuses[0]
        payment_obj = status_obj.get('payment', {})
        reference_id = payment_obj.get('reference_id', '')
        new_status = payment_obj.get('status', '').lower()
        transaction_id = payment_obj.get('transaction_id', '')
        pg_transaction_id = payment_obj.get('pg_transaction_id', '')
        transaction_status = payment_obj.get('transaction_status', '')
        amount_value = payment_obj.get('amount', {}).get('value', 0)
        currency = payment_obj.get('amount', {}).get('currency', 'INR')

        if not reference_id:
            instance.is_processed = True
            instance.error_message = "No reference_id in payment webhook"
            instance.save(update_fields=['is_processed', 'error_message'])
            logger.warning("[process_payment_webhook] No reference_id in %s", pk)
            return

        # ── c. Resolve tenant via phone_number_id ──
        metadata = payload.get('metadata', {})
        phone_number_id = metadata.get('phone_number_id', '')
        wa_app = None
        tenant = None
        if phone_number_id:
            try:
                wa_app = TenantWAApp.objects.select_related('tenant').get(
                    phone_number_id=phone_number_id,
                )
                tenant = wa_app.tenant
                instance.wa_app = wa_app
            except TenantWAApp.DoesNotExist:
                logger.warning(
                    "[process_payment_webhook] No TenantWAApp for phone_number_id=%s",
                    phone_number_id,
                )

        # ── d. Lookup WAOrder ──
        try:
            wa_order = WAOrder.objects.get(
                tenant=tenant, reference_id=reference_id,
            )
        except WAOrder.DoesNotExist:
            instance.is_processed = True
            instance.error_message = f"WAOrder not found: tenant={tenant}, ref={reference_id}"
            instance.save(update_fields=['is_processed', 'error_message', 'wa_app'])
            logger.warning(
                "[process_payment_webhook] WAOrder not found for ref=%s, tenant=%s",
                reference_id, tenant,
            )
            return

        # ── e. Create WAPaymentEvent (audit trail — always created) ──
        WAPaymentEvent.objects.create(
            order=wa_order,
            webhook_event=instance,
            status=new_status,
            transaction_id=transaction_id,
            pg_transaction_id=pg_transaction_id,
            transaction_status=transaction_status,
            amount_value=amount_value,
            currency=currency,
            raw_payload=payment_obj,
        )

        # ── f. Update WAOrder payment_status (priority check) ──
        current_priority = PAYMENT_STATUS_PRIORITY.get(wa_order.payment_status, 0)
        new_priority = PAYMENT_STATUS_PRIORITY.get(new_status, 0)

        update_fields = []
        if new_priority > current_priority:
            wa_order.payment_status = new_status
            update_fields.append('payment_status')

        # ── g. If captured → set timestamps, auto-send order_status ──
        if new_status == PaymentStatus.CAPTURED:
            wa_order.payment_captured_at = timezone.now()
            wa_order.transaction_id = transaction_id
            wa_order.pg_transaction_id = pg_transaction_id
            update_fields.extend([
                'payment_captured_at', 'transaction_id', 'pg_transaction_id',
            ])

            # Optional: call lookup_payment to verify (security)
            try:
                from wa.services.order_service import OrderService
                OrderService.lookup_payment(wa_order)
            except Exception as lookup_err:
                logger.warning(
                    "[process_payment_webhook] lookup_payment failed for %s: %s",
                    reference_id, lookup_err,
                )

            # Auto-send order_status if configured
            if wa_app and getattr(wa_app, 'auto_send_order_status_on_payment', False):
                try:
                    from wa.services.order_service import OrderService
                    OrderService.send_order_status(
                        wa_order,
                        new_status="processing",
                        body_text="Payment received, processing your order.",
                    )
                except Exception as auto_err:
                    logger.warning(
                        "[process_payment_webhook] auto send_order_status failed for %s: %s",
                        reference_id, auto_err,
                    )

        elif new_status == PaymentStatus.FAILED:
            # Nothing extra to do for failed — status is already updated
            pass

        if update_fields:
            # Deduplicate field names
            update_fields = list(set(update_fields))
            wa_order.save(update_fields=update_fields)

        # ── h. Broadcast payment_status_update via WebSocket ──
        try:
            _broadcast_payment_status_update(wa_order)
        except Exception as ws_err:
            logger.warning(
                "[process_payment_webhook] WebSocket broadcast failed for %s: %s",
                reference_id, ws_err,
            )

        # ── i. Mark event processed ──
        instance.is_processed = True
        instance.save(update_fields=['is_processed', 'wa_app'])
        logger.info(
            "[process_payment_webhook] Processed payment %s → status=%s for order ref=%s",
            pk, new_status, reference_id,
        )

    except Exception as exc:
        logger.error("[process_payment_webhook] Error processing %s: %s", pk, exc)
        try:
            instance = WAWebhookEvent.objects.get(pk=pk)
            instance.error_message = str(exc)[:500]
            instance.save(update_fields=['error_message'])
        except Exception:
            pass


def _create_team_inbox_message_v2(instance, wa_app):
    """
    Helper function to create a Messages entry in team_inbox for an outgoing WAMessage.
    
    Args:
        instance: WAMessage instance
        wa_app: TenantWAApp instance (from instance.wa_app)
        
    Returns:
        dict: Result with keys:
            - created: bool
            - message_id: int (if created)
            - error: str (if failed)
    """
    from contacts.models import TenantContact
    from team_inbox.models import (AuthorChoices, MessageDirectionChoices,
                                   MessageEventIds, MessagePlatformChoices,
                                   Messages)
    
    result = {
        'created': False,
        'message_id': None,
        'error': None
    }
    
    logger.debug("[_create_team_inbox_message_v2] Starting for WAMessage %s", instance.pk)
    
    try:
        payload = instance.raw_payload or {}
        
        # Extract phone number from contact FK or payload 'to' field
        phone_number = ''
        if instance.contact and instance.contact.phone:
            phone_number = str(instance.contact.phone)
        if not phone_number:
            phone_number = payload.get('to', '')
        if phone_number:
            phone_number = str(phone_number).lstrip('+')
        
        if not phone_number:
            result['error'] = "No recipient phone number"
            return result
        
        # Find the contact by phone number
        tenant = wa_app.tenant
        contact = TenantContact.objects.filter(
            tenant=tenant,
            phone=f"+{phone_number}"
        ).first()
        
        if not contact:
            # Try matching last 10 digits
            contacts = TenantContact.objects.filter(tenant=tenant)
            for c in contacts:
                if c.phone and str(c.phone).replace('+', '').endswith(phone_number[-10:]):
                    contact = c
                    break
        
        if not contact:
            result['error'] = f"No contact found for phone {phone_number}"
            return result
        
        # Create MessageEventIds entry for timeline ordering
        event_id = MessageEventIds.objects.create()
        
        # Build content from payload
        msg_type = instance.message_type or payload.get('type', 'text')
        content = {'type': msg_type.lower()}
        
        if msg_type.upper() == 'TEXT':
            text_obj = payload.get('text', {})
            content['body'] = {
                'text': text_obj.get('body', '') if isinstance(text_obj, dict) else str(text_obj)
            }
        elif msg_type.upper() == 'IMAGE':
            image_obj = payload.get('image', {})
            content['image'] = {'url': image_obj.get('link', '') or (instance.media_url or '')}
            if image_obj.get('caption'):
                content['image']['caption'] = image_obj.get('caption')
        elif msg_type.upper() == 'VIDEO':
            video_obj = payload.get('video', {})
            content['video'] = {'url': video_obj.get('link', '') or (instance.media_url or '')}
            if video_obj.get('caption'):
                content['video']['caption'] = video_obj.get('caption')
        elif msg_type.upper() == 'DOCUMENT':
            doc_obj = payload.get('document', {})
            content['document'] = {'url': doc_obj.get('link', '') or (instance.media_url or '')}
            if doc_obj.get('filename'):
                content['document']['filename'] = doc_obj.get('filename')
        elif msg_type.upper() == 'AUDIO':
            audio_obj = payload.get('audio', {})
            content['audio'] = {'url': audio_obj.get('link', '') or (instance.media_url or '')}
        elif msg_type.upper() == 'TEMPLATE':
            # Build template content from WATemplate model for proper FE rendering
            tmpl = instance.template
            if tmpl:
                # Start with the template body text
                body_text = tmpl.content or ''
                # Substitute body params if provided in the payload components
                components = payload.get('template', {}).get('components', [])
                for comp in components:
                    if comp.get('type') == 'body':
                        for i, param in enumerate(comp.get('parameters', []), start=1):
                            placeholder = '{{' + str(i) + '}}'
                            named_placeholder = '{{' + param.get('parameter_name', '') + '}}' if param.get('parameter_name') else None
                            value = param.get('text', '')
                            if named_placeholder:
                                body_text = body_text.replace(named_placeholder, value)
                            body_text = body_text.replace(placeholder, value)

                content['body'] = {'text': body_text}
                content['template'] = tmpl.element_name  # Marker for FE isTemplate detection
                if tmpl.header:
                    content['header'] = {'text': tmpl.header}
                if tmpl.footer:
                    content['footer'] = {'text': tmpl.footer}
                if tmpl.buttons:
                    content['buttons'] = tmpl.buttons
            else:
                # Fallback if template FK is missing
                content['body'] = {'text': str(payload)}
        elif msg_type.upper() == 'INTERACTIVE':
            interactive_data = payload.get('interactive', {})
            interactive_type = interactive_data.get('type', '')

            if interactive_type == 'order_details':
                # NEW type — order_details session messages
                content = {
                    'type': 'order_details',
                    'body': interactive_data.get('body'),
                    'header': interactive_data.get('header'),
                    'footer': interactive_data.get('footer'),
                    'order_details': interactive_data.get('action', {}).get('parameters'),
                }
            elif interactive_type == 'order_status':
                # NEW type — order_status session messages
                content = {
                    'type': 'order_status',
                    'body': interactive_data.get('body'),
                    'order_status': interactive_data.get('action', {}).get('parameters'),
                }
            else:
                # Existing interactive types (button, list, cta_url, etc.)
                # Preserve original behavior — store body text from payload
                # to avoid breaking ChatFlow or other existing interactive flows.
                content['body'] = {'text': str(payload)}
        else:
            content['body'] = {'text': str(payload)}
        
        # Add metadata
        content['_meta'] = {
            'wa_message_pk': str(instance.pk),
            'wa_message_id': instance.wa_message_id,
        }
        
        # Create Messages entry
        message = Messages.objects.create(
            tenant=tenant,
            message_id=event_id,
            content=content,
            direction=MessageDirectionChoices.OUTGOING,
            platform=MessagePlatformChoices.WHATSAPP,
            author=AuthorChoices.USER,
            contact=contact,
            outgoing_message=instance,  # FK field is still named outgoing_message
        )
        
        result['created'] = True
        result['message_id'] = message.pk
        logger.debug("[_create_team_inbox_message_v2] Created Messages %s", message.pk)
        
    except Exception as e:
        result['error'] = str(e)
        logger.error("[_create_team_inbox_message_v2] Error: %s", result['error'])
    
    return result


# Legacy helper - keep for backward compatibility with existing GupshupOutgoingMessages
def _create_team_inbox_message(instance, wa_app):
    """
    Helper function to create a Messages entry in team_inbox for an outgoing message.
    
    Args:
        instance: GupshupOutgoingMessages instance
        wa_app: TenantWAApp instance
        
    Returns:
        dict: Result with keys:
            - created: bool
            - message_id: int (if created)
            - error: str (if failed)
    """
    from contacts.models import TenantContact
    from team_inbox.models import (AuthorChoices, MessageDirectionChoices,
                                   MessageEventIds, MessagePlatformChoices,
                                   Messages)
    
    result = {
        'created': False,
        'message_id': None,
        'error': None
    }
    
    logger.debug(f"[_create_team_inbox_message] Starting for outgoing message {instance.pk}")
    
    try:
        payload = instance.payload or {}
        logger.debug(f"[_create_team_inbox_message] Payload: {payload}")
        
        # Extract phone number from payload (to field)
        phone_number = payload.get('to', '')
        logger.debug(f"[_create_team_inbox_message] Phone from payload: {phone_number}")
        if phone_number:
            # Normalize phone number (remove + prefix if present)
            phone_number = phone_number.lstrip('+')
        
        if not phone_number:
            result['error'] = "No 'to' phone number in payload"
            logger.error(f"[_create_team_inbox_message] {result['error']}")
            return result
        
        # Find the contact by phone number
        tenant = wa_app.tenant
        logger.debug(f"[_create_team_inbox_message] Tenant: {tenant.pk}")
        contact = None
        
        # TenantContact uses 'phone' field (PhoneNumberField)
        # Try exact match first with + prefix, then without
        contact = TenantContact.objects.filter(
            tenant=tenant,
            phone=f"+{phone_number}"
        ).first()
        logger.debug(f"[_create_team_inbox_message] Exact match result: {contact}")
        
        if not contact:
            # Try matching last 10 digits using string conversion
            contacts = TenantContact.objects.filter(tenant=tenant)
            logger.debug(f"[_create_team_inbox_message] Total contacts for tenant: {contacts.count()}")
            for c in contacts:
                if c.phone and str(c.phone).replace('+', '').endswith(phone_number[-10:]):
                    contact = c
                    logger.debug(f"[_create_team_inbox_message] Found contact by partial match: {contact.pk}")
                    break
        
        if not contact:
            result['error'] = f"No contact found for phone {phone_number}"
            logger.error(f"[_create_team_inbox_message] {result['error']}")
            return result
        
        logger.debug(f"[_create_team_inbox_message] Contact found: {contact.pk}")
        
        # Create MessageEventIds entry for timeline ordering
        event_id = MessageEventIds.objects.create()
        logger.debug(f"[_create_team_inbox_message] Created MessageEventIds: {event_id.pk}")
        
        # Build content from payload - match incoming message format
        msg_type = payload.get('type', 'text')
        content = {
            'type': msg_type
        }
        
        # Helper function to get media URL and ID from payload
        # Session messages use 'id' (Gupshup media ID), while template/URL messages use 'link'
        def get_media_info(media_obj: dict) -> dict:
            """Get media URL and ID from payload - handles both 'link' (direct URL) and 'id' (Gupshup media ID)."""
            result = {'url': '', 'media_id': ''}
            
            # Helper to ensure URL is absolute
            def make_absolute_url(url: str) -> str:
                if not url:
                    return ''
                if url.startswith('http'):
                    return url
                # Build absolute URL using Site domain
                from django.contrib.sites.models import Site
                try:
                    domain = Site.objects.get(id=1).domain
                    return f"https://{domain}{url}"
                except Exception:
                    return url
            
            # Check for direct link first (template messages, URL-based media)
            if media_obj.get('link'):
                result['url'] = make_absolute_url(media_obj.get('link'))
                return result
            
            # Session messages use Gupshup media ID, lookup from TenantMedia
            media_id = media_obj.get('id')
            if media_id:
                result['media_id'] = media_id
                from tenants.models import TenantMedia
                tenant_media = TenantMedia.objects.filter(
                    media_id=media_id,
                    tenant=tenant
                ).first()
                if tenant_media and tenant_media.media:
                    try:
                        result['url'] = make_absolute_url(tenant_media.media.url)
                    except Exception as e:
                        logger.error(f"[_create_team_inbox_message] Error getting media URL: {e}")
            
            return result
        
        # Extract body based on message type
        # Format should match incoming messages: {"type": "text", "body": {"text": "..."}}
        if msg_type == 'text':
            text_obj = payload.get('text', {})
            content['body'] = {
                'text': text_obj.get('body', '') if isinstance(text_obj, dict) else str(text_obj)
            }
        elif msg_type == 'image':
            image_obj = payload.get('image', {})
            media_info = get_media_info(image_obj)
            content['image'] = {
                'url': media_info['url'],
            }
            if media_info['media_id']:
                content['image']['media_id'] = media_info['media_id']
            if image_obj.get('caption'):
                content['image']['caption'] = image_obj.get('caption')
        elif msg_type == 'video':
            video_obj = payload.get('video', {})
            media_info = get_media_info(video_obj)
            content['video'] = {
                'url': media_info['url'],
            }
            if media_info['media_id']:
                content['video']['media_id'] = media_info['media_id']
            if video_obj.get('caption'):
                content['video']['caption'] = video_obj.get('caption')
        elif msg_type == 'audio':
            audio_obj = payload.get('audio', {})
            media_info = get_media_info(audio_obj)
            content['audio'] = {
                'url': media_info['url'],
            }
            if media_info['media_id']:
                content['audio']['media_id'] = media_info['media_id']
        elif msg_type == 'document':
            doc_obj = payload.get('document', {})
            media_info = get_media_info(doc_obj)
            content['document'] = {
                'url': media_info['url'],
            }
            if media_info['media_id']:
                content['document']['media_id'] = media_info['media_id']
            if doc_obj.get('caption'):
                content['document']['caption'] = doc_obj.get('caption')
            if doc_obj.get('filename'):
                content['document']['filename'] = doc_obj.get('filename')
        elif msg_type == 'sticker':
            sticker_obj = payload.get('sticker', {})
            media_info = get_media_info(sticker_obj)
            content['sticker'] = {
                'url': media_info['url'],
            }
            if media_info['media_id']:
                content['sticker']['media_id'] = media_info['media_id']
        elif msg_type == 'reaction':
            reaction_obj = payload.get('reaction', {})
            content['reaction'] = {
                'emoji': reaction_obj.get('emoji', ''),
                'message_id': reaction_obj.get('message_id', ''),
            }
        else:
            # For other types, store as text fallback
            content['body'] = {
                'text': str(payload.get(msg_type, ''))
            }
        
        # Create the Messages entry
        message = Messages.objects.create(
            tenant=tenant,
            message_id=event_id,
            content=content,
            direction=MessageDirectionChoices.OUTGOING,
            platform=MessagePlatformChoices.WHATSAPP,
            author=AuthorChoices.USER,  # Outgoing messages are from team users
            contact=contact,
            tenant_user=instance.created_by if hasattr(instance, 'created_by') and instance.created_by else None,
            outgoing_message=instance,
            is_read=True  # Outgoing messages are always "read"
        )
        
        logger.debug(f"[_create_team_inbox_message] Created Messages entry {message.pk} for outgoing message {instance.pk}")
        
        result['created'] = True
        result['message_id'] = message.pk
        return result
        
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"[_create_team_inbox_message] Error: {result['error']}")
        return result


def _normalize_gupshup_v2_to_cloud_api(payload):
    """
    Convert a Gupshup V2 message-event payload into the canonical
    Cloud API ``entry → changes → value → statuses`` structure.

    This keeps all downstream processing and WebSocket broadcasting
    completely BSP-agnostic — the frontend receives the same canonical
    ``message_status_update`` event regardless of which BSP sent the
    webhook.

    Gupshup V2 format::

        {
          "app": "...",
          "timestamp": 1580546677791,
          "version": 2,
          "type": "message-event",
          "payload": {
            "id": "<wamid or GS-UUID>",
            "gsId": "<GS-UUID>",             # present for sent/delivered/read
            "type": "enqueued|sent|delivered|read|failed",
            "destination": "91XXXXXXXXXX",
            "payload": {
              "ts": 1585344475,
              "whatsappMessageId": "...",     # present for enqueued
              "code": 1006,                   # present for failed
              "reason": "..."                 # present for failed
            }
          }
        }

    Returns:
        dict  – payload reshaped into Cloud API canonical format, or
                the original payload unchanged if it cannot be normalised.
    """
    inner = payload.get('payload') or {}
    inner_payload = inner.get('payload') or {}

    gs_status = (inner.get('type') or '').lower()
    if not gs_status:
        return payload  # unrecognised — return as-is

    # ── Resolve IDs ─────────────────────────────────────────────
    # enqueued/failed : inner['id'] = GS UUID,
    #                   inner_payload['whatsappMessageId'] = wamid
    # sent/delivered/read : inner['id'] = wamid,
    #                       inner['gsId'] = GS UUID
    gs_id = inner.get('gsId') or inner.get('id')
    cloud_id = inner_payload.get('whatsappMessageId') or inner.get('id')

    # ── Timestamp (seconds string) ──────────────────────────────
    ts_raw = inner_payload.get('ts') or payload.get('timestamp')
    ts_str = str(ts_raw) if ts_raw is not None else None

    status_entry = {
        'gs_id': gs_id,
        'id': cloud_id,
        'status': gs_status,
        'timestamp': ts_str,
        'recipient_id': inner.get('destination', ''),
    }

    # Include error details for failed events so ``msg_obj.error_message``
    # is populated the same way as Cloud API errors.
    if gs_status == 'failed':
        status_entry['errors'] = [{
            'code': inner_payload.get('code'),
            'title': inner_payload.get('reason', 'Message delivery failed'),
        }]

    return {
        'entry': [{
            'changes': [{
                'value': {
                    'statuses': [status_entry]
                }
            }]
        }]
    }


@shared_task
def process_message_status_webhook(pk: str):
    """
    Celery task to process message status webhooks (delivered, read, sent).
    
    This task parses the webhook payload from WAWebhookEvent and updates
    the corresponding WAMessage status and timestamps.
    
    IMPORTANT: Handles race conditions where webhooks arrive out of order.
    Status priority: PENDING(0) < SENT(1) < DELIVERED(2) < READ(3), FAILED(-1)
    We only update status if the new status has higher priority than current.
    
    Args:
        pk: UUID string of the WAWebhookEvent instance
        
    Returns:
        dict: Status of the operation
    """
    from wa.models import MessageStatus, WAMessage, WAWebhookEvent

    # Status priority for race condition handling.
    # Covers both WAMessage (MessageStatus) and BroadcastMessage
    # (MessageStatusChoices) — shared string values are compatible.
    # Terminal/error states use negative priorities so they are never
    # overwritten by normal progression webhooks.
    STATUS_PRIORITY = {
        MessageStatus.PENDING: 0,
        'QUEUED': 0,           # BroadcastMessage pre-send state
        'SENDING': 1,          # BroadcastMessage actively sending
        MessageStatus.SENT: 2,
        MessageStatus.DELIVERED: 3,
        MessageStatus.READ: 4,
        MessageStatus.FAILED: -1,
        MessageStatus.EXPIRED: -1,
        'BLOCKED': -1,         # BroadcastMessage blocked — terminal
    }
    
    def should_update_status(current_status, new_status):
        if new_status == MessageStatus.FAILED:
            return True
        # Terminal states should never be overwritten by normal progression
        terminal_states = {MessageStatus.FAILED, MessageStatus.EXPIRED, 'BLOCKED'}
        if current_status in terminal_states:
            return False
        current_priority = STATUS_PRIORITY.get(current_status, 0)
        new_priority = STATUS_PRIORITY.get(new_status, 0)
        return new_priority > current_priority
    
    result = {
        'webhook_pk': str(pk),
        'status': 'unknown',
        'messages_updated': [],
        'errors': []
    }
    
    try:
        instance = WAWebhookEvent.objects.get(pk=pk)
        
        # Only process STATUS type events
        if instance.event_type != 'STATUS':
            logger.info(f"Skipping non-STATUS event {instance.pk}")
            return result
        
        payload = instance.payload
        
        if not payload:
            result['status'] = 'skipped'
            result['errors'].append('Empty payload')
            return result
        
        # ── Normalize BSP-specific payloads to canonical format ─────
        # Gupshup V2 uses a flat structure (type:"message-event"), while
        # Cloud API uses entry→changes→value→statuses.  Normalize here
        # so the processing loop below is BSP-agnostic — the WebSocket
        # event the frontend receives is identical regardless of BSP.
        if payload.get('type') == 'message-event':
            payload = _normalize_gupshup_v2_to_cloud_api(payload)

        # Extract statuses from the (now-canonical) webhook payload
        entries = payload.get('entry', [])
        
        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                value = change.get('value', {})
                statuses = value.get('statuses', [])
                
                for status_data in statuses:
                    # Gupshup webhooks may include both gs_id (UUID from
                    # Partner API) and id (Cloud API wamid).  The stored
                    # wa_message_id could be in either format depending on
                    # which BSP sent the message.  Try each independently.
                    gs_id_val = status_data.get('gs_id')
                    cloud_id_val = status_data.get('id')
                    status = status_data.get('status', '').lower()
                    timestamp_str = status_data.get('timestamp')

                    if not gs_id_val and not cloud_id_val:
                        result['errors'].append('No gs_id/id in status data')
                        continue

                    # Find the WAMessage / BroadcastMessage by trying each
                    # ID variant until one matches.
                    wa_msg = None
                    broadcast_msg = None
                    matched_id = None

                    from broadcast.models import \
                        BroadcastMessage as BroadcastMessageModel

                    for candidate_id in [gs_id_val, cloud_id_val]:
                        if not candidate_id:
                            continue
                        # Try wa_message_id first, then gs_message_id
                        try:
                            wa_msg = WAMessage.objects.get(wa_message_id=candidate_id)
                            matched_id = candidate_id
                            break
                        except WAMessage.DoesNotExist:
                            try:
                                wa_msg = WAMessage.objects.get(gs_message_id=candidate_id)
                                matched_id = candidate_id
                                break
                            except WAMessage.DoesNotExist:
                                pass
                        # Try BroadcastMessage
                        try:
                            broadcast_msg = BroadcastMessageModel.objects.get(
                                message_id=candidate_id
                            )
                            matched_id = candidate_id
                            break
                        except BroadcastMessageModel.DoesNotExist:
                            continue

                    if wa_msg is None and broadcast_msg is None:
                        # ── Fallback: phone + wa_app + recent outgoing ────
                        # Gupshup Partner API returns a UUID (messageId) but
                        # the status webhook only carries a wamid (id), so
                        # the ID-based lookup fails.  Fall back to matching
                        # by recipient phone + wa_app + most recent outgoing
                        # message that hasn't been matched yet.
                        recipient_id = status_data.get('recipient_id', '')
                        if recipient_id and hasattr(instance, 'wa_app') and instance.wa_app:
                            from django.db.models import Q
                            phone_variants = [
                                recipient_id,
                                f"+{recipient_id}",
                            ]
                            wa_msg = (
                                WAMessage.objects
                                .filter(
                                    wa_app=instance.wa_app,
                                    direction='OUTBOUND',
                                    contact__phone__in=phone_variants,
                                    status__in=[
                                        MessageStatus.PENDING,
                                        MessageStatus.SENT,
                                        MessageStatus.DELIVERED,
                                    ],
                                )
                                .order_by('-created_at')
                                .first()
                            )
                            if wa_msg:
                                matched_id = wa_msg.wa_message_id
                                # Back-fill the wamid so future lookups hit
                                # directly on wa_message_id.
                                if cloud_id_val and wa_msg.wa_message_id != cloud_id_val:
                                    wa_msg.gs_message_id = wa_msg.wa_message_id
                                    wa_msg.wa_message_id = cloud_id_val
                                    wa_msg.save(update_fields=['wa_message_id', 'gs_message_id'])
                                    logger.info(
                                        "Back-filled wamid %s on WAMessage %s "
                                        "(was %s)",
                                        cloud_id_val, wa_msg.pk,
                                        wa_msg.gs_message_id,
                                    )

                    if wa_msg is None and broadcast_msg is None:
                        tried = '/'.join(filter(None, [gs_id_val, cloud_id_val]))
                        result['errors'].append(
                            f'No WAMessage or broadcast message found for id(s): {tried}'
                        )
                        continue

                    gs_id = matched_id  # keep variable for downstream logging
                    
                    # Parse timestamp
                    status_time = None
                    if timestamp_str:
                        try:
                            ts = int(timestamp_str)
                            # Gupshup sometimes sends millisecond epochs (13 digits)
                            # datetime.fromtimestamp() expects seconds (10 digits)
                            if ts > 1e12:
                                ts = ts // 1000
                            status_time = timezone.datetime.fromtimestamp(
                                ts,
                                tz=timezone.get_current_timezone()
                            )
                        except (ValueError, TypeError, OSError, OverflowError):
                            status_time = timezone.now()
                    else:
                        status_time = timezone.now()
                    
                    msg_obj = wa_msg if wa_msg else broadcast_msg
                    is_broadcast = broadcast_msg is not None
                    
                    update_fields = []
                    message_update = {
                        'gs_id': gs_id,
                        'old_status': msg_obj.status,
                        'new_status': None,
                        'status_updated': False,
                        'is_broadcast': is_broadcast
                    }
                    
                    # Determine the new status choice
                    new_status_choice = None
                    if status in ('sent', 'enqueued'):
                        # 'enqueued' is a Gupshup-specific status meaning
                        # the message is queued for delivery; treat as SENT.
                        new_status_choice = MessageStatus.SENT
                    elif status == 'delivered':
                        new_status_choice = MessageStatus.DELIVERED
                    elif status == 'read':
                        new_status_choice = MessageStatus.READ
                    elif status == 'failed':
                        new_status_choice = MessageStatus.FAILED
                    else:
                        result['errors'].append(f'Unknown status: {status}')
                        continue
                    
                    status_should_update = should_update_status(msg_obj.status, new_status_choice)
                    
                    if status in ('sent', 'enqueued'):
                        if not msg_obj.sent_at:
                            msg_obj.sent_at = status_time
                            update_fields.append('sent_at')
                        if status_should_update:
                            msg_obj.status = MessageStatus.SENT
                            update_fields.append('status')
                            message_update['status_updated'] = True
                        message_update['new_status'] = 'sent'
                        
                    elif status == 'delivered':
                        if not msg_obj.delivered_at:
                            msg_obj.delivered_at = status_time
                            update_fields.append('delivered_at')
                        if not msg_obj.sent_at:
                            msg_obj.sent_at = status_time
                            update_fields.append('sent_at')
                        if status_should_update:
                            msg_obj.status = MessageStatus.DELIVERED
                            update_fields.append('status')
                            message_update['status_updated'] = True
                        message_update['new_status'] = 'delivered'
                        
                    elif status == 'read':
                        if not msg_obj.read_at:
                            msg_obj.read_at = status_time
                            update_fields.append('read_at')
                        if not msg_obj.delivered_at:
                            msg_obj.delivered_at = status_time
                            update_fields.append('delivered_at')
                        if not msg_obj.sent_at:
                            msg_obj.sent_at = status_time
                            update_fields.append('sent_at')
                        if status_should_update:
                            msg_obj.status = MessageStatus.READ
                            update_fields.append('status')
                            message_update['status_updated'] = True
                        message_update['new_status'] = 'read'
                        
                    elif status == 'failed':
                        msg_obj.status = MessageStatus.FAILED
                        msg_obj.failed_at = status_time
                        update_fields.extend(['status', 'failed_at'])
                        
                        errors_data = status_data.get('errors', [])
                        if errors_data:
                            if is_broadcast:
                                # Store error in response field for BroadcastMessage
                                msg_obj.response = str(errors_data)
                                update_fields.append('response')
                            else:
                                msg_obj.error_message = str(errors_data)
                                update_fields.append('error_message')
                        
                        message_update['new_status'] = 'failed'
                        message_update['status_updated'] = True
                    
                    if update_fields:
                        msg_obj.save(update_fields=update_fields)
                        result['messages_updated'].append(message_update)
                        
                        # Broadcast status update to WebSocket clients
                        if is_broadcast:
                            _broadcast_broadcast_message_status_update(msg_obj, msg_obj.status)
                        else:
                            _broadcast_message_status_update_v2(msg_obj, msg_obj.status)
        
        # Mark webhook as processed
        instance.is_processed = True
        instance.save(update_fields=['is_processed'])
        
        result['status'] = 'processed'
        return result
        
    except WAWebhookEvent.DoesNotExist:
        result['status'] = 'error'
        result['errors'].append(f'WAWebhookEvent with pk={pk} does not exist')
        return result
    except Exception as e:
        result['status'] = 'error'
        result['errors'].append(str(e))
        logger.error(f"Error processing message status webhook {pk}: {str(e)}")
        return result


# Keep legacy _broadcast_message_status_update for backward compatibility
def _broadcast_message_status_update(outgoing_msg, status: str):
    """
    Broadcast message status update to WebSocket clients.
    
    Args:
        outgoing_msg: GupshupOutgoingMessages instance
        status: Status string ('sent', 'delivered', 'read', 'failed')
    """
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    from team_inbox.models import Messages
    
    try:
        # Get tenant ID from the Gupshup app
        tenant_id = None
        contact_id = None
        team_inbox_message_id = None  # Messages.pk (id in serializer)
        message_event_id = None  # MessageEventIds.pk (message_id in serializer)
        
        if outgoing_msg.gupshup_model and outgoing_msg.gupshup_model.tenant:
            tenant_id = outgoing_msg.gupshup_model.tenant.pk
        
        if not tenant_id:
            logger.debug(f"[_broadcast_message_status_update] No tenant found for outgoing message {outgoing_msg.pk}")
            return
        
        # Find the team_inbox Message linked to this outgoing message
        team_inbox_msg = Messages.objects.select_related('message_id', 'contact').filter(outgoing_message=outgoing_msg).first()
        if team_inbox_msg:
            team_inbox_message_id = team_inbox_msg.pk  # This is 'id' in serializer
            if team_inbox_msg.message_id:
                message_event_id = team_inbox_msg.message_id.pk  # This is 'message_id' in serializer
            if team_inbox_msg.contact:
                contact_id = team_inbox_msg.contact.pk
        
        # Get the channel layer and broadcast
        channel_layer = get_channel_layer()
        room_group_name = f"team_inbox_{tenant_id}"
        
        # Format timestamps as ISO strings
        def format_dt(dt):
            return dt.isoformat() if dt else None
        
        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                'type': 'message_status_update',
                'outgoing_message_id': outgoing_msg.pk,  # GupshupOutgoingMessages.pk
                'id': team_inbox_message_id,  # Messages.pk - matches 'id' in serializer
                'message_id': message_event_id,  # MessageEventIds.pk - matches 'message_id' in serializer
                'contact_id': contact_id,
                'status': status,
                'outgoing_status': status,  # Match serializer field name
                'sent_at': format_dt(outgoing_msg.sent_at),
                'delivered_at': format_dt(outgoing_msg.delivered_at),
                'read_at': format_dt(outgoing_msg.read_at),
                'failed_at': format_dt(outgoing_msg.failed_at),
                'outgoing_sent_at': format_dt(outgoing_msg.sent_at),  # Match serializer field names
                'outgoing_delivered_at': format_dt(outgoing_msg.delivered_at),
                'outgoing_read_at': format_dt(outgoing_msg.read_at),
                'outgoing_failed_at': format_dt(outgoing_msg.failed_at),
            }
        )
        
        logger.debug(f"[_broadcast_message_status_update] Broadcasted status '{status}' for message {outgoing_msg.pk} to room {room_group_name}")
        
    except Exception as e:
        logger.error(f"[_broadcast_message_status_update] Error broadcasting: {str(e)}")


def _broadcast_broadcast_message_status_update(broadcast_msg, status: str):
    """
    Broadcast message status update to WebSocket clients for BroadcastMessage.
    
    Looks up the corresponding Messages entry to include its ID for frontend matching.
    
    Args:
        broadcast_msg: BroadcastMessage instance
        status: Status string ('sent', 'delivered', 'read', 'failed')
    """
    import json

    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from django.core.serializers.json import DjangoJSONEncoder

    from team_inbox.models import Messages
    
    try:
        # Get tenant ID from the broadcast
        tenant_id = None
        contact_id = None
        
        if broadcast_msg.broadcast and broadcast_msg.broadcast.tenant:
            tenant_id = broadcast_msg.broadcast.tenant.pk
        
        if not tenant_id:
            logger.debug(f"[_broadcast_broadcast_message_status_update] No tenant found for broadcast message {broadcast_msg.pk}")
            return
        
        # Get contact ID
        if broadcast_msg.contact:
            contact_id = broadcast_msg.contact.pk
        
        # Look up the corresponding Messages entry by external_message_id
        messages_entry = None
        messages_id = None
        message_event_id = None
        
        if broadcast_msg.message_id:
            messages_entry = Messages.objects.filter(
                external_message_id=broadcast_msg.message_id
            ).first()
            if messages_entry:
                messages_id = messages_entry.pk
                message_event_id = messages_entry.message_id_id  # FK to MessageEventIds
        
        # Get the channel layer and broadcast
        channel_layer = get_channel_layer()
        room_group_name = f"team_inbox_{tenant_id}"
        
        # Format timestamps as ISO strings
        def format_dt(dt):
            return dt.isoformat() if dt else None
        
        # Pre-serialize through DjangoJSONEncoder to convert non-primitive
        # types (UUID, datetime, etc.) to strings.  channels-redis uses
        # msgpack which cannot serialize these types.
        event_data = {
            'id': messages_id,
            'message_id': message_event_id,
            'broadcast_message_id': broadcast_msg.pk,
            'external_message_id': broadcast_msg.message_id,
            'contact_id': contact_id,
            'status': status,
            'outgoing_status': status,
            'sent_at': format_dt(broadcast_msg.sent_at),
            'delivered_at': format_dt(broadcast_msg.delivered_at),
            'read_at': format_dt(broadcast_msg.read_at),
            'failed_at': format_dt(broadcast_msg.failed_at),
            'outgoing_sent_at': format_dt(broadcast_msg.sent_at),
            'outgoing_delivered_at': format_dt(broadcast_msg.delivered_at),
            'outgoing_read_at': format_dt(broadcast_msg.read_at),
            'outgoing_failed_at': format_dt(broadcast_msg.failed_at),
        }
        safe_data = json.loads(json.dumps(event_data, cls=DjangoJSONEncoder))
        
        async_to_sync(channel_layer.group_send)(
            room_group_name,
            {
                'type': 'message_status_update',
                **safe_data,
            }
        )
        
        logger.debug(f"[_broadcast_broadcast_message_status_update] Broadcasted status '{status}' for broadcast message {broadcast_msg.pk} (Messages.pk={messages_id}) to room {room_group_name}")
        
    except Exception as e:
        logger.error(f"[_broadcast_broadcast_message_status_update] Error broadcasting: {str(e)}")


@shared_task
def process_webhook_event_task(pk: str):
    """
    Unified Celery task to process webhook events based on their event_type.
    
    Routes to the appropriate processing function based on event_type:
    - MESSAGE: process_message_webhook
    - TEMPLATE: process_template_webhook
    - STATUS: process_message_status_webhook
    - BILLING: (no processing currently)
    
    Args:
        pk: UUID string of the WAWebhookEvent instance
    """
    from wa.models import WAWebhookEvent
    
    try:
        instance = WAWebhookEvent.objects.get(pk=pk)
        event_type = instance.event_type
        
        if event_type == 'MESSAGE':
            process_message_webhook(pk)
        elif event_type == 'TEMPLATE':
            process_template_webhook(pk)
        elif event_type == 'STATUS':
            process_message_status_webhook(pk)
        elif event_type == 'BILLING':
            # No processing for billing events currently
            instance.is_processed = True
            instance.save(update_fields=['is_processed'])
            logger.debug(f"Billing webhook {pk} marked as processed (no action required)")
        elif event_type == 'PAYMENT':
            process_payment_webhook(pk)
        elif event_type == 'UNKNOWN':
            # Payload could not be classified — store for debugging, do NOT
            # process (prevents phantom contacts from unrecognised payloads).
            instance.is_processed = True
            instance.error_message = "Unclassified webhook — skipped"
            instance.save(update_fields=['is_processed', 'error_message'])
            logger.info(f"UNKNOWN webhook {pk} skipped (unrecognised payload format)")
        else:
            logger.info(f"Unknown event type: {event_type} for webhook {pk}")
            instance.error_message = f"Unknown event type: {event_type}"
            instance.is_processed = True
            instance.save(update_fields=['error_message', 'is_processed'])
            
    except WAWebhookEvent.DoesNotExist:
        logger.debug(f"WAWebhookEvent with pk={pk} does not exist")
    except Exception as e:
        logger.error(f"Error processing webhook event {pk}: {str(e)}")


@shared_task
def send_wa_message(pk: str):
    """
    Alias for send_outgoing_message for WAMessage.
    Used by signals when a WAMessage is created.
    """
    return send_outgoing_message(pk)


@shared_task
def submit_template_to_meta(template_id: int):
    """
    Alias for submit_template_to_gupshup - will be BSP-agnostic in the future.
    """
    return submit_template_to_gupshup(template_id)


@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def auto_register_gupshup_webhook(self, wa_app_pk: int):
    """
    Auto-register our webhook receiver with Gupshup when a new Gupshup
    WAApp is created.

    Creates a ``WASubscription`` pointing at our public
    ``/wa/v2/webhooks/gupshup/`` endpoint and calls
    ``GupshupAdapter.register_webhook()`` to register it with the Gupshup
    Partner API.

    Triggered by the ``post_save`` signal on ``TenantWAApp``.
    """
    from django.conf import settings as django_settings

    from tenants.models import BSPChoices, TenantWAApp
    from wa.adapters import get_bsp_adapter
    from wa.models import SubscriptionStatus, WASubscription, WebhookEventType

    try:
        wa_app = TenantWAApp.objects.get(pk=wa_app_pk)
    except TenantWAApp.DoesNotExist:
        logger.error("auto_register_gupshup_webhook: WAApp pk=%s not found", wa_app_pk)
        return {"status": "error", "reason": "wa_app_not_found"}

    if wa_app.bsp != BSPChoices.GUPSHUP:
        return {"status": "skipped", "reason": "not_gupshup"}

    # Build the absolute webhook URL from settings
    base = getattr(django_settings, "DEFAULT_WEBHOOK_BASE_URL", "").rstrip("/")
    webhook_path = "/wa/v2/webhooks/gupshup/"
    webhook_url = f"{base}{webhook_path}"

    if not base or base.startswith("http://localhost"):
        logger.warning(
            "auto_register_gupshup_webhook: DEFAULT_WEBHOOK_BASE_URL is %s "
            "— Gupshup will not be able to reach this. "
            "Subscription created locally but BSP registration will likely fail.",
            base,
        )

    # Avoid duplicates — if there's already an ACTIVE subscription for this
    # app pointing at our webhook URL, skip.
    existing = WASubscription.objects.filter(
        wa_app=wa_app,
        webhook_url=webhook_url,
        status=SubscriptionStatus.ACTIVE,
    ).exists()
    if existing:
        logger.info("auto_register_gupshup_webhook: active subscription already exists for app %s", wa_app_pk)
        return {"status": "skipped", "reason": "already_exists"}

    # Create the subscription record
    all_event_types = [et.value for et in WebhookEventType]
    subscription = WASubscription.objects.create(
        wa_app=wa_app,
        webhook_url=webhook_url,
        event_types=all_event_types,
        status=SubscriptionStatus.PENDING,
    )
    logger.info(
        "auto_register_gupshup_webhook: created WASubscription pk=%s for app %s",
        subscription.pk, wa_app_pk,
    )

    # Register with Gupshup via adapter
    try:
        adapter = get_bsp_adapter(wa_app)
        result = adapter.register_webhook(subscription)

        if result.success:
            logger.info(
                "auto_register_gupshup_webhook: SUCCESS — bsp_sub_id=%s",
                subscription.bsp_subscription_id,
            )
            return {"status": "success", "subscription_id": str(subscription.pk)}

        logger.warning(
            "auto_register_gupshup_webhook: Gupshup rejected — %s",
            result.error_message,
        )
        raise self.retry(
            exc=Exception(result.error_message or "BSP registration failed"),
        )

    except self.MaxRetriesExceededError:
        logger.error(
            "auto_register_gupshup_webhook: max retries exceeded for app %s",
            wa_app_pk,
        )
        return {"status": "error", "reason": "max_retries_exceeded"}
    except Exception as exc:
        logger.exception(
            "auto_register_gupshup_webhook: unexpected error for app %s — %s",
            wa_app_pk, exc,
        )
        raise self.retry(exc=exc)


# ──────────────────────────────────────────────────────────────────────────────
# Template retry — schedule after failed submit in viewset create()
# ──────────────────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # 60s, 120s, 240s (exponential via retry backoff)
)
def retry_submit_template(self, template_id: str):
    """
    Retry submitting a DRAFT template to the BSP.

    Called automatically when the synchronous ``adapter.submit_template()``
    fails during template creation.  Uses exponential backoff
    (60s → 120s → 240s) for up to 3 retries.
    """
    from wa.adapters import get_bsp_adapter
    from wa.models import TemplateStatus, WATemplate

    try:
        template = WATemplate.objects.select_related("wa_app").get(id=template_id)
    except WATemplate.DoesNotExist:
        logger.warning("retry_submit_template: template %s not found — skipping", template_id)
        return {"status": "skipped", "reason": "not_found"}

    # Only retry if still in DRAFT with needs_sync=True
    if template.status != TemplateStatus.DRAFT or not template.needs_sync:
        logger.info(
            "retry_submit_template: template %s is %s (needs_sync=%s) — skipping",
            template_id, template.status, template.needs_sync,
        )
        return {"status": "skipped", "reason": f"status={template.status}"}

    try:
        adapter = get_bsp_adapter(template.wa_app)
        result = adapter.submit_template(template)

        if result.success:
            logger.info("retry_submit_template: template %s submitted successfully", template_id)
            return {"status": "success", "template_id": template_id}
        else:
            logger.warning(
                "retry_submit_template: template %s failed — %s",
                template_id, result.error_message,
            )
            raise self.retry(
                exc=Exception(result.error_message),
                countdown=60 * (2 ** self.request.retries),
            )
    except self.MaxRetriesExceededError:
        logger.error("retry_submit_template: max retries exceeded for template %s", template_id)
        WATemplate.objects.filter(id=template_id).update(
            error_message="Auto-retry exhausted after 3 attempts. Use manual sync to retry.",
        )
        return {"status": "error", "reason": "max_retries_exceeded"}
    except Exception as exc:
        logger.exception("retry_submit_template: unexpected error for template %s — %s", template_id, exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


# ── BE-19: Stuck payment checker ────────────────────────────────────────

@shared_task
def check_stuck_payments():
    """
    Periodic task: check orders with pending payment for > 15 min
    via the META Cloud API payment lookup endpoint.

    Registered in Celery beat schedule (every 5 minutes).
    Processes at most 50 orders per run to avoid overloading the API.
    """
    from datetime import timedelta

    from django.utils import timezone

    from wa.models import PaymentStatus, WAOrder
    from wa.services.order_service import OrderService

    cutoff = timezone.now() - timedelta(minutes=15)
    stuck_orders = (
        WAOrder.objects.filter(
            payment_status=PaymentStatus.PENDING,
            created_at__lt=cutoff,
        )
        .select_related("wa_app")[:50]
    )

    checked = 0
    updated = 0
    for order in stuck_orders:
        checked += 1
        try:
            result = OrderService.lookup_payment(order)
            payments = result.get("payments", [])
            if payments:
                payment_status = payments[0].get("status", "").lower()
                if payment_status == "captured":
                    order.payment_status = PaymentStatus.CAPTURED
                    order.payment_captured_at = timezone.now()
                    order.save(update_fields=["payment_status", "payment_captured_at"])
                    updated += 1
                    logger.info(
                        "[check_stuck_payments] Order %s (ref=%s) → captured",
                        order.pk, order.reference_id,
                    )
                elif payment_status == "failed":
                    order.payment_status = PaymentStatus.FAILED
                    order.save(update_fields=["payment_status"])
                    updated += 1
                    logger.info(
                        "[check_stuck_payments] Order %s (ref=%s) → failed",
                        order.pk, order.reference_id,
                    )
        except Exception as e:
            logger.warning(
                "[check_stuck_payments] Lookup failed for order ref=%s: %s",
                order.reference_id, e,
            )

    logger.info(
        "[check_stuck_payments] Checked %d stuck orders, updated %d",
        checked, updated,
    )
