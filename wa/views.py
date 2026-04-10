"""
Public Webhook Receiver Views -- unauthenticated endpoints that BSPs POST to.

These endpoints are the entry-point for **inbound** webhook traffic from
Gupshup, META, etc.  They:

1. Accept the raw POST body.
2. Identify the ``WAApp`` (via ``gs_app_id`` / WABA-id in the payload).
3. Classify the ``WebhookEventType`` (MESSAGE, TEMPLATE, STATUS, ...).
4. Persist a ``WAWebhookEvent`` row.
5. The existing ``post_save`` signal on ``WAWebhookEvent`` queues a Celery
   task (``process_webhook_event_task``) that does the heavy processing.

Security:
    - Gupshup endpoint: unauthenticated (HMAC not yet supported by GS).
    - META endpoint: validates ``X-Hub-Signature-256`` (HMAC-SHA256 with
      ``META_APP_SECRET``) and ``hub.verify_token`` during verification.
    - Rate-limiting should be handled at the reverse-proxy / WAF layer.

URL layout (registered in ``wa/urls.py``):
    POST /wa/v2/webhooks/gupshup/         -- Gupshup callback receiver
    GET  /wa/v2/webhooks/gupshup/         -- Gupshup verification (hub.challenge)
    POST /wa/v2/webhooks/meta/            -- META Cloud API callback receiver
    GET  /wa/v2/webhooks/meta/            -- META verification (hub.challenge)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

from django.conf import settings as django_settings
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _classify_cloud_api_event(payload: Dict[str, Any]) -> str:
    """
    Classify a META Cloud API webhook payload into our ``WebhookEventType``.

    Both Gupshup and META Direct use the same Cloud API envelope::

        { "entry": [{ "changes": [{ "field": "<field>", "value": {...} }] }] }

    Mapping:
        messages                           -> MESSAGE
        message_template_status_update     -> TEMPLATE
        template_category_update           -> TEMPLATE
        statuses                           -> STATUS
        billing                            -> BILLING
        account_update                     -> ACCOUNT
    """
    try:
        field = (
            payload
            .get("entry", [{}])[0]
            .get("changes", [{}])[0]
            .get("field", "")
        )
    except (IndexError, AttributeError):
        field = ""

    if field in ("message_template_status_update", "template_category_update"):
        return "TEMPLATE"
    if field in ("account_update", "account_alerts"):
        return "ACCOUNT"
    if field == "billing":
        return "BILLING"

    # META Cloud API sends status/billing/read-receipt webhooks with
    # field="messages" but value containing "statuses" (no "messages"
    # key).  We must inspect the value *contents* to classify correctly.
    if field == "messages":
        try:
            value = (
                payload
                .get("entry", [{}])[0]
                .get("changes", [{}])[0]
                .get("value", {})
            )
        except (IndexError, AttributeError):
            value = {}

        has_messages = "messages" in value and value["messages"]
        has_statuses = "statuses" in value

        if has_statuses and not has_messages:
            # Check if it's a payment status webhook
            statuses_list = value.get("statuses", [])
            if statuses_list and isinstance(statuses_list, list):
                first_status = statuses_list[0] if statuses_list else {}
                if isinstance(first_status, dict) and first_status.get("type") == "payment":
                    return "PAYMENT"
            return "STATUS"
        # If both are present, MESSAGE takes priority (status will be
        # handled separately by the status processor).
        return "MESSAGE"

    if field == "statuses":
        return "STATUS"

    # Fallback -- look for top-level hints
    try:
        value = payload.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        if "messages" in value and value["messages"]:
            return "MESSAGE"
        if "statuses" in value:
            return "STATUS"
    except (IndexError, AttributeError):
        pass

    # ── Extra safety: peek at Gupshup non-Cloud-API formats ──────────
    # Gupshup Partner API v2 payloads sometimes use a flat structure
    # with "type": "message-event" / "message" at the top level.
    payload_type = payload.get("type", "")
    if payload_type in ("message-event", "billing-event"):
        return "STATUS"
    if payload_type in ("message",):
        return "MESSAGE"

    logger.warning(
        "Could not classify Cloud API webhook (field=%r, type=%r), "
        "defaulting to UNKNOWN — will be skipped by processors",
        field, payload_type,
    )
    return "UNKNOWN"


# Keep old name as alias for backward compatibility
_classify_gupshup_event = _classify_cloud_api_event


def _extract_gs_app_id(payload: Dict[str, Any]) -> Optional[str]:
    """Return the ``gs_app_id`` string from a Gupshup payload (top-level key)."""
    return payload.get("gs_app_id") or payload.get("app_id")


def _extract_meta_waba_id(payload: Dict[str, Any]) -> Optional[str]:
    """Return the WABA ID from a META Cloud API payload (``entry[0].id``)."""
    try:
        return str(payload["entry"][0]["id"])
    except (KeyError, IndexError, TypeError):
        return None


def _extract_meta_phone_number_id(payload: Dict[str, Any]) -> Optional[str]:
    """Return the phone_number_id from ``entry[0].changes[0].value.metadata``."""
    try:
        return str(
            payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]
        )
    except (KeyError, IndexError, TypeError):
        return None


def _verify_meta_signature(request) -> bool:
    """
    Validate the ``X-Hub-Signature-256`` header against the request body.

    Returns ``True`` if the signature is valid **or** if ``META_APP_SECRET``
    is not configured (graceful degradation in dev).
    """
    app_secret = getattr(django_settings, "META_APP_SECRET", "")
    if not app_secret:
        logger.warning("META_APP_SECRET not set -- skipping X-Hub-Signature-256 verification")
        return True

    signature_header = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
    if not signature_header.startswith("sha256="):
        logger.warning("META webhook: missing or malformed X-Hub-Signature-256 header")
        return False

    expected_sig = signature_header[7:]  # strip "sha256=" prefix
    computed_sig = hmac.new(
        app_secret.encode("utf-8"),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_sig, expected_sig)


# ──────────────────────────────────────────────────────────────────────────────
# Gupshup Webhook Receiver
# ──────────────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name="dispatch")
class GupshupWebhookView(View):
    """
    Public endpoint for Gupshup webhook callbacks.

    GET  — Gupshup verification handshake (returns ``hub.challenge``).
    POST — Receives webhook events and creates ``WAWebhookEvent`` rows.
    """

    # ── GET: verification handshake ───────────────────────────────────────

    def get(self, request, *args, **kwargs):
        """
        Gupshup (and META) webhook verification.

        Gupshup sends a GET with ``hub.mode``, ``hub.verify_token``, and
        ``hub.challenge``.  We echo back the challenge to prove ownership.
        """
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        if mode == "subscribe" and challenge:
            # Optional: verify token against WASubscription.verify_token
            logger.info("Gupshup webhook verification — echoing challenge")
            return HttpResponse(challenge, content_type="text/plain", status=200)

        return JsonResponse({"error": "Invalid verification request"}, status=403)

    # ── POST: event ingestion ─────────────────────────────────────────────

    def post(self, request, *args, **kwargs):
        """
        Ingest a webhook event from Gupshup.

        Flow:
        1. Parse JSON body.
        2. Look up ``WAApp`` via ``gs_app_id``.
        3. Classify event type (TEMPLATE, MESSAGE, STATUS, …).
        4. Create ``WAWebhookEvent`` → triggers signal → Celery pipeline.
        5. Return 200 immediately (processing is async).
        """
        from tenants.models import BSPChoices
        from wa.models import WAApp, WAWebhookEvent

        # --- parse body ---------------------------------------------------
        try:
            payload: Dict[str, Any] = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Gupshup webhook: invalid JSON body")
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # --- identify the WAApp -------------------------------------------
        gs_app_id = _extract_gs_app_id(payload)
        if not gs_app_id:
            logger.warning("Gupshup webhook: no gs_app_id in payload")
            return JsonResponse({"error": "Missing gs_app_id"}, status=400)

        try:
            wa_app = WAApp.objects.get(app_id=gs_app_id, bsp=BSPChoices.GUPSHUP)
        except WAApp.DoesNotExist:
            logger.warning("Gupshup webhook: no Gupshup app with app_id=%s", gs_app_id)
            return JsonResponse({"error": "Unknown app"}, status=404)

        # --- classify & persist -------------------------------------------
        event_type = _classify_cloud_api_event(payload)

        webhook_event = WAWebhookEvent.objects.create(
            wa_app=wa_app,
            event_type=event_type,
            bsp=BSPChoices.GUPSHUP,
            payload=payload,          # BaseWebhookDumps.payload
        )

        logger.info(
            "Gupshup webhook ingested: event=%s app=%s pk=%s",
            event_type,
            gs_app_id,
            webhook_event.pk,
        )

        # 200 = "received, will process async"
        return JsonResponse(
            {
                "status": "received",
                "event_id": str(webhook_event.pk),
                "event_type": event_type,
            },
            status=200,
        )


# ──────────────────────────────────────────────────────────────────────────────
# META Direct Webhook Receiver
# ──────────────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name="dispatch")
class MetaWebhookView(View):
    """
    Public endpoint for META Cloud API webhook callbacks.

    GET  -- META verification handshake (validates ``hub.verify_token``,
           returns ``hub.challenge``).
    POST -- Receives webhook events, verifies ``X-Hub-Signature-256``,
           and creates ``WAWebhookEvent`` rows.

    META identifies the app via ``entry[0].id`` (WABA ID) and
    ``entry[0].changes[0].value.metadata.phone_number_id``.
    """

    # ── GET: verification handshake ───────────────────────────────────────

    def get(self, request, *args, **kwargs):
        """
        META webhook verification.

        META sends a GET with ``hub.mode=subscribe``,
        ``hub.verify_token=<your_token>``, and ``hub.challenge=<int>``.

        We verify the token against ``META_WEBHOOK_VERIFY_TOKEN`` and
        echo back the challenge.
        """
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        expected_token = getattr(django_settings, "META_WEBHOOK_VERIFY_TOKEN", "")

        if mode == "subscribe" and challenge:
            if expected_token and token != expected_token:
                logger.warning(
                    "META webhook verification FAILED -- "
                    "hub.verify_token mismatch (got=%s)",
                    token,
                )
                return JsonResponse({"error": "Verify token mismatch"}, status=403)

            logger.info("META webhook verification -- echoing challenge")
            return HttpResponse(challenge, content_type="text/plain", status=200)

        return JsonResponse({"error": "Invalid verification request"}, status=403)

    # ── POST: event ingestion ─────────────────────────────────────────────

    def post(self, request, *args, **kwargs):
        """
        Ingest a webhook event from META Cloud API.

        Flow:
        1. Verify ``X-Hub-Signature-256``.
        2. Parse JSON body.
        3. Look up ``WAApp`` via ``waba_id`` (and optionally ``phone_number_id``).
        4. Classify event type.
        5. Create ``WAWebhookEvent`` -> triggers signal -> Celery pipeline.
        6. Return 200 immediately.
        """
        from tenants.models import BSPChoices
        from wa.models import WAApp, WAWebhookEvent

        # --- verify signature ---------------------------------------------
        if not _verify_meta_signature(request):
            logger.warning("META webhook: invalid signature – returning 200 anyway")
            return JsonResponse({"status": "ignored", "reason": "invalid_signature"}, status=200)

        # --- parse body ---------------------------------------------------
        try:
            payload: Dict[str, Any] = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            logger.warning("META webhook: invalid JSON body – returning 200 anyway")
            return JsonResponse({"status": "ignored", "reason": "invalid_json"}, status=200)

        # META webhooks have "object": "whatsapp_business_account"
        if payload.get("object") != "whatsapp_business_account":
            logger.info("META webhook: ignoring non-WBA object=%s", payload.get("object"))
            return JsonResponse({"status": "ignored"}, status=200)

        # --- identify the WAApp -------------------------------------------
        waba_id = _extract_meta_waba_id(payload)
        phone_number_id = _extract_meta_phone_number_id(payload)

        if not waba_id:
            logger.warning("META webhook: no WABA ID in payload")
            # Always return 200 to Meta — non-200 causes delivery throttling
            return JsonResponse({"status": "ignored", "reason": "missing_waba_id"}, status=200)

        # Try to match by waba_id first, then fallback to phone_number_id
        wa_app = None
        try:
            wa_app = WAApp.objects.get(waba_id=waba_id, bsp=BSPChoices.META)
        except WAApp.DoesNotExist:
            if phone_number_id:
                try:
                    wa_app = WAApp.objects.get(
                        phone_number_id=phone_number_id,
                        bsp=BSPChoices.META,
                    )
                except WAApp.DoesNotExist:
                    pass

        if wa_app is None:
            logger.warning(
                "META webhook: no META app with waba_id=%s / phone_number_id=%s",
                waba_id,
                phone_number_id,
            )
            # Always return 200 to Meta — non-200 causes delivery throttling
            return JsonResponse({"status": "ignored", "reason": "unknown_app"}, status=200)

        # --- classify & persist -------------------------------------------
        event_type = _classify_cloud_api_event(payload)

        webhook_event = WAWebhookEvent.objects.create(
            wa_app=wa_app,
            event_type=event_type,
            bsp=BSPChoices.META,
            payload=payload,
        )

        logger.info(
            "META webhook ingested: event=%s waba=%s phone=%s pk=%s",
            event_type,
            waba_id,
            phone_number_id,
            webhook_event.pk,
        )

        return JsonResponse(
            {
                "status": "received",
                "event_id": str(webhook_event.pk),
                "event_type": event_type,
            },
            status=200,
        )
