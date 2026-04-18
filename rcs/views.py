"""RCS webhook receivers."""

from __future__ import annotations

import base64
import json
import logging

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from rcs.models import RCSApp, RCSWebhookEvent
from rcs.providers import get_rcs_provider

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class RCSWebhookView(View):
    """Receives all RCS webhook notifications (messages + events) for any provider.

    Google RBM sends Pub/Sub push messages.
    Meta RCS sends WhatsApp-style webhooks.
    """

    def get(self, request, rcs_app_id):
        """Handle Meta webhook verification (challenge response)."""
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")
        if mode == "subscribe" and challenge:
            try:
                rcs_app = RCSApp.objects.get(id=rcs_app_id, is_active=True)
            except RCSApp.DoesNotExist:
                return JsonResponse({"ok": True})
            if token == rcs_app.webhook_client_token:
                return JsonResponse(int(challenge), safe=False)
        return JsonResponse({"ok": True})

    def post(self, request, rcs_app_id):
        try:
            rcs_app = RCSApp.objects.select_related("tenant").get(
                id=rcs_app_id,
                is_active=True,
            )
        except RCSApp.DoesNotExist:
            return JsonResponse({"ok": True})  # Silent — prevent probing

        provider = get_rcs_provider(rcs_app)

        if rcs_app.provider == "META_RCS":
            return self._handle_meta_webhook(request, rcs_app, provider)
        else:
            return self._handle_google_webhook(request, rcs_app, provider)

    def _handle_google_webhook(self, request, rcs_app, provider):
        """Handle Google RBM Pub/Sub push webhook."""
        payload = _decode_pubsub_payload(request)
        if not payload:
            # Always ack Pub/Sub to prevent redelivery storms (#111)
            logger.warning("RCS webhook: failed to decode payload for app %s", rcs_app.id)
            return JsonResponse({"ok": True})

        # Check for Pub/Sub subscription confirmation
        pubsub_event = request.headers.get("ce-type", "")
        if pubsub_event == "google.pubsub.v1.PubsubMessage.SUBSCRIPTION_CONFIRMATION":
            logger.info("Pub/Sub confirmation received for RCS app %s", rcs_app.id)
            return JsonResponse({"ok": True})

        if not provider.validate_webhook_signature(request):
            return JsonResponse({"ok": True})

        # Validate decoded schema — required RBM fields (#111)
        if not _validate_rbm_payload(payload):
            logger.warning(
                "RCS webhook: invalid RBM schema for app %s — payload keys: %s",
                rcs_app.id,
                list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
            )
            return JsonResponse({"ok": True})

        event_type = _classify_event(payload)
        sender_phone = payload.get("senderPhoneNumber", "")
        message_id = payload.get("messageId", "") or payload.get("eventId", "")

        RCSWebhookEvent.objects.get_or_create(
            rcs_app=rcs_app,
            provider_message_id=message_id,
            event_type=event_type,
            defaults={
                "tenant": rcs_app.tenant,
                "provider": rcs_app.provider,
                "sender_phone": sender_phone,
                "payload": payload,
            },
        )
        return JsonResponse({"ok": True})

    def _handle_meta_webhook(self, request, rcs_app, provider):
        """Handle Meta RCS webhook (WhatsApp-style entry[].changes[] format)."""
        if not provider.validate_webhook_signature(request):
            return JsonResponse({"ok": True})

        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, Exception):
            return JsonResponse({"ok": True})

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                for msg in value.get("messages", []):
                    message_id = msg.get("id", "")
                    sender_phone = msg.get("from", "")
                    event_type = _classify_meta_message(msg)

                    RCSWebhookEvent.objects.get_or_create(
                        rcs_app=rcs_app,
                        provider_message_id=message_id,
                        event_type=event_type,
                        defaults={
                            "tenant": rcs_app.tenant,
                            "provider": rcs_app.provider,
                            "sender_phone": sender_phone,
                            "payload": msg,
                        },
                    )

                for status in value.get("statuses", []):
                    message_id = status.get("id", "")
                    recipient = status.get("recipient_id", "")
                    status_map = {"delivered": "DELIVERED", "read": "READ", "sent": "SENT"}
                    event_type = status_map.get(status.get("status", ""), "UNKNOWN")

                    RCSWebhookEvent.objects.get_or_create(
                        rcs_app=rcs_app,
                        provider_message_id=message_id,
                        event_type=event_type,
                        defaults={
                            "tenant": rcs_app.tenant,
                            "provider": rcs_app.provider,
                            "sender_phone": recipient,
                            "payload": status,
                        },
                    )

        return JsonResponse({"ok": True})


def _decode_pubsub_payload(request):
    """Decode Google Pub/Sub push message envelope or direct webhook body (#111).

    Handles two formats:
    1. Pub/Sub: {"message": {"data": "<base64-encoded JSON>", "messageId": "..."}}
    2. Direct: raw RBM JSON body
    """
    try:
        body = json.loads(request.body or b"{}")

        # Pub/Sub envelope detection
        pubsub_data = body.get("message", {})
        if isinstance(pubsub_data, dict) and "data" in pubsub_data:
            encoded_data = pubsub_data["data"]
            decoded = json.loads(base64.b64decode(encoded_data))
            logger.debug("RCS webhook: decoded Pub/Sub envelope (messageId=%s)", pubsub_data.get("messageId", ""))
            return decoded

        # Direct format — return body as-is
        return body
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("RCS webhook: payload decode error — %s", exc)
        return None


def _validate_rbm_payload(payload: dict) -> bool:
    """Validate that decoded RBM payload contains minimum required fields (#111).

    An RBM payload must be a dict and contain at least one of:
    - A message field (text, suggestionResponse, location, userFile)
    - An event field (eventType with a known value)
    - senderPhoneNumber + messageId (delivery/read events)
    """
    if not isinstance(payload, dict):
        return False
    # Message payloads
    if any(k in payload for k in ("text", "suggestionResponse", "location", "userFile")):
        return True
    # Event payloads (DELIVERED, READ, IS_TYPING)
    if payload.get("eventType") in ("DELIVERED", "READ", "IS_TYPING"):
        return True
    # Must have at least messageId or eventId to be useful
    if payload.get("messageId") or payload.get("eventId"):
        return True
    return False


def _classify_event(payload):
    """Classify Google RBM webhook payload into event type."""
    if "text" in payload:
        return "MESSAGE"
    if "suggestionResponse" in payload:
        return "SUGGESTION_RESPONSE"
    if "location" in payload:
        return "LOCATION"
    if "userFile" in payload:
        return "FILE"
    event_type = payload.get("eventType", "")
    if event_type in ("DELIVERED", "READ", "IS_TYPING"):
        return event_type
    return "UNKNOWN"


def _classify_meta_message(msg):
    """Classify Meta RCS inbound message into event type."""
    msg_type = msg.get("type", "text")
    if msg_type == "text":
        return "MESSAGE"
    if msg_type == "interactive":
        return "SUGGESTION_RESPONSE"
    if msg_type == "location":
        return "LOCATION"
    if msg_type in ("image", "video", "audio", "document"):
        return "FILE"
    return "UNKNOWN"
