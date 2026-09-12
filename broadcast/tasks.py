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
    tenant_id: int = None,
):
    """
    Compute charge breakdown asynchronously for large contact sets.

    Result is stored in Redis cache keyed by ``task.id``.
    The viewset polls via ``charge_breakdown_status`` action.
    """
    import json

    from django.core.cache import cache

    cache_key = f"charge_breakdown:{self.request.id}"

    # The owning tenant travels with the payload so the poller can prove the
    # caller is entitled to read it. The key is a UUID4 and so unique, but
    # uniqueness is not authorisation: the poll endpoint has no other way to
    # tell whose breakdown it just fetched, and task ids are handed to clients
    # (#320).
    #
    # It is passed in by the caller rather than derived from ``wa_app_id`` here,
    # because the failure branch below needs it too — and the most likely reason
    # to reach that branch is that the app could not be loaded at all. Deriving
    # it would leave exactly those failures unreadable by anyone, which is the
    # "polls forever on a dead task" behaviour #315 set out to remove.
    #
    # The fallback covers a rolling deploy: a task enqueued by the previous
    # revision arrives without the kwarg.
    try:
        from broadcast.services.charge_breakdown import ChargeBreakdownService
        from tenants.models import TenantWAApp

        if tenant_id is None:
            tenant_id = TenantWAApp.objects.filter(id=wa_app_id).values_list("tenant_id", flat=True).first()

        wa_app = TenantWAApp.objects.select_related("tenant").get(id=wa_app_id)
        svc = ChargeBreakdownService(wa_app=wa_app)

        result = svc.compute(
            contact_ids=contact_ids,
            broadcast_id=broadcast_id,
            template_id=template_id,
        )

        cache.set(
            cache_key,
            json.dumps({"status": "completed", "tenant_id": tenant_id, "result": result}),
            timeout=CHARGE_BREAKDOWN_CACHE_TTL,
        )
        return result

    except Exception as exc:
        logger.exception("Charge breakdown task failed: %s", exc)
        try:
            from django.core.cache import cache as _cache

            _cache.set(
                cache_key,
                json.dumps({"status": "failed", "tenant_id": tenant_id, "error": str(exc)}),
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
            try:
                result = setup_broadcast_task.delay(broadcast.pk)
            except Exception:
                logger.exception(
                    "[process_scheduled_broadcasts] Failed to enqueue broadcast %s, reverting to SCHEDULED",
                    broadcast.pk,
                )
                broadcast.status = BroadcastStatusChoices.SCHEDULED
                broadcast.save(update_fields=["status"])
                continue
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


#: Statuses that mean the provider already accepted this message. A batch
#: retry must never re-send one of these: the provider has no idempotency key
#: for sends, so a resend is a second real message to a real customer, billed
#: again, and a spam report waiting to happen (#271).
#: Spelled as literals rather than ``MessageStatusChoices`` members because
#: ``broadcast.models`` is imported inside the tasks here, not at module scope.
#: ``test_the_sent_statuses_match_the_enum`` keeps the two in step.
ALREADY_SENT_STATUSES = frozenset({"SENT", "DELIVERED", "READ"})

#: Substrings that mark a failure as worth another attempt rather than
#: terminal. Matched against the provider error text because the API clients
#: raise a plain ``Exception`` carrying the status code in its message; a typed
#: exception would be better and is a larger change than this fix.
_TRANSIENT_ERROR_MARKERS = (
    "status code 429",
    "status code 500",
    "status code 502",
    "status code 503",
    "status code 504",
    "too many requests",
    "rate limit",
    "timed out",
    "timeout",
    "connection aborted",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
)

#: Ceiling on automatic per-message retries.
MAX_MESSAGE_RETRIES = 3


def _already_sent(message) -> bool:
    """True if this message must not be sent again.

    Two independent signals, because either alone can be stale: a terminal
    status, or a provider message id — which is only ever set from a response
    the provider actually returned.
    """
    if message.status in ALREADY_SENT_STATUSES:
        return True
    return bool(getattr(message, "message_id", "") or "")


def _is_transient(error_text: str, retry_after: int = 0) -> bool:
    """Whether a provider error deserves another attempt.

    A 429 or a 502 says "not now"; an invalid template or a blocked number
    says "not ever". Treating the first as terminal burns the recipient for
    good and — because failures are refunded — quietly turns a rate-limit
    event into a billing event.

    A provider that answered with ``Retry-After`` has already said the failure
    is temporary, and said it in a header rather than in prose — a stronger
    signal than any substring match, and one that does not need the error text
    to be spelled the way we expect (#271).
    """
    if retry_after:
        return True
    lowered = (error_text or "").lower()
    return any(marker in lowered for marker in _TRANSIENT_ERROR_MARKERS)


def _is_opted_out(message) -> bool:
    """Whether this message must not be sent because the contact opted out (#276).

    MARKETING only. Utility and authentication templates are transactional —
    an order update or a login code is not what anyone unsubscribed from, and
    Meta draws the same line — so suppressing them would break traffic the
    contact still expects.
    """
    if not message.broadcast.is_marketing_broadcast:
        return False
    return bool(message.contact and message.contact.marketing_opt_out)


def _requeue_deferred(message_ids: List[int], countdown: int) -> int:
    """Put messages waiting on a send window back on the queue, timed to it.

    The countdown is the fix: it is what makes a 429 a *delayed* send rather
    than a fixed five-minute sweep, and it lives in the broker, so the delay
    outlives the worker that scheduled it (#271).

    Eager mode has no broker and runs the task inline, ignoring the countdown —
    which would re-attempt the send inside the very window we are waiting on,
    and recurse doing it. There the rows are left PENDING and logged; the only
    place eager mode is on is a dev box with no broker.

    Returns the countdown actually scheduled, or 0 if nothing was queued.
    """
    countdown = max(int(countdown or 0), 1)

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        logger.info(
            "Eager mode: %s deferred message(s) left in PENDING instead of a %ss countdown",
            len(message_ids),
            countdown,
        )
        return 0

    process_broadcast_messages_batch.apply_async(args=[message_ids], countdown=countdown)
    logger.info("Re-queued %s deferred message(s) in %ss", len(message_ids), countdown)
    return countdown


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
        # Get messages with related broadcast and contact data. The template is
        # pulled in too because the opt-out check below reads its category, and
        # a lazy load there would be two extra queries per message in the batch.
        messages = BroadcastMessage.objects.select_related(
            "broadcast", "contact", "broadcast__template_number__gupshup_template"
        ).filter(id__in=message_ids)

        if not messages.exists():
            logger.warning(f"No messages found for IDs: {message_ids}")
            return {"status": "completed", "processed": 0, "successful": 0, "failed": 0, "message_ids": message_ids}

        processed_count = 0
        success_count = 0
        failed_count = 0
        processed_ids = []

        skipped_count = 0
        retryable_count = 0
        suppressed_count = 0

        # Messages waiting on a send window, and the longest wait anyone asked
        # for. One delayed task carries them all: a batch is normally one
        # broadcast on one number, so they are waiting on the same window, and a
        # mixed batch from the retry sweep takes the longest of the waits rather
        # than re-attempting anybody early (#271).
        deferred_ids: List[int] = []
        deferred_countdown = 0

        # Process each message
        for message in messages:
            try:
                # A batch retry re-runs the whole message_ids list, so without
                # this guard one late failure re-sends everything before it
                # (#271). The provider offers no idempotency key, so the only
                # protection is not asking twice.
                if _already_sent(message):
                    logger.info(
                        "Skipping message %s — already sent (status=%s, message_id=%s)",
                        message.id,
                        message.status,
                        message.message_id or "",
                    )
                    skipped_count += 1
                    continue

                # A contact who opted out of marketing gets no marketing
                # template (#276). The check sits here, ahead of the provider
                # call, because that call is the spend — and because the
                # charge estimate already left this contact out, so sending
                # anyway would bill nobody for a message Meta counts against
                # the number's quality rating.
                if _is_opted_out(message):
                    logger.info(
                        "Suppressing message %s — contact %s opted out of marketing",
                        message.id,
                        message.contact_id,
                    )
                    message.status = MessageStatusChoices.SUPPRESSED
                    message.response = "Suppressed: contact opted out of marketing messages"
                    message.save(update_fields=["status", "response"])
                    suppressed_count += 1
                    continue

                # Update status to SENDING
                message.status = MessageStatusChoices.SENDING
                message.task_id = self.request.id
                message.save(update_fields=["status", "task_id"])

                logger.info(f"Processing message {message.id} for {message.contact} via {message.broadcast.platform}")

                # Route to appropriate platform handler based on broadcast platform
                result = route_to_platform_handler(message)

                if result.get("deferred"):
                    # The provider never saw this one — its number is inside a
                    # window it may not send in. That is not an attempt, so it
                    # neither spends a retry nor counts as a failure: the row
                    # goes back to PENDING and the delayed re-queue below owns
                    # it (#271).
                    message.status = MessageStatusChoices.PENDING
                    message.response = result.get("error", "Deferred: waiting on the number's send window")
                    message.save(update_fields=["status", "response"])
                    deferred_ids.append(message.id)
                    deferred_countdown = max(deferred_countdown, int(result.get("retry_after") or 0))
                    logger.info("Message %s deferred — %s", message.id, message.response)
                    continue

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
                    error_text = result.get("error", "Unknown error")
                    message.retry_count += 1
                    message.response = error_text
                    retry_after = int(result.get("retry_after") or 0)

                    if _is_transient(error_text, retry_after) and message.retry_count <= MAX_MESSAGE_RETRIES:
                        # Back to PENDING for another attempt — the countdown
                        # below where the provider named one, the sweep
                        # otherwise. FAILED here would be permanent *and*
                        # refunded, turning a 429 into a billing event.
                        message.status = MessageStatusChoices.PENDING
                        retryable_count += 1
                        if retry_after:
                            # The provider named an interval, so this one is not
                            # the sweep's to guess at — it goes back on the queue
                            # timed to the window it was told about (#271).
                            deferred_ids.append(message.id)
                            deferred_countdown = max(deferred_countdown, retry_after)
                        logger.warning(
                            "Message %s hit a transient error (attempt %s/%s), retrying in %s: %s",
                            message.id,
                            message.retry_count,
                            MAX_MESSAGE_RETRIES,
                            f"{retry_after}s at the provider's request" if retry_after else "the next sweep",
                            error_text,
                        )
                    else:
                        message.status = MessageStatusChoices.FAILED
                        failed_count += 1
                        logger.error(f"Message {message.id} failed: {error_text}")

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

        requeued_after = _requeue_deferred(deferred_ids, deferred_countdown) if deferred_ids else 0

        result = {
            "status": "completed",
            "processed": processed_count,
            "successful": success_count,
            "failed": failed_count,
            "retryable": retryable_count,
            "skipped_already_sent": skipped_count,
            "suppressed_opted_out": suppressed_count,
            "deferred": len(deferred_ids),
            "deferred_countdown": requeued_after,
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
                    # Only messages that never reached the provider. Blanket-
                    # failing the batch marked delivered messages FAILED, and
                    # since failures are refunded, credited the tenant for
                    # traffic that really went out (#271).
                    BroadcastMessage.objects.filter(id__in=message_ids).exclude(
                        status__in=ALREADY_SENT_STATUSES
                    ).exclude(message_id__isnull=False, message_id__gt="").update(
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


def _wa_app_for_broadcast(message):
    """The ``TenantWAApp`` this broadcast message sends from.

    Resolution chain:
        message → broadcast → template_number → .gupshup_template (WATemplate)
                  → wa_app (TenantWAApp)

    Raises:
        ValueError: if the chain is broken.
    """
    template_number = message.broadcast.template_number
    if not template_number or not hasattr(template_number, "gupshup_template"):
        raise ValueError("Broadcast has no linked template_number / WATemplate")

    wa_template = template_number.gupshup_template  # reverse OneToOne → WATemplate
    wa_app = wa_template.wa_app
    if not wa_app:
        raise ValueError("WATemplate has no wa_app")

    return wa_app, wa_template


def handle_whatsapp_message(message):
    """
    Handle WhatsApp message sending, through the BSP adapter.

    This used to pick an API client from ``wa_app.bsp`` here, with
    ``else: Gupshup`` as the fallback — so a blank ``bsp`` came here and
    failed with "Gupshup credentials missing" while the adapter factory,
    reading the same column, handed back META Direct (#265). Routing through
    ``get_bsp_adapter`` means there is one answer to "which provider", and it
    is the same one the sync, webhook and template paths get.

    Args:
        message (BroadcastMessage): The message to send

    Returns:
        dict: Send result with success status and details. ``deferred`` marks a
        result the provider never saw, so the loop knows not to spend a retry on
        it; ``retry_after`` is how long the caller must wait (#271).
    """
    from broadcast.services import rate_limiter
    from wa.adapters import get_bsp_adapter

    try:
        logger.info(f"Sending WhatsApp message to {message.contact.phone}")

        # Debug: log the full payload for carousel templates to trace type issues
        import json

        payload = message.payload
        template_components = (payload.get("template") or {}).get("components", [])
        for comp in template_components:
            if comp.get("type") == "CAROUSEL":
                logger.info(f"CAROUSEL payload for {message.contact.phone}: {json.dumps(comp, indent=2, default=str)}")

        wa_app, wa_template = _wa_app_for_broadcast(message)
        is_marketing = message.broadcast.is_marketing_broadcast

        # Both checks happen before the request, because the request is the
        # spend: sending into a window this number is already over costs its
        # quality rating, not just a 429. A message that has to wait comes back
        # `deferred` rather than failed — the provider never saw it, so it must
        # not spend a retry either (#271).
        waiting = rate_limiter.cooldown_seconds_remaining(wa_app)
        if waiting:
            return {
                "success": False,
                "deferred": True,
                "retry_after": waiting,
                "error": f"Provider asked this number to pause; {waiting}s left",
            }

        if not rate_limiter.reserve_send_slot(wa_app):
            return {
                "success": False,
                "deferred": True,
                "retry_after": rate_limiter.PACE_WINDOW_SECONDS,
                "error": f"Number is at its send pace ({rate_limiter.sends_per_minute(wa_app)}/min)",
            }

        result = get_bsp_adapter(wa_app).send_template(
            message.payload,
            is_marketing=is_marketing,
            template_type=getattr(wa_template, "template_type", "") or "",
        )

        if not result.success:
            msg_type = "marketing" if is_marketing else "transactional"
            logger.error(f"Error sending WhatsApp {msg_type} template: {result.error_message}")
            # A 429 that names an interval is the provider saying exactly when it
            # will take traffic again. Honouring it beats the fixed five-minute
            # sweep in both directions: sooner when it asks for seconds, and —
            # the direction that matters — not sooner when it asks for longer,
            # because re-queueing inside the window earns another 429 (#271).
            # ``start_cooldown`` returns 0 when the provider sent no usable
            # header, which leaves the old timing in place.
            return {
                "success": False,
                "error": result.error_message,
                "retry_after": rate_limiter.start_cooldown(wa_app, result.retry_after_seconds),
            }

        # The adapter normalises the id, so this no longer has to guess at the
        # provider's response shape — the guess here only ever handled META's,
        # which left `message_id` blank on Gupshup and silently disabled the
        # duplicate-send guard in `_already_sent` (#271).
        return {
            "success": True,
            "message_id": (result.data or {}).get("message_id") or "",
            "response": result.raw_response,
        }

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

        # Render content with per-contact placeholder substitution
        data = message.broadcast.placeholder_data or {}
        text = message.rendered_content
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


def handle_voice_message(message):
    """Dispatch one voice broadcast recipient.

    Creates a ``VoiceCall`` row, acquires the per-config concurrency
    semaphore, and queues ``voice.tasks.initiate_call``. The call
    state machine takes over from there; ``voice.signals`` mirrors
    terminal status back onto the ``BroadcastMessage`` (see #162
    status sync).

    Args:
        message (BroadcastMessage): The voice recipient to dial.

    Returns:
        dict: ``{success, message_id, error}`` matching the other
        platform handlers' shape.
    """
    try:
        from voice.concurrency import acquire as acquire_voice_slot
        from voice.constants import CallDirection, CallStatus
        from voice.models import VoiceCall, VoiceProviderConfig
        from voice.tasks import initiate_call as voice_initiate_call

        broadcast = message.broadcast
        tenant = broadcast.tenant

        # Resolve outbound config — prefer the tenant's default; fall back
        # to the first enabled config so dev setups without TenantVoiceApp
        # still dispatch.
        config = None
        voice_app = getattr(tenant, "voice_app", None)
        if voice_app is not None and voice_app.default_outbound_config_id:
            config = voice_app.default_outbound_config
        if config is None:
            config = VoiceProviderConfig.objects.filter(tenant=tenant, enabled=True).order_by("-priority").first()
        if config is None:
            return {
                "success": False,
                "error": f"No active VoiceProviderConfig for tenant {tenant.id}",
            }

        # Semaphore: cap simultaneous in-flight calls per config so a big
        # campaign doesn't blow past the provider's concurrency limit.
        if not acquire_voice_slot(tenant.id, config.id, config.max_concurrent_calls):
            return {
                "success": False,
                "error": (
                    f"Voice concurrency cap reached for config "
                    f"{config.id} (max={config.max_concurrent_calls}); "
                    f"message will be retried."
                ),
            }

        from_number = (config.from_numbers or [None])[0]
        if not from_number:
            return {
                "success": False,
                "error": f"VoiceProviderConfig {config.id} has no from_numbers configured",
            }

        # Time-of-day compliance gate (#171). When the broadcast carries
        # an ``allowed_hours_local`` window we check the recipient's local
        # time. Out-of-window dispatches re-queue themselves for
        # ``next_allowed_time`` instead of being dropped.
        #
        # Cap re-schedules at ``MAX_TOD_RESCHEDULES`` so a stuck-window
        # broadcast (e.g. a wrap-around config that always evaluates
        # "outside") fails loudly rather than re-queueing forever.
        # Counter lives in ``BroadcastMessage.webhook_response`` since
        # that's already JSON-typed on the model. (#179 review)
        MAX_TOD_RESCHEDULES = 24
        to_number_e164 = str(message.contact.phone)
        if broadcast.allowed_hours_local:
            from voice.compliance.time_of_day import (
                is_within_allowed_hours,
                next_allowed_time,
                resolve_recipient_timezone,
            )

            recipient_tz = resolve_recipient_timezone(to_number_e164)
            if not is_within_allowed_hours(broadcast.allowed_hours_local, recipient_tz):
                tod_state = dict(message.webhook_response or {})
                reschedules = int(tod_state.get("tod_reschedule_count", 0))
                if reschedules >= MAX_TOD_RESCHEDULES:
                    logger.warning(
                        "[broadcast.handle_voice_message] %s exceeded %d TOD reschedules; failing",
                        message.id,
                        MAX_TOD_RESCHEDULES,
                    )
                    return {
                        "success": False,
                        "error": (
                            f"Exceeded {MAX_TOD_RESCHEDULES} time-of-day reschedule "
                            f"attempts; check allowed_hours_local + recipient timezone."
                        ),
                    }
                tod_state["tod_reschedule_count"] = reschedules + 1
                message.webhook_response = tod_state
                message.save(update_fields=["webhook_response"])

                eta = next_allowed_time(broadcast.allowed_hours_local, recipient_tz)
                # Re-enter the routing batch task at the allowed time —
                # that's the same path the scheduler uses, so the message
                # transitions back through SENDING → routed handler
                # without bypassing status accounting.
                process_broadcast_messages_batch.apply_async(args=[[message.id]], eta=eta)
                logger.info(
                    "[broadcast.handle_voice_message] %s out of allowed_hours_local; "
                    "rescheduled for %s (%s), attempt %d/%d",
                    message.id,
                    eta.isoformat(),
                    recipient_tz,
                    reschedules + 1,
                    MAX_TOD_RESCHEDULES,
                )
                return {
                    "success": True,
                    "message_id": str(message.id),
                    "response": {"rescheduled_for": eta.isoformat()},
                }

        # Render TTS / audio URL from the voice template + placeholder
        # data. Template Jinja rendering arrives with #168 IVR; for #162
        # we pass through ``tts_text`` / ``audio_url`` as-is.
        static_play = _render_voice_template(broadcast)

        call = VoiceCall.objects.create(
            tenant=tenant,
            name=f"broadcast-{broadcast.id}-msg-{message.id}",
            provider_config=config,
            provider_call_id=f"pending-{message.id}",  # replaced by adapter on initiate
            direction=CallDirection.OUTBOUND,
            from_number=str(from_number),
            to_number=to_number_e164,
            contact=message.contact,
            broadcast=broadcast,
            status=CallStatus.QUEUED,
            metadata={"static_play": static_play, "broadcast_message_id": message.id},
        )

        voice_initiate_call.delay(str(call.id))

        return {
            "success": True,
            "message_id": str(call.id),
            "response": {"voice_call_id": str(call.id)},
        }

    except Exception as e:
        error_msg = f"Voice broadcast dispatch failed: {str(e)}"
        logger.exception(error_msg)
        return {"success": False, "error": error_msg}


def _render_voice_template(broadcast) -> dict:
    """Resolve the broadcast's voice template into a play instruction.

    Returns a dict matching ``voice.adapters.base.PlayInstruction`` keys
    (``audio_url`` / ``tts_text`` / ``tts_voice`` / ``tts_language``).
    """
    tpl = getattr(broadcast, "voice_template", None)
    if tpl is None:
        return {}
    return {
        "audio_url": tpl.audio_url or None,
        "tts_text": tpl.tts_text or None,
        "tts_voice": tpl.tts_voice or None,
        "tts_language": tpl.tts_language or None,
    }


# ── Populate platform handler registry (must come after function definitions)
_PLATFORM_HANDLERS.update(
    {
        "WHATSAPP": handle_whatsapp_message,
        "TELEGRAM": handle_telegram_message,
        "SMS": handle_sms_message,
        "RCS": handle_rcs_message,
        "VOICE": handle_voice_message,
    }
)
