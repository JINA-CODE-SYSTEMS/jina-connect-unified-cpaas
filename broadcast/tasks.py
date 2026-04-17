import logging
from typing import List

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

import team_inbox.signals  # noqa: F401, E402 — ensure signals are loaded for broadcasting
from broadcast.utils.placeholder_renderer import render_placeholders

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Charge-Breakdown Async Task (Issue #190)
# ---------------------------------------------------------------------------

CHARGE_BREAKDOWN_CACHE_TTL = 300  # 5 minutes


@shared_task(bind=True, max_retries=1, soft_time_limit=120, time_limit=180)
def compute_charge_breakdown_task(
    self,
    wa_app_id: int,
    contact_ids: list = None,
    broadcast_id: int = None,
    template_id=None,
):
    """
    Compute charge breakdown asynchronously for large contact sets.

    Result is stored in Redis cache keyed by ``task.id``.
    The viewset polls via ``charge_breakdown_status`` action.
    """
    import json

    from django.core.cache import cache

    cache_key = f"charge_breakdown:{self.request.id}"

    try:
        from broadcast.services.charge_breakdown import ChargeBreakdownService
        from tenants.models import TenantWAApp

        wa_app = TenantWAApp.objects.select_related("tenant").get(id=wa_app_id)
        svc = ChargeBreakdownService(wa_app=wa_app)

        result = svc.compute(
            contact_ids=contact_ids,
            broadcast_id=broadcast_id,
            template_id=template_id,
        )

        cache.set(
            cache_key,
            json.dumps({"status": "completed", "result": result}),
            timeout=CHARGE_BREAKDOWN_CACHE_TTL,
        )
        return result

    except Exception as exc:
        logger.exception("Charge breakdown task failed: %s", exc)
        try:
            from django.core.cache import cache as _cache

            _cache.set(
                cache_key,
                json.dumps({"status": "failed", "error": str(exc)}),
                timeout=CHARGE_BREAKDOWN_CACHE_TTL,
            )
        except Exception:
            pass
        raise


def _get_absolute_media_url(media_field) -> str:
    """
    Convert a Django FileField/ImageField to an absolute URL.

    Args:
        media_field: Django FileField or ImageField instance

    Returns:
        Absolute URL string or None if media doesn't exist
    """
    if not media_field:
        return None

    relative_url = media_field.url

    # If already absolute, return as-is
    if relative_url.startswith(("http://", "https://")):
        return relative_url

    # Build absolute URL using settings.BASE_URL (reliable, configurable)
    base_url = getattr(settings, "BASE_URL", "http://localhost:8000").rstrip("/")

    # Ensure relative_url starts with /
    if not relative_url.startswith("/"):
        relative_url = "/" + relative_url

    return f"{base_url}{relative_url}"


def _create_team_inbox_message_from_broadcast(broadcast_message) -> dict:
    """
    Create a Messages entry in team_inbox from a successfully sent BroadcastMessage.

    Uses the rendered_content property which handles placeholder substitution
    for template messages.

    Args:
        broadcast_message: BroadcastMessage instance that was successfully sent

    Returns:
        dict with 'created' (bool), 'message_id' (int), and optionally 'error' (str)
    """
    from broadcast.models import BroadcastPlatformChoices
    from team_inbox.models import (
        AuthorChoices,
        MessageDirectionChoices,
        MessagePlatformChoices,
    )

    result = {"created": False, "message_id": None, "error": None}

    try:
        broadcast = broadcast_message.broadcast
        contact = broadcast_message.contact
        tenant = broadcast.tenant

        # Get the template for header/footer
        template = None
        if broadcast.template_number and broadcast.template_number.gupshup_template:
            template = broadcast.template_number.gupshup_template

        # Build content structure matching team_inbox format
        # {"type": "text|image|video|document|audio", "body": {"text": "..."}, ...}
        rendered_body = broadcast_message.rendered_content

        # Determine content type from template
        # Map TemplateTypeChoices to team_inbox content types
        content_type = "text"  # default
        if template:
            template_type_map = {
                "TEXT": "text",
                "IMAGE": "image",
                "VIDEO": "video",
                "DOCUMENT": "document",
                "AUDIO": "audio",
                "CAROUSEL": "cards",  # maps to cards with buttons
                "LOCATION": "text",  # location treated as text for now
                "PRODUCT": "text",  # product treated as text for now
                "CATALOG": "text",  # catalog treated as text for now
            }
            content_type = template_type_map.get(template.template_type, "text")

        content = {"type": content_type, "body": {"text": rendered_body}}

        # Add media URL for media types (image, video, document, audio)
        # Uses the same 3-level priority as _build_media_header_component():
        #   0. broadcast.media_overrides["header"] — user-uploaded replacement at send time
        #   1. template.tenant_media              — locally uploaded file on template
        #   2. template.example_media_url          — Meta CDN preview link (fallback)
        if template and content_type in ["image", "video", "document", "audio"]:
            media_url = None

            # Priority 0: broadcast media_overrides["header"] → TenantMedia id
            override_media_id = (broadcast.media_overrides or {}).get("header")
            if override_media_id:
                try:
                    from tenants.models import TenantMedia

                    override_tm = TenantMedia.objects.get(pk=override_media_id)
                    if override_tm.media:
                        media_url = _get_absolute_media_url(override_tm.media)
                except TenantMedia.DoesNotExist:
                    logger.warning(
                        f"media_overrides header TenantMedia id={override_media_id} "
                        f"not found — falling back to template media."
                    )

            # Priority 1: template.tenant_media (locally uploaded file)
            if not media_url and template.tenant_media and template.tenant_media.media:
                media_url = _get_absolute_media_url(template.tenant_media.media)

            # Priority 2: template.example_media_url (Meta CDN fallback)
            if not media_url and template.example_media_url:
                media_url = template.example_media_url

            if media_url:
                # Structure: {"image": {"url": "...", "caption": "..."}}
                content[content_type] = {
                    "url": media_url,
                    "caption": rendered_body,  # Use body text as caption for media
                }

        # Handle CAROUSEL/cards type
        if content_type == "cards" and template and template.cards:
            content["cards"] = _convert_template_cards_to_inbox_format(
                template,
                broadcast.placeholder_data,
                broadcast_message._get_contact_reserved_vars(),
                media_overrides=broadcast.media_overrides,
            )

        # Add header if template has one
        if template and template.header:
            # Render header with placeholder substitution
            header_text = _render_template_field(
                template.header, broadcast.placeholder_data, broadcast_message._get_contact_reserved_vars()
            )
            content["header"] = {"text": header_text}

        # Add footer if template has one
        if template and template.footer:
            footer_text = _render_template_field(
                template.footer, broadcast.placeholder_data, broadcast_message._get_contact_reserved_vars()
            )
            content["footer"] = {"text": footer_text}

        # Add buttons if template has them
        if template and template.buttons:
            content["buttons"] = _convert_template_buttons_to_inbox_format(
                template.buttons, broadcast.placeholder_data, broadcast_message._get_contact_reserved_vars()
            )

        # Add template info for reference
        if template:
            content["template"] = {"name": template.element_name, "language": template.language_code}

        # Map broadcast platform to team_inbox platform
        platform_map = {
            BroadcastPlatformChoices.WHATSAPP: MessagePlatformChoices.WHATSAPP,
            BroadcastPlatformChoices.TELEGRAM: MessagePlatformChoices.TELEGRAM,
            BroadcastPlatformChoices.SMS: MessagePlatformChoices.SMS,
            BroadcastPlatformChoices.RCS: MessagePlatformChoices.RCS,
        }
        platform = platform_map.get(broadcast.platform, MessagePlatformChoices.WHATSAPP)

        # Create the Messages entry via shared factory
        from team_inbox.utils.inbox_message_factory import create_inbox_message

        message = create_inbox_message(
            tenant=tenant,
            contact=contact,
            platform=platform,
            direction=MessageDirectionChoices.OUTGOING,
            author=AuthorChoices.USER,
            content=content,
            tenant_user=broadcast.created_by,
            is_read=True,
            external_message_id=broadcast_message.message_id,
        )

        result["created"] = True
        result["message_id"] = message.pk
        return result

    except Exception as e:
        logger.exception(f"[_create_team_inbox_message_from_broadcast] Error creating team inbox message: {str(e)}")
        result["error"] = str(e)
        return result


def _convert_template_buttons_to_inbox_format(
    template_buttons: list, placeholder_data: dict, reserved_vars: dict
) -> list:
    """
    Convert WATemplate buttons to team_inbox format.

    WATemplate button format:
        {"type": "URL", "text": "...", "url": "https://..."}
        {"type": "QUICK_REPLY", "text": "..."}
        {"type": "PHONE_NUMBER", "text": "...", "phone_number": "+..."}

    Team inbox button format:
        {"type": "url", "text": "...", "url": "https://..."}
        {"type": "quick_reply", "text": "..."}
        {"type": "call", "text": "...", "phone": "+..."}

    Args:
        template_buttons: List of buttons from WATemplate
        placeholder_data: Broadcast placeholder data
        reserved_vars: Contact-specific reserved variables

    Returns:
        List of buttons in team_inbox format
    """
    if not template_buttons:
        return []

    # Merge data for placeholder substitution (reserved vars take precedence)
    final_data = {**placeholder_data, **reserved_vars}

    def _render(text: str) -> str:
        return render_placeholders(text, final_data)

    # Type mapping from Gupshup to team_inbox
    type_map = {
        "URL": "url",
        "QUICK_REPLY": "quick_reply",
        "PHONE_NUMBER": "call",
    }

    converted_buttons = []
    for btn in template_buttons:
        btn_type = btn.get("type", "").upper()
        inbox_type = type_map.get(btn_type)

        if not inbox_type:
            logger.warning(f"Unknown button type: {btn_type}, skipping")
            continue

        inbox_btn = {"type": inbox_type, "text": btn.get("text", "")}

        # Add type-specific fields
        if inbox_type == "url" and btn.get("url"):
            inbox_btn["url"] = _render(btn["url"])
        elif inbox_type == "call" and btn.get("phone_number"):
            inbox_btn["phone"] = btn["phone_number"]

        converted_buttons.append(inbox_btn)

    return converted_buttons


def _convert_template_cards_to_inbox_format(
    template,
    placeholder_data: dict,
    reserved_vars: dict,
    media_overrides: dict = None,
) -> list:
    """
    Convert WATemplate cards (carousel) to team_inbox format.

    WATemplate card format:
        [{"body": "Card text {{name}}", "buttons": [...]}, ...]

    Team inbox card format:
        [{"image": {"url": "..."}, "body": {"text": "..."}, "buttons": [...]}, ...]

    Args:
        template: WATemplate instance with cards and card_media
        placeholder_data: Broadcast placeholder data
        reserved_vars: Contact-specific reserved variables
        media_overrides: Broadcast.media_overrides dict (optional),
            e.g. {"cards": {"0": <TenantMedia id>, "1": ...}}

    Returns:
        List of cards in team_inbox format
    """
    cards = template.cards
    if not cards or not isinstance(cards, list):
        return []

    # Merge data for placeholder substitution (reserved vars take precedence)
    final_data = {**placeholder_data, **reserved_vars}

    def _render(text: str) -> str:
        return render_placeholders(text, final_data)

    def _detect_media_type(media_name: str) -> str:
        """Detect if media is video or image from filename."""
        name = (media_name or "").lower()
        if any(ext in name for ext in [".mp4", ".mov", ".avi", ".webm"]):
            return "video"
        return "image"

    # Get card media by index (template-level)
    card_media_map = template.get_card_media_by_index()
    card_overrides = (media_overrides or {}).get("cards", {})

    converted_cards = []
    for i, card in enumerate(cards):
        inbox_card = {}
        media_url = None
        media_type = "image"  # default

        # Priority 0: card-level override from broadcast.media_overrides["cards"]
        card_override_id = card_overrides.get(str(i))
        if card_override_id:
            try:
                from tenants.models import TenantMedia

                override_tm = TenantMedia.objects.get(pk=card_override_id)
                if override_tm.media:
                    media_url = _get_absolute_media_url(override_tm.media)
                    media_type = _detect_media_type(override_tm.media.name)
            except TenantMedia.DoesNotExist:
                logger.warning(
                    f"media_overrides card {i} TenantMedia id={card_override_id} "
                    f"not found — falling back to template card media."
                )

        # Priority 1: template card_media (locally uploaded file)
        if not media_url:
            card_media = card_media_map.get(i)
            if card_media and card_media.media:
                media_url = _get_absolute_media_url(card_media.media)
                media_type = _detect_media_type(card_media.media.name)

        if media_url:
            inbox_card[media_type] = {"url": media_url}

        # Add card body
        card_body = card.get("body", "")
        if card_body:
            inbox_card["body"] = {"text": _render(card_body)}

        # Add card buttons
        card_buttons = card.get("buttons", [])
        if card_buttons:
            inbox_card["buttons"] = _convert_template_buttons_to_inbox_format(
                card_buttons, placeholder_data, reserved_vars
            )

        converted_cards.append(inbox_card)

    return converted_cards


def _render_template_field(field_content: str, placeholder_data: dict, reserved_vars: dict) -> str:
    """
    Render a template field (header/footer) with placeholder substitution.

    Args:
        field_content: The template field text with placeholders like {{ name }} or {{name}}
        placeholder_data: Broadcast placeholder data (dynamic, user-provided)
        reserved_vars: Contact-specific reserved variables (take precedence)

    Returns:
        Rendered string with placeholders replaced
    """
    if not field_content:
        return ""

    # Reserved vars take precedence - contact-specific data should not be overridden
    final_data = {**placeholder_data, **reserved_vars}
    return render_placeholders(field_content, final_data)


@shared_task
def process_scheduled_broadcasts():
    """Celery beat task: find SCHEDULED broadcasts whose time has arrived and launch them (#101).

    Runs every minute via beat_schedule. Picks up broadcasts with
    status=SCHEDULED and scheduled_time <= now, transitions them to SENDING,
    and dispatches ``setup_broadcast_task`` for each.
    """
    from django.utils import timezone as tz

    from broadcast.models import Broadcast, BroadcastStatusChoices

    now = tz.now()
    launched = 0
    with transaction.atomic():
        due = Broadcast.objects.filter(
            status=BroadcastStatusChoices.SCHEDULED,
            scheduled_time__lte=now,
        ).select_for_update(skip_locked=True)

        for broadcast in due:
            broadcast.status = BroadcastStatusChoices.SENDING
            broadcast.save(update_fields=["status"])
            result = setup_broadcast_task.delay(broadcast.pk)
            broadcast.task_id = result.id
            broadcast.save(update_fields=["task_id"])
            launched += 1
            logger.info("[process_scheduled_broadcasts] Launched broadcast %s (task %s)", broadcast.pk, result.id)

    if launched:
        logger.info("[process_scheduled_broadcasts] Launched %d scheduled broadcasts", launched)
    return {"launched": launched}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def setup_broadcast_task(self, broadcast_id: int):
    """
    Setup broadcast processing by creating message records and queuing batch tasks.

    Args:
        broadcast_id (int): The ID of the Broadcast to process.
    """
    from broadcast.models import Broadcast, BroadcastStatusChoices
    from broadcast.utils.service import BroadcastService

    try:
        broadcast = Broadcast.objects.get(id=broadcast_id)

        # Check if broadcast is still in pending/scheduled status
        if broadcast.status not in [BroadcastStatusChoices.SENDING, BroadcastStatusChoices.SCHEDULED]:
            logger.info(f"Broadcast {broadcast_id} status is {broadcast.status} - skipping execution")
            return {"skipped": True, "reason": f"Broadcast status is {broadcast.status}"}

        # Update status to SENDING
        broadcast.status = BroadcastStatusChoices.SENDING
        broadcast.save(update_fields=["status"])

        # Start the broadcast process
        service = BroadcastService(broadcast_id=broadcast_id)
        try:
            result = service()
            logger.info(f"Broadcast {broadcast_id} processing initiated: {result}")
        except Exception as e:
            logger.exception(f"Error during broadcast {broadcast_id} processing: {str(e)}")
            broadcast.status = BroadcastStatusChoices.FAILED
            broadcast.reason_for_cancellation = str(e)
            broadcast.save(update_fields=["status", "reason_for_cancellation"])
            result = {"error": str(e)}
        return result

    except Broadcast.DoesNotExist:
        logger.error(f"Broadcast {broadcast_id} not found")
        return {"error": "Broadcast not found"}
    except Exception as e:
        logger.exception(f"Error setting up broadcast {broadcast_id}: {str(e)}")

        # Update broadcast status to failed
        try:
            broadcast = Broadcast.objects.get(id=broadcast_id)
            broadcast.status = BroadcastStatusChoices.FAILED
            broadcast.save(update_fields=["status"])
        except Exception as save_err:
            logger.error(f"Failed to mark broadcast {broadcast_id} as FAILED: {save_err}")

        return {"error": str(e)}


@shared_task
def cancel_broadcast_task(task_id: str):
    """
    Cancel a scheduled broadcast task

    Args:
        task_id (str): The Celery task ID to cancel

    Returns:
        dict: Result of the cancellation attempt
    """
    from celery import current_app

    try:
        # Revoke the task
        current_app.control.revoke(task_id, terminate=True)
        logger.info(f"Successfully cancelled task {task_id}")
        return {"success": True, "task_id": task_id}
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {str(e)}")
        return {"success": False, "error": str(e), "task_id": task_id}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_broadcast_messages_batch(self, message_ids: List[int]):
    """
    Process a batch of BroadcastMessage IDs through appropriate platform handlers

    Args:
        message_ids (List[int]): List of BroadcastMessage IDs to process

    Returns:
        dict: Processing results with success/failure counts
    """
    from broadcast.models import BroadcastMessage, MessageStatusChoices

    if not message_ids:
        logger.warning("Empty message_ids list provided to process_broadcast_messages_batch")
        return {"status": "completed", "processed": 0, "successful": 0, "failed": 0, "message_ids": []}

    logger.info(f"Processing batch of {len(message_ids)} broadcast messages")

    try:
        # Get messages with related broadcast and contact data
        messages = BroadcastMessage.objects.select_related("broadcast", "contact").filter(id__in=message_ids)

        if not messages.exists():
            logger.warning(f"No messages found for IDs: {message_ids}")
            return {"status": "completed", "processed": 0, "successful": 0, "failed": 0, "message_ids": message_ids}

        processed_count = 0
        success_count = 0
        failed_count = 0
        processed_ids = []

        # Process each message
        for message in messages:
            try:
                # Update status to SENDING
                message.status = MessageStatusChoices.SENDING
                message.task_id = self.request.id
                message.save(update_fields=["status", "task_id"])

                logger.info(f"Processing message {message.id} for {message.contact} via {message.broadcast.platform}")

                # Route to appropriate platform handler based on broadcast platform
                result = route_to_platform_handler(message)

                if result["success"]:
                    message.status = MessageStatusChoices.SENT
                    message.message_id = result.get("message_id", "")
                    message.response = result.get("response", "Success")
                    message.sent_at = timezone.now()
                    success_count += 1
                    logger.info(f"Message {message.id} sent successfully")

                    # Create team inbox entry for the sent message
                    try:
                        inbox_result = _create_team_inbox_message_from_broadcast(message)
                        if inbox_result["created"]:
                            logger.info(
                                f"Created team inbox message {inbox_result['message_id']} for broadcast message {message.id}"
                            )
                        else:
                            logger.warning(
                                f"Failed to create team inbox message for broadcast message {message.id}: {inbox_result.get('error')}"
                            )
                    except Exception as inbox_error:
                        logger.exception(
                            f"Error creating team inbox message for broadcast message {message.id}: {str(inbox_error)}"
                        )
                else:
                    message.status = MessageStatusChoices.FAILED
                    message.response = result.get("error", "Unknown error")
                    message.retry_count += 1
                    failed_count += 1
                    logger.error(f"Message {message.id} failed: {result.get('error', 'Unknown error')}")

                message.save(update_fields=["status", "message_id", "response", "retry_count", "sent_at"])
                processed_count += 1
                processed_ids.append(message.id)

            except Exception as e:
                logger.exception(f"Error processing message {message.id}: {str(e)}")
                # Update message to failed status
                try:
                    message.status = MessageStatusChoices.FAILED
                    message.response = f"Processing error: {str(e)}"
                    message.retry_count += 1
                    message.save(update_fields=["status", "response", "retry_count"])
                    failed_count += 1
                    processed_count += 1
                    processed_ids.append(message.id)
                except Exception as save_error:
                    logger.exception(f"Error saving failed message {message.id}: {str(save_error)}")

        result = {
            "status": "completed",
            "processed": processed_count,
            "successful": success_count,
            "failed": failed_count,
            "message_ids": processed_ids,
        }

        logger.info(f"Batch processing completed: {result}")
        return result

    except Exception as exc:
        logger.exception(f"Error processing batch {message_ids}: {str(exc)}")

        # Retry logic
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying batch processing (attempt {self.request.retries + 1})")
            raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
        else:
            # Mark all messages as failed after max retries
            logger.error(f"Max retries exceeded for batch {message_ids}")
            try:
                with transaction.atomic():
                    BroadcastMessage.objects.filter(id__in=message_ids).update(
                        status=MessageStatusChoices.FAILED, response=f"Max retries exceeded: {str(exc)}"
                    )
            except Exception as update_error:
                logger.exception(f"Error updating failed messages: {str(update_error)}")

            return {
                "status": "failed",
                "processed": len(message_ids),
                "successful": 0,
                "failed": len(message_ids),
                "error": str(exc),
                "message_ids": message_ids,
            }


# ── Platform handler dispatch registry ─────────────────────────────────────
# Add new platforms here instead of growing an if/elif chain.
# Maps to actual function refs — populated after the functions are defined
# (see bottom of file).
_PLATFORM_HANDLERS: dict = {}


def route_to_platform_handler(message):
    """
    Route message to appropriate platform handler based on broadcast platform.

    Args:
        message (BroadcastMessage): The message to process

    Returns:
        dict: Processing result with success status and details
    """
    platform = message.broadcast.platform.upper()

    handler_name = _PLATFORM_HANDLERS.get(platform)
    if not handler_name:
        error_msg = f"Unsupported platform: {platform}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    try:
        return handler_name(message)
    except Exception as e:
        error_msg = f"Error in platform handler for {platform}: {str(e)}"
        logger.exception(error_msg)
        return {"success": False, "error": error_msg}


def _get_wa_api_for_broadcast(message):
    """
    Resolve the correct WhatsApp API client for a BroadcastMessage based on
    the BSP of the template's wa_app.

    Resolution chain:
        message → broadcast → template_number → .gupshup_template (WATemplate)
                  → wa_app (TenantWAApp) → bsp

    Returns:
        API client instance with a ``.send_template(data, is_marketing)`` method.

    Raises:
        ValueError: If wa_app or credentials cannot be resolved.
    """
    from django.conf import settings

    from tenants.models import BSPChoices

    template_number = message.broadcast.template_number
    if not template_number or not hasattr(template_number, "gupshup_template"):
        raise ValueError("Broadcast has no linked template_number / WATemplate")

    wa_template = template_number.gupshup_template  # reverse OneToOne → WATemplate
    wa_app = wa_template.wa_app
    if not wa_app:
        raise ValueError("WATemplate has no wa_app")

    bsp = getattr(wa_app, "bsp", None)

    if bsp == BSPChoices.META:
        from wa.utility.apis.meta.template_api import TemplateAPI as MetaTemplateAPI

        creds = wa_app.bsp_credentials or {}
        token = creds.get("access_token") or getattr(settings, "META_PERM_TOKEN", None)
        if not token:
            raise ValueError(
                "META access token not configured. Set bsp_credentials.access_token "
                "on the WAApp or META_PERM_TOKEN in settings."
            )
        waba_id = wa_app.waba_id
        if not waba_id:
            raise ValueError("WABA ID not configured on the WAApp.")

        phone_number_id = wa_app.phone_number_id
        if not phone_number_id:
            raise ValueError(
                "phone_number_id not configured on the WAApp. Required for META Cloud API message sending."
            )

        return MetaTemplateAPI(
            token=token,
            waba_id=waba_id,
            phone_number_id=phone_number_id,
        )

    else:
        # Default: Gupshup (covers BSPChoices.GUPSHUP and legacy apps)
        from wa.utility.apis.gupshup.template_api import TemplateAPI as GupshupTemplateAPI

        app_id = wa_app.app_id
        app_secret = wa_app.app_secret
        if not app_id or not app_secret:
            raise ValueError(f"Gupshup credentials (app_id/app_secret) missing on WAApp {wa_app.pk}")

        return GupshupTemplateAPI(appId=app_id, token=app_secret)


def handle_whatsapp_message(message):
    """
    Handle WhatsApp message sending (BSP-aware).

    Resolves the correct API client (Gupshup or META Direct) from the
    template's wa_app, then sends via ``api.send_template()``.

    Args:
        message (BroadcastMessage): The message to send

    Returns:
        dict: Send result with success status and details
    """
    from broadcast.models import BroadcastMessage

    message: BroadcastMessage = message

    def extract_message_id(response: dict) -> str:
        """
        Extract message ID from API response.
        Both Gupshup and META return: {'messages': [{'id': 'wamid.xxx'}], ...}
        """
        try:
            messages = response.get("messages", [])
            if messages and len(messages) > 0:
                return messages[0].get("id", "")
        except (KeyError, IndexError, TypeError):
            pass
        return ""

    try:
        logger.info(f"Sending WhatsApp message to {message.contact.phone}")

        # Debug: log the full payload for carousel templates to trace type issues
        import json

        payload = message.payload
        template_components = (payload.get("template") or {}).get("components", [])
        for comp in template_components:
            if comp.get("type") == "CAROUSEL":
                logger.info(f"CAROUSEL payload for {message.contact.phone}: {json.dumps(comp, indent=2, default=str)}")

        wa_api = _get_wa_api_for_broadcast(message)
        is_marketing = message.broadcast.is_marketing_broadcast

        try:
            result = wa_api.send_template(data=message.payload, is_marketing=is_marketing)
            return {"success": True, "message_id": extract_message_id(result), "response": result}
        except Exception as e:
            msg_type = "marketing" if is_marketing else "transactional"
            logger.exception(f"Error sending WhatsApp {msg_type} template: {str(e)}")
            return {"success": False, "error": str(e)}

    except Exception as e:
        error_msg = f"WhatsApp sending failed: {str(e)}"
        logger.exception(error_msg)
        return {"success": False, "error": error_msg}


def handle_telegram_message(message):
    """
    Handle Telegram message sending via TelegramMessageSender.

    Resolves the active TelegramBotApp for the tenant, builds a
    TelegramMessageSender, and dispatches based on broadcast content.

    Args:
        message (BroadcastMessage): The message to send

    Returns:
        dict: Send result with success status and details
    """
    try:
        from telegram.models import TelegramBotApp
        from telegram.services.message_sender import TelegramMessageSender

        contact = message.contact
        tenant = message.broadcast.tenant

        # Resolve active Telegram bot for this tenant
        bot_app = TelegramBotApp.objects.filter(tenant=tenant, is_active=True).first()
        if not bot_app:
            return {"success": False, "error": f"No active Telegram bot configured for tenant {tenant.pk}"}

        # Contact must have a telegram_chat_id to receive messages
        chat_id = contact.telegram_chat_id
        if not chat_id:
            return {"success": False, "error": f"Contact {contact.pk} has no telegram_chat_id"}

        sender = TelegramMessageSender(bot_app)

        # Determine content from broadcast placeholder_data
        data = message.broadcast.placeholder_data or {}
        text = data.get("message") or data.get("text") or data.get("body", "")
        media_url = data.get("media_url") or data.get("image_url") or data.get("image")
        media_type = data.get("media_type", "photo")

        if media_url:
            result = sender.send_media(
                chat_id=str(chat_id),
                media_type=media_type,
                media_url=media_url,
                caption=text or None,
                contact=contact,
            )
        elif text:
            result = sender.send_text(
                chat_id=str(chat_id),
                text=text,
                contact=contact,
            )
        else:
            return {"success": False, "error": "Broadcast has no text or media content to send"}

        logger.info(
            "Telegram broadcast message %s to chat_id %s: success=%s",
            message.pk,
            chat_id,
            result.get("success"),
        )
        return result

    except Exception as e:
        error_msg = f"Telegram sending failed: {str(e)}"
        logger.exception(error_msg)
        return {"success": False, "error": error_msg}


def handle_sms_message(message):
    """
    Handle SMS message sending

    Args:
        message (BroadcastMessage): The message to send

    Returns:
        dict: Send result with success status and details
    """
    try:
        from sms.models import SMSApp
        from sms.services.message_sender import SMSMessageSender

        logger.info(f"Sending SMS message to {message.contact.phone}")

        sms_app = SMSApp.objects.filter(tenant=message.broadcast.tenant, is_active=True).first()
        if not sms_app:
            return {"success": False, "error": f"No active SMS app configured for tenant {message.broadcast.tenant_id}"}

        sender = SMSMessageSender(sms_app)

        data = message.broadcast.placeholder_data or {}
        text = message.rendered_content or data.get("message") or data.get("text") or data.get("body", "")
        if not text:
            return {"success": False, "error": "Broadcast has no SMS text content to send"}

        result = sender.send_text(
            chat_id=str(message.contact.phone),
            text=text,
            contact=message.contact,
            broadcast_message=message,
            create_inbox_entry=False,
        )

        return {
            "success": result.get("success", False),
            "message_id": result.get("message_id", ""),
            "response": result,
            "error": result.get("error"),
        }

    except Exception as e:
        error_msg = f"SMS sending failed: {str(e)}"
        logger.exception(error_msg)
        return {"success": False, "error": error_msg}


def handle_rcs_message(message):
    """
    Handle RCS message sending via RCSMessageSender.

    Resolves the active RCSApp for the tenant, builds a RCSMessageSender,
    and dispatches based on broadcast content (text, media, rich card).
    Falls back to SMS automatically when the recipient device is not RCS-capable
    (handled inside RCSMessageSender._send_with_fallback).

    Args:
        message (BroadcastMessage): The message to send

    Returns:
        dict: Send result with success status and details
    """
    try:
        from rcs.models import RCSApp
        from rcs.services.message_sender import RCSMessageSender

        tenant = message.broadcast.tenant
        contact = message.contact

        rcs_app = RCSApp.objects.filter(tenant=tenant, is_active=True).first()
        if not rcs_app:
            return {"success": False, "error": f"No active RCS app configured for tenant {tenant.pk}"}

        if not contact.phone:
            return {"success": False, "error": f"Contact {contact.pk} has no phone number"}

        sender = RCSMessageSender(rcs_app)

        data = message.broadcast.placeholder_data or {}
        text = message.rendered_content or data.get("message") or data.get("text") or data.get("body", "")
        media_url = data.get("media_url") or data.get("image_url")
        media_type = data.get("media_type", "image")

        phone = str(contact.phone)

        if media_url:
            result = sender.send_media(
                chat_id=phone,
                media_type=media_type,
                media_url=media_url,
                caption=text or None,
                contact=contact,
                broadcast_message=message,
            )
        elif text:
            result = sender.send_text(
                chat_id=phone,
                text=text,
                contact=contact,
                broadcast_message=message,
            )
        else:
            return {"success": False, "error": "Broadcast has no text or media content to send via RCS"}

        logger.info(
            "RCS broadcast message %s to %s: success=%s",
            message.pk,
            phone,
            result.get("success"),
        )
        return result

    except Exception as e:
        error_msg = f"RCS sending failed: {str(e)}"
        logger.exception(error_msg)
        return {"success": False, "error": error_msg}


# ── Populate platform handler registry (must come after function definitions)
_PLATFORM_HANDLERS.update(
    {
        "WHATSAPP": handle_whatsapp_message,
        "TELEGRAM": handle_telegram_message,
        "SMS": handle_sms_message,
        "RCS": handle_rcs_message,
    }
)
