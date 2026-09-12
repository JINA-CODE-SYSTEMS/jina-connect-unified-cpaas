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
      An unverifiable POST is dropped, not accepted: there is no fail-open
      path when no secret is configured (#306).
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
        message_template_quality_update    -> TEMPLATE
        statuses                           -> STATUS
        billing                            -> BILLING
        account_update / account_alerts    -> ACCOUNT
        phone_number_quality_update        -> ACCOUNT
        phone_number_name_update           -> ACCOUNT
    """
    try:
        field = payload.get("entry", [{}])[0].get("changes", [{}])[0].get("field", "")
    except (IndexError, AttributeError):
        field = ""

    if field in (
        "message_template_status_update",
        "template_category_update",
        # Quality is how a template dies: META drops it to RED and then pauses
        # it. Unclassified, this landed in UNKNOWN and was discarded, so the
        # first visible sign was the pause itself (#267).
        "message_template_quality_update",
    ):
        return "TEMPLATE"
    if field in (
        "account_update",
        "account_alerts",
        # Tier and quality changes for a *number* arrive on their own field,
        # which was not classified at all — so the one push channel reporting
        # them was thrown away.
        "phone_number_quality_update",
        "phone_number_name_update",
    ):
        return "ACCOUNT"
    if field == "billing":
        return "BILLING"

    # META Cloud API sends status/billing/read-receipt webhooks with
    # field="messages" but value containing "statuses" (no "messages"
    # key).  We must inspect the value *contents* to classify correctly.
    if field == "messages":
        try:
            value = payload.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
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
        field,
        payload_type,
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
        return str(payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"])
    except (KeyError, IndexError, TypeError):
        return None


# Reason codes for a rejected META delivery.  META is always answered with
# 200 (a non-200 throttles delivery), so the ``reason`` in the body and the
# log line are the only places a rejection is ever visible — keep the three
# failure modes distinguishable rather than collapsing them into one string.
SIG_OK = ""
SIG_UNVERIFIABLE = "missing_app_secret"
SIG_BAD_HEADER = "malformed_signature_header"
SIG_MISMATCH = "invalid_signature"


def _verify_meta_signature(request) -> str:
    """
    Validate the ``X-Hub-Signature-256`` header against the request body.

    Returns ``SIG_OK`` (the empty string) when the signature is valid,
    otherwise the reason code naming *why* the delivery was rejected.

    There is deliberately **no fail-open path**.  An absent secret used to
    return ``True``, which left this public, unauthenticated endpoint with no
    authentication at all: any well-formed body was accepted, so anyone who
    learned or guessed a ``waba_id`` could inject inbound messages, delivery
    statuses and template decisions into any tenant (#306).

    ``META_WEBHOOK_ALLOW_UNSIGNED`` is a development-only escape hatch for
    replaying captured payloads locally.  It refuses to engage unless
    ``DEBUG`` is also true, so setting it on a production deployment cannot
    silently disable verification.

    One deployment-wide secret cannot serve several client-owned META apps,
    but selecting a per-app secret needs the per-app webhook URL identity from
    #310: until the URL itself names the sender, the only identifier available
    here lives in the body, which cannot be trusted before it is verified.
    """
    app_secret = getattr(django_settings, "META_APP_SECRET", "")
    if not app_secret:
        allow_unsigned = bool(getattr(django_settings, "META_WEBHOOK_ALLOW_UNSIGNED", False))
        if allow_unsigned and not django_settings.DEBUG:
            logger.error(
                "META webhook: META_WEBHOOK_ALLOW_UNSIGNED is set but DEBUG is False -- "
                "refusing to bypass X-Hub-Signature-256 verification (reason=%s)",
                SIG_UNVERIFIABLE,
            )
        elif allow_unsigned:
            logger.warning(
                "META webhook: X-Hub-Signature-256 verification bypassed by "
                "META_WEBHOOK_ALLOW_UNSIGNED -- development builds only"
            )
            return SIG_OK
        else:
            logger.error(
                "META webhook: no app secret configured -- rejecting unverifiable delivery (reason=%s)",
                SIG_UNVERIFIABLE,
            )
        return SIG_UNVERIFIABLE

    signature_header = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
    if not signature_header.startswith("sha256="):
        logger.warning(
            "META webhook: missing or malformed X-Hub-Signature-256 header (reason=%s)",
            SIG_BAD_HEADER,
        )
        return SIG_BAD_HEADER

    expected_sig = signature_header[7:]  # strip "sha256=" prefix
    computed_sig = hmac.new(
        app_secret.encode("utf-8"),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_sig, expected_sig):
        logger.warning("META webhook: X-Hub-Signature-256 mismatch (reason=%s)", SIG_MISMATCH)
        return SIG_MISMATCH

    return SIG_OK


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
        request.GET.get("hub.verify_token")
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
            payload=payload,  # BaseWebhookDumps.payload
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
                    "META webhook verification FAILED -- hub.verify_token mismatch (got=%s)",
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
        3. Look up ``WAApp`` via ``phone_number_id``, falling back to
           ``waba_id`` for events that carry no number.
        4. Classify event type.
        5. Create ``WAWebhookEvent`` -> triggers signal -> Celery pipeline.
        6. Return 200 immediately.
        """
        from tenants.models import BSPChoices
        from wa.models import WAApp, WAWebhookEvent

        # --- verify signature ---------------------------------------------
        # An unverifiable delivery is dropped, never ingested (#306).  The 200
        # is deliberate and must stay: META throttles delivery on non-200
        # responses, so the distinct ``reason`` carries what the status code
        # cannot.
        signature_reason = _verify_meta_signature(request)
        if signature_reason:
            logger.warning("META webhook: dropping unverified delivery (reason=%s)", signature_reason)
            return JsonResponse({"status": "ignored", "reason": signature_reason}, status=200)

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

        # Match on the most specific identifier the payload carries:
        # ``phone_number_id`` first, ``waba_id`` only as a fallback.
        #
        # One ``TenantWAApp`` holds one number, so a tenant with several
        # numbers holds several rows — and those rows may share a ``waba_id``.
        # ``waba_id`` is therefore not a unique routing key. Matching it first
        # and taking ``.first()`` filed every event for every number on a
        # shared WABA against whichever row the database happened to return,
        # and the ``phone_number_id`` fallback could never correct it because
        # it was guarded on the WABA match having failed (#309).
        #
        # ``bsp_q`` rather than ``bsp=META`` because a blank column means
        # META too — filtering on the literal answered those apps' webhooks
        # with "unknown_app" while every other path served them (#265).
        from wa.adapters import bsp_q

        meta_apps = WAApp.objects.filter(bsp_q(BSPChoices.META))

        wa_app = None
        if phone_number_id:
            wa_app = meta_apps.filter(phone_number_id=phone_number_id).first()

        ambiguous_waba_apps: list = []
        if wa_app is None:
            # No number in the payload — account-level updates legitimately
            # carry none — or no row holds it. Fall back to the WABA, but only
            # when it identifies exactly one app. Two or more and there is no
            # non-arbitrary answer, which is the same conclusion
            # ``MetaDirectAdapter.fetch_waba_info`` reaches on a shared WABA.
            waba_matches = list(meta_apps.filter(waba_id=waba_id).order_by("created_at", "id")[:2])
            if len(waba_matches) == 1:
                wa_app = waba_matches[0]
            elif len(waba_matches) > 1:
                ambiguous_waba_apps = waba_matches

        if wa_app is None and not ambiguous_waba_apps:
            logger.warning(
                "META webhook: no META app with waba_id=%s / phone_number_id=%s",
                waba_id,
                phone_number_id,
            )
            # Always return 200 to Meta — non-200 causes delivery throttling
            return JsonResponse({"status": "ignored", "reason": "unknown_app"}, status=200)

        # --- classify & persist -------------------------------------------
        event_type = _classify_cloud_api_event(payload)

        if ambiguous_waba_apps:
            # Recorded, not attributed. The payload is kept so the event is not
            # lost, but it is stored already-processed with the ambiguity in
            # ``error_message``, so nothing downstream applies it to an app it
            # may not belong to. The FK has to point somewhere (it is NOT NULL,
            # and a migration is out of scope here), so it points at the oldest
            # matching app — deterministically, not arbitrarily — and the
            # message says plainly that attribution was refused. An operator
            # finds these by searching ``error_message`` in the admin, and once
            # the owning app's ``phone_number_id`` is filled in, the existing
            # "Reprocess selected webhook events" action replays it.
            holder = ambiguous_waba_apps[0]
            detail = (
                f"Ambiguous routing: waba_id={waba_id} matches {len(ambiguous_waba_apps)} META apps "
                f"({', '.join(str(app.pk) for app in ambiguous_waba_apps)}) and the payload carries no "
                f"phone_number_id, so this event was recorded rather than attributed to any of them. "
                f"Stored against {holder.pk} for retention only (#309)."
            )
            webhook_event = WAWebhookEvent.objects.create(
                wa_app=holder,
                event_type=event_type,
                bsp=BSPChoices.META,
                payload=payload,
                is_processed=True,
                error_message=detail,
            )
            logger.error(
                "META webhook: ambiguous waba_id=%s (%s candidate apps, no phone_number_id) -- "
                "recorded unattributed as pk=%s",
                waba_id,
                len(ambiguous_waba_apps),
                webhook_event.pk,
            )
            return JsonResponse(
                {
                    "status": "recorded",
                    "reason": "ambiguous_waba_id",
                    "event_id": str(webhook_event.pk),
                    "event_type": event_type,
                },
                status=200,
            )

        webhook_event = WAWebhookEvent.objects.create(
            wa_app=wa_app,
            event_type=event_type,
            bsp=BSPChoices.META,
            payload=payload,
        )

        logger.info(
            "META webhook ingested: event=%s waba=%s phone=%s app=%s pk=%s",
            event_type,
            waba_id,
            phone_number_id,
            wa_app.pk,
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
