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
    - Gupshup endpoint: unauthenticated deliveries (HMAC not yet supported by
      GS); its verification handshake checks ``hub.verify_token`` (#308).
    - META endpoint: validates ``X-Hub-Signature-256`` (HMAC-SHA256 keyed on
      the sending app's own ``TenantWAApp.meta_app_secret`` where it has one,
      and on the deployment-wide ``META_APP_SECRET`` otherwise) and
      ``hub.verify_token`` during verification. An unverifiable POST is
      dropped, not accepted: there is no fail-open path when no secret is
      available (#306).
    - Both handshakes check ``hub.verify_token`` against the *addressed app's
      own* ``TenantWAApp.webhook_verify_token`` where it has one, and against
      the deployment-wide ``<BSP>_WEBHOOK_VERIFY_TOKEN`` setting otherwise
      (#307). One shared token would have to be handed to every client, which
      makes it a secret across tenants; a mismatch is 403, counted per app, and
      logged without the token that was presented.
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
  URL, a delivery to it is attributed from the URL rather than from the body,
  and it is authenticated against *that app's* own Meta app secret (#306).
* **The unsuffixed path is single-app.** It authenticates against the
  deployment-wide ``META_APP_SECRET`` / verify token — it has no app to ask for
  one of its own, on a handshake there is no body to route from — so it cannot
  tell two clients' apps apart and must not be shared between clients. It behaves
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


# ──────────────────────────────────────────────────────────────────────────────
# The verification handshake (#307)
# ──────────────────────────────────────────────────────────────────────────────
#
# Both receivers' GET handlers share these two helpers rather than each writing
# the comparison and the rejection out. The rule is one rule — measure
# ``hub.verify_token`` against whatever ``select_verify_token`` says this URL
# expects, which is the addressed app's own token when it has one — and #308
# shipped because the two handshakes had drifted into two implementations of it,
# one of which compared nothing at all.


def _tokens_match(presented: Optional[str], expected: str) -> bool:
    """Whether *presented* is *expected*, compared in constant time.

    ``hmac.compare_digest`` for the same reason ``_verify_meta_signature`` uses
    it on the HMAC: ``!=`` on secrets leaks their shared prefix through timing,
    and this endpoint takes as many guesses as anyone cares to send it. Tokens
    are ASCII by construction (``secrets.token_urlsafe`` plus a prefix), but a
    presented one is arbitrary client input, so both sides are encoded before
    comparison — ``compare_digest`` refuses non-ASCII ``str``.
    """
    return hmac.compare_digest((presented or "").encode("utf-8"), expected.encode("utf-8"))


def _refuse_handshake(bsp: str, wa_app=None, token_scope: str = "") -> JsonResponse:
    """Refuse a verification handshake: count it, log it, answer 403.

    403 rather than the 200 a rejected *delivery* gets. A failed handshake is
    read by the person clicking "Verify and save" in a BSP dashboard, which is
    exactly who needs to see it, and it is not the POST traffic META throttles
    the whole deployment's delivery on.

    Counted because nothing else would notice (#307). A client re-verifying their
    URL with a stale token and someone guessing at a live one look identical from
    here, and both matter: the counter is bucketed per app so it says *whose*
    endpoint is being refused.

    The presented token is never logged, and neither is the expected one. A
    presented token is a guess at a secret — or another tenant's real token sent
    to the wrong endpoint — and a log sink is a wider audience than either owner
    agreed to. ``token_scope`` carries the diagnosis instead: refused against the
    app's own token means a client holding the wrong value, refused against the
    deployment-wide one means an app that has no token of its own yet.
    """
    from wa.services import webhook_identity

    total = webhook_identity.record_verify_token_rejection(bsp, wa_app=wa_app)
    logger.warning(
        "%s webhook verification FAILED -- hub.verify_token mismatch (app=%s, token_scope=%s, today=%s)",
        bsp,
        getattr(wa_app, "pk", None) or "legacy-path",
        token_scope,
        total,
    )
    return JsonResponse({"error": "Verify token mismatch"}, status=403)


# Reason codes for a rejected META delivery.  META is always answered with
# 200 (a non-200 throttles delivery), so the ``reason`` in the body and the
# log line are the only places a rejection is ever visible — keep the four
# failure modes distinguishable rather than collapsing them into one string.
SIG_OK = ""
#: No secret anywhere and no app to have one: the legacy unsuffixed receiver on
#: a deployment with ``META_APP_SECRET`` unset. A configuration fault of the
#: deployment.
SIG_UNVERIFIABLE = "missing_app_secret"
#: A per-app URL resolved a real app, but neither that app's own
#: ``meta_app_secret`` nor the deployment-wide setting is available. Its own
#: code because its own fix: this one names a client whose onboarding is
#: incomplete, and the operator can go and ask *them* for the secret. Collapsed
#: into ``missing_app_secret`` it would read as "the deployment is
#: misconfigured" and send whoever is paged to the wrong place.
SIG_APP_SECRET_MISSING = "app_secret_not_configured"
SIG_BAD_HEADER = "malformed_signature_header"
SIG_MISMATCH = "invalid_signature"


def _verify_meta_signature(request, wa_app=None) -> tuple[str, str]:
    """
    Validate the ``X-Hub-Signature-256`` header against the request body.

    Returns ``(reason, secret_scope)``.  *reason* is ``SIG_OK`` (the empty
    string) when the signature is valid, otherwise the code naming *why* the
    delivery was rejected.  *secret_scope* is one of
    ``webhook_identity.SECRET_SCOPE_*`` and says which secret this request was
    actually checked against, so the caller can log it: a verified delivery
    means nothing without naming the key that verified it.

    **Per-app selection (#306's second half).** *wa_app* is the app the URL
    named, resolved before a byte of the body was read (#310).  Its own
    ``meta_app_secret`` (#311) keys the HMAC when it has one, which is what
    makes N client-owned apps with N different secrets verifiable at once —
    ``X-Hub-Signature-256`` is a symmetric HMAC keyed on the *sending* app's
    secret, and one deployment-wide value could only ever match one client's.
    A body signed by app A and delivered to app B's URL therefore fails as
    ``SIG_MISMATCH``, which is the whole point.

    An app with no secret of its own falls back to the deployment-wide setting
    rather than being refused outright: that is the pre-#311 configuration every
    existing install is in, and an upgrade must not stop verifying their
    traffic. ``SIG_APP_SECRET_MISSING`` is for when that fallback is empty too.

    *wa_app* is ``None`` on the legacy unsuffixed receiver, which has no
    identity at this point in the request and so has only the shared secret.
    That path is byte-for-byte what it was.

    There is deliberately **no fail-open path**.  An absent secret used to
    return ``True``, which left this public, unauthenticated endpoint with no
    authentication at all: any well-formed body was accepted, so anyone who
    learned or guessed a ``waba_id`` could inject inbound messages, delivery
    statuses and template decisions into any tenant (#306).

    ``META_WEBHOOK_ALLOW_UNSIGNED`` is a development-only escape hatch for
    replaying captured payloads locally.  It refuses to engage unless
    ``DEBUG`` is also true, so setting it on a production deployment cannot
    silently disable verification.
    """
    from wa.services import webhook_identity

    app_secret, secret_scope = webhook_identity.select_app_secret(wa_app)

    if not app_secret:
        # Which of the two no-secret faults this is depends on whether an app
        # was identified at all — see the reason codes above for why they are
        # not one code.
        reason = SIG_APP_SECRET_MISSING if wa_app is not None else SIG_UNVERIFIABLE
        allow_unsigned = bool(getattr(django_settings, "META_WEBHOOK_ALLOW_UNSIGNED", False))
        if allow_unsigned and not django_settings.DEBUG:
            logger.error(
                "META webhook: META_WEBHOOK_ALLOW_UNSIGNED is set but DEBUG is False -- "
                "refusing to bypass X-Hub-Signature-256 verification (reason=%s, app=%s)",
                reason,
                getattr(wa_app, "pk", None),
            )
        elif allow_unsigned:
            logger.warning(
                "META webhook: X-Hub-Signature-256 verification bypassed by "
                "META_WEBHOOK_ALLOW_UNSIGNED -- development builds only"
            )
            return SIG_OK, secret_scope
        else:
            logger.error(
                "META webhook: no app secret available -- rejecting unverifiable delivery (reason=%s, app=%s)",
                reason,
                getattr(wa_app, "pk", None),
            )
        return reason, secret_scope

    signature_header = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
    if not signature_header.startswith("sha256="):
        logger.warning(
            "META webhook: missing or malformed X-Hub-Signature-256 header (reason=%s, app=%s)",
            SIG_BAD_HEADER,
            getattr(wa_app, "pk", None),
        )
        return SIG_BAD_HEADER, secret_scope

    expected_sig = signature_header[7:]  # strip "sha256=" prefix
    computed_sig = hmac.new(
        app_secret.encode("utf-8"),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_sig, expected_sig):
        # ``secret_scope`` is the diagnosis, not decoration. "Mismatch against
        # the app's own secret" is a rotated client secret; "mismatch against
        # the deployment secret" on a per-app URL is an onboarded client whose
        # secret was never stored, signing with a key this deployment has never
        # seen. Same rejection, different phone call.
        logger.warning(
            "META webhook: X-Hub-Signature-256 mismatch (reason=%s, app=%s, secret_scope=%s)",
            SIG_MISMATCH,
            getattr(wa_app, "pk", None),
            secret_scope,
        )
        return SIG_MISMATCH, secret_scope

    return SIG_OK, secret_scope


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

        We verify the presented token against the one this URL expects and only
        then echo back the challenge, so that reaching the endpoint is not by
        itself enough to claim ownership of it.

        Which token that is comes from
        ``webhook_identity.select_verify_token`` (#307): on a per-app URL the
        resolved app's own ``webhook_verify_token``, and
        ``GUPSHUP_WEBHOOK_VERIFY_TOKEN`` only for an app that has none or on the
        legacy path, which has no app to ask. One shared token handed to every
        client is a secret across tenants — any holder could complete the
        handshake for another client's endpoint.

        Unset-token behaviour: when neither is configured there is nothing to
        compare against, the check is skipped and the challenge is echoed — the
        same "configure the secret to enable the check" rule
        ``MetaWebhookView.get`` applies, kept identical on purpose so the two
        handshakes cannot drift apart.

        The presented token is never logged: it is an attacker-supplied guess
        at a shared secret, and log sinks are a wider audience than the secret.

        On a per-app URL the identifier is resolved first, so an unowned one
        cannot be made to echo a challenge (see ``MetaWebhookView.get`` for why
        that answer is a 403 while an unowned *delivery* is a 200).
        """
        from wa.models import BSPChoices
        from wa.services import webhook_identity

        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        wa_app = None
        if webhook_identifier is not None:
            wa_app, identity_reason = _resolve_webhook_app(BSPChoices.GUPSHUP, webhook_identifier)
            if identity_reason:
                return JsonResponse({"error": "Unknown webhook URL", "reason": identity_reason}, status=403)

        # The app's own token over the deployment-wide setting (#307), selected
        # through the same registry-driven helper the META handshake uses —
        # per-app verify tokens are defined for every BSP, not bolted onto one
        # (#305 D-4).
        expected_token, token_scope = webhook_identity.select_verify_token(BSPChoices.GUPSHUP, wa_app=wa_app)

        if mode == "subscribe" and challenge:
            if expected_token and not _tokens_match(token, expected_token):
                return _refuse_handshake(BSPChoices.GUPSHUP, wa_app=wa_app, token_scope=token_scope)

            logger.info(
                "Gupshup webhook verification — echoing challenge (app=%s, token_scope=%s)",
                wa_app.pk if wa_app is not None else "legacy-path",
                token_scope,
            )
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
      path names the app, so it is known before the body is parsed, which is
      what lets the HMAC be keyed on that app's own secret (#306).
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

        We verify the token against the one this URL expects and echo back the
        challenge. On a per-app URL that is the resolved app's own
        ``webhook_verify_token``, and ``META_WEBHOOK_VERIFY_TOKEN`` only for an
        app that has none or on the legacy path (#307) — see
        ``webhook_identity.select_verify_token``, which is the same selection
        ``_verify_meta_signature`` makes for the POST's signing secret. App A's
        token therefore fails on app B's endpoint, which a single
        deployment-wide token handed to every client could not achieve.

        Neither the presented token nor the expected one is ever logged; see
        ``_refuse_handshake``.

        On a per-app URL the identifier is resolved first, so a handshake
        against an identifier no app owns cannot be made to echo a challenge —
        answering it would tell a scanner that the URL shape is live. 403, not
        the 200 a *delivery* gets: a failed handshake is visible to the person
        clicking "Verify and save" in a dashboard, which is exactly who needs to
        see it, and it is not the POST traffic META throttles on non-200s.
        """
        from wa.models import BSPChoices
        from wa.services import webhook_identity

        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        wa_app = None
        if webhook_identifier is not None:
            wa_app, reason = _resolve_webhook_app(BSPChoices.META, webhook_identifier)
            if reason:
                return JsonResponse({"error": "Unknown webhook URL", "reason": reason}, status=403)

        # Per app, over the deployment-wide setting (#307) — the same selection
        # ``_verify_meta_signature`` makes for the POST's HMAC, one line further
        # down the same identity.
        expected_token, token_scope = webhook_identity.select_verify_token(BSPChoices.META, wa_app=wa_app)

        if mode == "subscribe" and challenge:
            if expected_token and not _tokens_match(token, expected_token):
                return _refuse_handshake(BSPChoices.META, wa_app=wa_app, token_scope=token_scope)

            logger.info(
                "META webhook verification -- echoing challenge (app=%s, token_scope=%s)",
                wa_app.pk if wa_app is not None else "legacy-path",
                token_scope,
            )
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
        from wa.services import webhook_identity

        # --- identify the app from the URL, if this is a per-app URL -------
        # First, because the secret used one line below is selected from it: the
        # app has to be known before the HMAC can be keyed, and the only
        # identifiers inside the body are worthless until that HMAC checks out
        # (#310). An unknown identifier is answered 200 with nothing written.
        url_app = None
        if webhook_identifier is not None:
            url_app, identity_reason = _resolve_webhook_app(BSPChoices.META, webhook_identifier)
            if identity_reason:
                return JsonResponse({"status": "ignored", "reason": identity_reason}, status=200)

        # --- verify signature ---------------------------------------------
        # Keyed on ``url_app``'s own ``meta_app_secret`` when it has one, and on
        # the deployment-wide secret otherwise (#306's second half, over #311's
        # column). ``secret_scope`` records which of the two actually ran, and
        # travels into every log line below — an ingested event whose log does
        # not say what verified it is the state the original fail-open hid in.
        #
        # An unverifiable delivery is dropped, never ingested. The 200 is
        # deliberate and must stay: META throttles delivery to the whole
        # deployment on non-200 responses, so one client's rotated secret would
        # slow every other client's events down. That is exactly why the
        # rejection is *counted* — with no status code to notice, the counter and
        # the reason code are the only trace a client has gone quiet.
        signature_reason, secret_scope = _verify_meta_signature(request, wa_app=url_app)
        if signature_reason:
            webhook_identity.record_signature_rejection(BSPChoices.META, signature_reason, wa_app=url_app)
            logger.warning(
                "META webhook: dropping unverified delivery (reason=%s, app=%s, secret_scope=%s)",
                signature_reason,
                getattr(url_app, "pk", None),
                secret_scope,
            )
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

            # ``secret_scope`` is on the accepted line too, not only the
            # rejected one: "app" says this client's own secret verified their
            # own delivery, "deployment" says they are still on the shared
            # secret and are not yet separable from any other client on it.
            logger.info(
                "META webhook ingested via per-app URL: event=%s app=%s hint=%s pk=%s secret_scope=%s",
                event_type,
                url_app.pk,
                _mask_webhook_identifier(webhook_identifier),
                webhook_event.pk,
                secret_scope,
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
            "META webhook ingested: event=%s waba=%s phone=%s app=%s pk=%s secret_scope=%s",
            event_type,
            waba_id,
            phone_number_id,
            wa_app.pk,
            webhook_event.pk,
            secret_scope,
        )

        return JsonResponse(
            {
                "status": "received",
                "event_id": str(webhook_event.pk),
                "event_type": event_type,
            },
            status=200,
        )
