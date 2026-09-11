"""Provider read receipts for inbound WhatsApp messages (#274).

Marking a conversation read in the inbox only ever moved our own rows —
``is_read``/``read_at`` plus a WebSocket broadcast so the other agents stop
seeing the unread badge. Nothing told WhatsApp, so the customer never got
blue ticks however promptly an agent read their message.

Only META Direct can deliver one: it is a single ``POST`` to the same
``/{phone_number_id}/messages`` endpoint that sends messages. Gupshup's
partner API exposes no read-receipt endpoint at all, so a Gupshup app logs
the skip — the gap is the provider's, and a silent no-op would hide that
from whoever next wonders why blue ticks only appear for some tenants.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def send_read_receipt(messages) -> dict:
    """Acknowledge the newest inbound WhatsApp message in *messages*.

    META marks every earlier message of the conversation read along with
    the one it is given, so a batch of fifty needs exactly one call.

    Never raises: a missing blue tick must not fail the mark-as-read that
    triggered it.

    Args:
        messages: iterable of ``team_inbox.Messages`` just marked as read.

    Returns:
        dict with ``sent`` (bool) and ``reason`` (str) — the reason is for
        logs and tests, no caller branches on it.
    """
    result = {"sent": False, "reason": ""}

    try:
        inbound = [m for m in messages if m.platform == "WHATSAPP" and m.direction == "INCOMING"]
        if not inbound:
            result["reason"] = "no_whatsapp_inbound"
            return result

        newest = max(inbound, key=lambda m: m.timestamp)
        wa_message_id = ((newest.content or {}).get("_meta") or {}).get("wa_message_id")
        if not wa_message_id:
            # Anything that reached the inbox by a path which does not
            # stamp ``_meta`` — there is no provider ID to acknowledge.
            result["reason"] = "no_provider_message_id"
            return result

        wa_app = _resolve_wa_app(newest)
        if not wa_app:
            result["reason"] = "no_wa_app"
            return result

        from tenants.models import BSPChoices

        if wa_app.bsp != BSPChoices.META:
            logger.info(
                "[read_receipts] %s has no read-receipt API — message %s acknowledged locally only",
                wa_app.bsp or "unknown BSP",
                newest.pk,
            )
            result["reason"] = f"provider_unsupported:{wa_app.bsp}"
            return result

        from django.conf import settings

        creds = wa_app.bsp_credentials or {}
        token = creds.get("access_token") or getattr(settings, "META_PERM_TOKEN", None)
        if not token or not wa_app.phone_number_id:
            result["reason"] = "no_credentials"
            return result

        from wa.utility.apis.meta.session_message_api import SessionMessageAPI

        api = SessionMessageAPI(token=token, phone_number_id=wa_app.phone_number_id)
        api.mark_read(wa_message_id)

    except Exception as exc:  # noqa: BLE001 — a receipt must never break mark-as-read
        logger.warning("[read_receipts] could not send read receipt: %s", exc)
        result["reason"] = f"error:{exc}"
        return result

    logger.debug("[read_receipts] acknowledged %s", wa_message_id)
    result["sent"] = True
    result["reason"] = "sent"
    return result


def _resolve_wa_app(message):
    """The WAApp that received *message*, or ``None``.

    The inbound builder stamps the webhook event's ID into
    ``content["_meta"]`` (``wa.tasks._build_team_inbox_content``), and the
    event names the exact app — a tenant running two numbers would
    otherwise get whichever came first. Fall back to the tenant's app when
    the event is gone, since most tenants have exactly one.
    """
    from wa.models import WAApp, WAWebhookEvent

    event_id = ((message.content or {}).get("_meta") or {}).get("webhook_event_id")
    if event_id:
        event = WAWebhookEvent.objects.filter(pk=event_id).select_related("wa_app").first()
        if event:
            return event.wa_app

    return WAApp.objects.filter(tenant_id=message.tenant_id, is_active=True).first()
