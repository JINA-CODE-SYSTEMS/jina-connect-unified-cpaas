"""
Telegram webhook receiver — public, unauthenticated endpoint.

Telegram POSTs Update objects here. We validate the secret token,
persist the event, and return 200 immediately. Processing happens
asynchronously via the post_save signal → Celery task.
"""

from __future__ import annotations

import hmac
import json
import logging

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from telegram.constants import UPDATE_TYPE_MAP
from telegram.models import TelegramBotApp, TelegramWebhookEvent

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class TelegramWebhookView(View):
    """
    POST /telegram/v1/webhooks/<uuid:bot_app_id>/
        Receive Telegram webhook updates.

    GET  /telegram/v1/webhooks/<uuid:bot_app_id>/
        Health check — returns 200 OK.
    """

    def get(self, request, bot_app_id):
        """Health check — Telegram doesn't use GET, but useful for monitoring."""
        return JsonResponse({"ok": True})

    def post(self, request, bot_app_id):
        """Receive and persist a Telegram Update."""
        # 0. Payload size guard (Telegram updates are typically < 64 KB)
        MAX_PAYLOAD_BYTES = 1 * 1024 * 1024  # 1 MB
        if int(request.headers.get("Content-Length", 0)) > MAX_PAYLOAD_BYTES or len(request.body) > MAX_PAYLOAD_BYTES:
            return JsonResponse({"ok": True})

        # 1. Resolve bot app
        try:
            bot_app = TelegramBotApp.objects.get(pk=bot_app_id, is_active=True)
        except TelegramBotApp.DoesNotExist:
            logger.warning("[TelegramWebhookView] Unknown or inactive bot_app_id=%s", bot_app_id)
            # Return 200 so Telegram doesn't retry for unknown bots
            return JsonResponse({"ok": True})

        # 2. Validate secret token
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(secret_header, bot_app.webhook_secret):
            logger.warning(
                "[TelegramWebhookView] Invalid secret token for bot_app_id=%s",
                bot_app_id,
            )
            return JsonResponse({"ok": True})

        # 3. Parse JSON body
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            logger.warning("[TelegramWebhookView] Invalid JSON body for bot_app_id=%s", bot_app_id)
            return JsonResponse({"ok": True})

        # 4. Extract update_id
        update_id = payload.get("update_id")
        if update_id is None:
            logger.warning("[TelegramWebhookView] No update_id in payload for bot_app_id=%s", bot_app_id)
            return JsonResponse({"ok": True})

        # 5. Idempotency check — skip if already seen
        if TelegramWebhookEvent.objects.filter(bot_app=bot_app, update_id=update_id).exists():
            logger.info(
                "[TelegramWebhookView] Duplicate update_id=%s for bot_app_id=%s, skipping",
                update_id,
                bot_app_id,
            )
            return JsonResponse({"ok": True})

        # 6. Classify event type
        event_type = "UNKNOWN"
        for key, label in UPDATE_TYPE_MAP.items():
            if key in payload:
                event_type = label
                break

        # 7. Persist event (post_save signal will queue Celery task)
        TelegramWebhookEvent.objects.create(
            tenant=bot_app.tenant,
            bot_app=bot_app,
            update_id=update_id,
            event_type=event_type,
            payload=payload,
        )

        # 8. Return 200 fast
        return JsonResponse({"ok": True})
