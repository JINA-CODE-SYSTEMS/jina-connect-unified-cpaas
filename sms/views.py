"""SMS webhook receivers."""

from __future__ import annotations

import json
import logging
from hashlib import sha256

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from sms.models import SMSApp, SMSWebhookEvent
from sms.providers import get_sms_provider

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class SMSInboundWebhookView(View):
    def get(self, request, sms_app_id):
        return JsonResponse({"ok": True})

    def post(self, request, sms_app_id):
        app = SMSApp.objects.filter(pk=sms_app_id, is_active=True).select_related("tenant").first()
        if not app:
            return JsonResponse({"ok": True})

        provider = get_sms_provider(app)
        if not provider.validate_webhook_signature(request):
            logger.warning("SMS inbound webhook signature failed for app %s", sms_app_id)
            return JsonResponse({"ok": True})

        payload = _read_payload(request)
        normalized = provider.parse_inbound_webhook(payload)
        provider_message_id = normalized.provider_message_id or _fallback_message_id(payload, event_type="INBOUND")

        SMSWebhookEvent.objects.get_or_create(
            sms_app=app,
            provider_message_id=provider_message_id,
            event_type="INBOUND",
            defaults={
                "tenant": app.tenant,
                "provider": app.provider,
                "payload": payload,
                "from_number": normalized.from_number,
                "to_number": normalized.to_number,
            },
        )
        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class SMSDLRWebhookView(View):
    def get(self, request, sms_app_id):
        return JsonResponse({"ok": True})

    def post(self, request, sms_app_id):
        app = SMSApp.objects.filter(pk=sms_app_id, is_active=True).select_related("tenant").first()
        if not app:
            return JsonResponse({"ok": True})

        provider = get_sms_provider(app)
        if not provider.validate_webhook_signature(request):
            logger.warning("SMS DLR webhook signature failed for app %s", sms_app_id)
            return JsonResponse({"ok": True})

        payload = _read_payload(request)
        normalized = provider.parse_dlr_webhook(payload)
        provider_message_id = normalized.provider_message_id or _fallback_message_id(payload, event_type="DLR")

        SMSWebhookEvent.objects.get_or_create(
            sms_app=app,
            provider_message_id=provider_message_id,
            event_type="DLR",
            defaults={
                "tenant": app.tenant,
                "provider": app.provider,
                "payload": payload,
                "from_number": payload.get("From", payload.get("sender", "")),
                "to_number": payload.get("To", payload.get("to", "")),
            },
        )
        return JsonResponse({"ok": True})


def _read_payload(request):
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return {}
    if request.POST:
        return request.POST.dict()
    return {}


def _fallback_message_id(payload: dict, event_type: str) -> str:
    """Build deterministic fallback IDs when providers omit message IDs."""
    digest = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    return f"{event_type.lower()}-{digest}"
