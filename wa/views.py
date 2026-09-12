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
    POST /wa/v2/webhooks/gupshup/         -- Gupshup callback receiver (legacy, shared)
    GET  /wa/v2/webhooks/gupshup/         -- Gupshup verification (hub.challenge)
    POST /wa/v2/webhooks/gupshup/<id>/    -- Gupshup callback receiver for one app
    GET  /wa/v2/webhooks/gupshup/<id>/    -- Gupshup verification for one app
    POST /wa/v2/webhooks/meta/            -- META Cloud API callback receiver (legacy, shared)
    GET  /wa/v2/webhooks/meta/            -- META verification (hub.challenge)
    POST /wa/v2/webhooks/meta/<id>/       -- META callback receiver for one app
    GET  /wa/v2/webhooks/meta/<id>/       -- META verification for one app

``<id>`` is ``TenantWAApp.webhook_identifier``: an opaque, unguessable string
that names the sending app *in the path*, so the app is known before the body is
parsed and a per-app secret can be selected (#310). See
``wa.services.webhook_identity``.

The two shapes are both permanent, and they differ in one important way:

* **The suffixed path identifies one app.** Each client registers their own
  URL, and a delivery to it is attributed from the URL, not from the body.
* **The unsuffixed path is single-app.** It authenticates against the
  deployment-wide ``META_APP_SECRET`` / verify token, so it cannot tell two
  clients' apps apart and must not be shared between clients. It behaves
  exactly as it always has — self-hosters and the live deployment have it
  registered in Meta's App Dashboard, and an upgrade must not require anyone to
  re-register a URL.
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


# ──────────────────────────────────────────────────────────────────────────────
# Per-app webhook identity (#310)
# ──────────────────────────────────────────────────────────────────────────────

# Reason codes for a delivery addressed to a per-app URL that resolves to
# nothing usable. Like the signature reasons below, these travel in a 200 body:
# a non-200 makes META throttle delivery to the whole deployment, so every
# client's events would slow down because one client's URL is stale.
APP_UNKNOWN_IDENTIFIER = "unknown_webhook_identifier"
APP_WRONG_BSP = "identifier_belongs_to_other_bsp"


def _mask_webhook_identifier(webhook_identifier: Optional[str]) -> str:
    """The only form of an identifier that may appear in a log line."""
    from wa.services import webhook_identity

    return webhook_identity.mask(webhook_identifier)


def _resolve_webhook_app(bsp: str, webhook_identifier: str):
    """Resolve the app a per-app webhook URL names.

    Returns ``(wa_app, reason)``: exactly one of the two is set. A reason means
    the caller answers 200, writes nothing, and is done.

    One indexed query (see ``webhook_identity.resolve_app``), so the cost is the
    same whether the instance hosts one app or a hundred.

    The identifier is never logged in full. It is the whole of the authority to
    address an app's receiver — a log sink is a wider audience than the client
    who was given the URL — so only the masked hint goes out, which is still
    enough to tell a burst of scans from one client's stale dashboard entry.
    """
    from wa.services import webhook_identity

    wa_app = webhook_identity.resolve_app(webhook_identifier)

    if wa_app is None:
        # Counted, not stored: a public URL shape invites scanning, and an
        # unauthenticated INSERT per junk request is a worse problem than the
        # one being measured. D-7's durable aggregate is its own piece of work.
        total = webhook_identity.record_unknown_identifier(bsp)
        logger.warning(
            "%s webhook: no app owns this webhook identifier (hint=%s, reason=%s, today=%s)",
            bsp,
            _mask_webhook_identifier(webhook_identifier),
            APP_UNKNOWN_IDENTIFIER,
            total,
        )
        return None, APP_UNKNOWN_IDENTIFIER

    from wa.adapters import resolve_bsp

    app_bsp = resolve_bsp(wa_app)
    if app_bsp != bsp:
        # The identifier is real but it was pasted under the wrong receiver —
        # a client copying one URL into another BSP's dashboard. Distinguished
        # from "unknown" because the fix is different, and counted the same way
        # because the delivery is still refused.
        webhook_identity.record_unknown_identifier(bsp)
        logger.warning(
            "%s webhook: identifier belongs to a %s app (hint=%s, app=%s, reason=%s)",
            bsp,
            app_bsp,
            _mask_webhook_identifier(webhook_identifier),
            wa_app.pk,
            APP_WRONG_BSP,
        )
        return None, APP_WRONG_BSP

    return wa_app, ""


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

    One deployment-wide secret cannot serve several client-owned META apps.
    #310 removed the reason it had to: on a per-app URL the path names the
    sending app, so ``MetaWebhookView.post`` knows the app before it reads a
    byte of the body and can hand the right secret down here. It does not yet,
    deliberately — the per-app app-secret column is #311 and the verification
    that reads it is #306's second half. Until those land, both the legacy and
    the per-app path verify against ``settings.META_APP_SECRET``, which is a
    correct single-app deployment and an honest unverifiable one otherwise.
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

    Served at two paths, the same pair as the META receiver (#310): the
    unsuffixed legacy path, which identifies the app from ``gs_app_id`` in the
    body, and ``/wa/v2/webhooks/gupshup/<webhook_identifier>/``, which
    identifies it from the URL. Per-app identity is defined for every BSP, not
    only META (#305 D-4) — a Meta-only version of this would have to be undone
    when Embedded Signup (#258) lands beside bring-your-own-app.
    """

    # ── GET: verification handshake ───────────────────────────────────────

    def get(self, request, *args, webhook_identifier=None, **kwargs):
        """
        Gupshup webhook verification.

        Gupshup sends a GET with ``hub.mode=subscribe``,
        ``hub.verify_token=<your_token>``, and ``hub.challenge=<int>``.

        We verify the presented token against ``GUPSHUP_WEBHOOK_VERIFY_TOKEN``
        and only then echo back the challenge, so that reaching the endpoint is
        not by itself enough to claim ownership of it.

        Unset-secret behaviour: when ``GUPSHUP_WEBHOOK_VERIFY_TOKEN`` is empty
        the token check is skipped and the challenge is echoed — the same
        "configure the secret to enable the check" rule ``MetaWebhookView.get``
        applies to ``META_WEBHOOK_VERIFY_TOKEN``, kept identical on purpose so
        the two handshakes cannot drift apart.

        The presented token is never logged: it is an attacker-supplied guess
        at a shared secret, and log sinks are a wider audience than the secret.

        On a per-app URL the identifier is resolved first, so an unowned one
        cannot be made to echo a challenge (see ``MetaWebhookView.get`` for why
        that answer is a 403 while an unowned *delivery* is a 200).
        """
        from wa.models import BSPChoices

        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        if webhook_identifier is not None:
            _app, identity_reason = _resolve_webhook_app(BSPChoices.GUPSHUP, webhook_identifier)
            if identity_reason:
                return JsonResponse({"error": "Unknown webhook URL", "reason": identity_reason}, status=403)

        # SEAM (#307): the app resolved above is the one whose own verify token
        # this should check. Per-app verify-token validation is #307.
        expected_token = getattr(django_settings, "GUPSHUP_WEBHOOK_VERIFY_TOKEN", "")

        if mode == "subscribe" and challenge:
            if expected_token and token != expected_token:
                logger.warning("Gupshup webhook verification FAILED — hub.verify_token mismatch")
                return JsonResponse({"error": "Verify token mismatch"}, status=403)

            logger.info("Gupshup webhook verification — echoing challenge")
            return HttpResponse(challenge, content_type="text/plain", status=200)

        return JsonResponse({"error": "Invalid verification request"}, status=403)

    # ── POST: event ingestion ─────────────────────────────────────────────

    def post(self, request, *args, webhook_identifier=None, **kwargs):
        """
        Ingest a webhook event from Gupshup.

        Flow:
        0. On a per-app URL, resolve the app from the path (#310).
        1. Parse JSON body.
        2. On the legacy path, look up ``WAApp`` via ``gs_app_id``.
        3. Classify event type (TEMPLATE, MESSAGE, STATUS, …).
        4. Create ``WAWebhookEvent`` → triggers signal → Celery pipeline.
        5. Return 200 immediately (processing is async).
        """
        from tenants.models import BSPChoices
        from wa.models import WAApp, WAWebhookEvent

        # --- identify the app from the URL, if this is a per-app URL -------
        # Before the body is parsed, deliberately: the ordering is the point of
        # #310, and it is the same here as on the META receiver even though
        # Gupshup offers no signature to verify — a receiver whose two BSPs
        # answer an unowned URL differently is a receiver someone will have to
        # reason about twice.
        url_app = None
        if webhook_identifier is not None:
            url_app, identity_reason = _resolve_webhook_app(BSPChoices.GUPSHUP, webhook_identifier)
            if identity_reason:
                return JsonResponse({"status": "ignored", "reason": identity_reason}, status=200)

        # --- parse body ---------------------------------------------------
        try:
            payload: Dict[str, Any] = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Gupshup webhook: invalid JSON body")
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # --- identify the WAApp -------------------------------------------
        if url_app is not None:
            # The URL said which app this is, so ``gs_app_id`` is not needed and
            # is not required: an event missing it is no longer unroutable. It is
            # still cross-checked, because a mismatch means a client pasted one
            # app's URL into another app's Gupshup settings and nothing else
            # would ever say so.
            wa_app = url_app
            gs_app_id = _extract_gs_app_id(payload)
            if gs_app_id and wa_app.app_id and gs_app_id != wa_app.app_id:
                logger.warning(
                    "Gupshup webhook: per-app URL for app=%s received an event for gs_app_id=%s "
                    "(app holds %s) -- recorded against the URL's app",
                    wa_app.pk,
                    gs_app_id,
                    wa_app.app_id,
                )
        else:
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
            "Gupshup webhook ingested: event=%s app=%s wa_app=%s hint=%s pk=%s",
            event_type,
            gs_app_id,
            wa_app.pk,
            _mask_webhook_identifier(webhook_identifier),
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

    Served at two paths (#310):

    * ``/wa/v2/webhooks/meta/<webhook_identifier>/`` — one client's app. The
      path names the app, so it is known before the body is parsed.
    * ``/wa/v2/webhooks/meta/`` — the legacy, single-app path. Unchanged: it
      routes from the body and verifies against the deployment-wide secret,
      because it is the URL already registered in live App Dashboards.

    On the legacy path META identifies the app via ``entry[0].id`` (WABA ID) and
    ``entry[0].changes[0].value.metadata.phone_number_id``.
    """

    # ── GET: verification handshake ───────────────────────────────────────

    def get(self, request, *args, webhook_identifier=None, **kwargs):
        """
        META webhook verification.

        META sends a GET with ``hub.mode=subscribe``,
        ``hub.verify_token=<your_token>``, and ``hub.challenge=<int>``.

        We verify the token against ``META_WEBHOOK_VERIFY_TOKEN`` and
        echo back the challenge.

        On a per-app URL the identifier is resolved first, so a handshake
        against an identifier no app owns cannot be made to echo a challenge —
        answering it would tell a scanner that the URL shape is live. 403, not
        the 200 a *delivery* gets: a failed handshake is visible to the person
        clicking "Verify and save" in a dashboard, which is exactly who needs to
        see it, and it is not the POST traffic META throttles on non-200s.
        """
        from wa.models import BSPChoices

        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        wa_app = None
        if webhook_identifier is not None:
            wa_app, reason = _resolve_webhook_app(BSPChoices.META, webhook_identifier)
            if reason:
                return JsonResponse({"error": "Unknown webhook URL", "reason": reason}, status=403)

        # SEAM (#307): ``wa_app`` is the app whose own verify token this
        # handshake should be checking — ``WASubscription.verify_token`` already
        # exists for it, unwired. Validating it per app is #307 and is
        # deliberately not done here; until then every handshake, on either
        # path, checks the one deployment-wide token.
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

    def post(self, request, *args, webhook_identifier=None, **kwargs):
        """
        Ingest a webhook event from META Cloud API.

        Flow:
        0. On a per-app URL, resolve the app from the path (#310) — one indexed
           query, before anything in the body is believed.
        1. Verify ``X-Hub-Signature-256``.
        2. Parse JSON body.
        3. Identify the ``WAApp``: the path on a per-app URL; on the legacy path
           ``phone_number_id``, falling back to ``waba_id`` for events that carry
           no number.
        4. Classify event type.
        5. Create ``WAWebhookEvent`` -> triggers signal -> Celery pipeline.
        6. Return 200 immediately.
        """
        from tenants.models import BSPChoices
        from wa.models import WAApp, WAWebhookEvent

        # --- identify the app from the URL, if this is a per-app URL -------
        # First, because this is the step the rest of #305 is waiting on: with
        # the app known here, the secret used one line below becomes selectable
        # per app. An unknown identifier is answered 200 with nothing written.
        url_app = None
        if webhook_identifier is not None:
            url_app, identity_reason = _resolve_webhook_app(BSPChoices.META, webhook_identifier)
            if identity_reason:
                return JsonResponse({"status": "ignored", "reason": identity_reason}, status=200)

        # --- verify signature ---------------------------------------------
        # An unverifiable delivery is dropped, never ingested (#306).  The 200
        # is deliberate and must stay: META throttles delivery on non-200
        # responses, so the distinct ``reason`` carries what the status code
        # cannot.
        #
        # SEAM (#306 second half, #311): when ``url_app`` is set, that app's own
        # secret is the one that should key this HMAC. The column to read it
        # from is #311 (one more ``EncryptedTextField``, per #289's pattern) and
        # passing it in is #306's second half. Neither is half-done here: today
        # both paths verify against the deployment-wide ``META_APP_SECRET``,
        # which is correct for a single-app deployment and honestly
        # unverifiable for any other — the same behaviour as before #310.
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

        # --- per-app URL: the path already said which app this is ----------
        # Returns here rather than falling through, so the body-routing block
        # below stays exactly what it was for the legacy path: #309's
        # precedence rule and its ambiguous-WABA branch are not re-litigated by
        # this ticket, and a URL that names one app has no ambiguity to resolve.
        if url_app is not None:
            event_type = _classify_cloud_api_event(payload)
            webhook_event = WAWebhookEvent.objects.create(
                wa_app=url_app,
                event_type=event_type,
                bsp=BSPChoices.META,
                payload=payload,
            )

            # A cross-check, not a gate. The URL is the identity; the body's
            # identifiers are the sender's claim about itself. They disagreeing
            # is worth a line — a client pasting one app's URL into another
            # app's dashboard looks exactly like this — but the event still
            # belongs to the app whose URL received it, and refusing it would
            # throw away a delivery we were correctly given.
            body_phone_number_id = _extract_meta_phone_number_id(payload)
            if body_phone_number_id and url_app.phone_number_id and body_phone_number_id != url_app.phone_number_id:
                logger.warning(
                    "META webhook: per-app URL for app=%s received an event for phone_number_id=%s "
                    "(app holds %s) -- recorded against the URL's app",
                    url_app.pk,
                    body_phone_number_id,
                    url_app.phone_number_id,
                )

            logger.info(
                "META webhook ingested via per-app URL: event=%s app=%s hint=%s pk=%s",
                event_type,
                url_app.pk,
                _mask_webhook_identifier(webhook_identifier),
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

        # --- identify the WAApp (legacy path: from the body) ---------------
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
