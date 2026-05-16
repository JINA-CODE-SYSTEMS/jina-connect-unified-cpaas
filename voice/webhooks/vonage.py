"""Vonage webhook handlers (#165).

Two endpoints per the plan:

  * ``VonageEventHandler``   — call lifecycle events
                                (started / ringing / answered / completed / etc.)
  * ``VonageAnswerHandler``  — returns the NCCO JSON Vonage runs on answer

Both inherit ``BaseWebhookHandler``. JSON-bodied (unlike Twilio /
Plivo which post form data).
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from django.http import Http404, HttpResponse, JsonResponse

from abstract.webhooks import BaseWebhookHandler
from voice.adapters.http_voice.vonage import VonageVoiceAdapter
from voice.constants import VoiceProvider
from voice.ivr.dialects import ncco

logger = logging.getLogger(__name__)


class _VonageHandlerBase(BaseWebhookHandler):
    redis_key_prefix = "webhook:idempotency:voice:vonage"

    def _get_adapter(self, request, config_uuid: str) -> VonageVoiceAdapter:
        cached = getattr(request, "_voice_adapter", None)
        if cached is not None:
            return cached

        from voice.models import VoiceProviderConfig

        try:
            cfg = VoiceProviderConfig.objects.get(pk=UUID(config_uuid), provider=VoiceProvider.VONAGE)
        except (VoiceProviderConfig.DoesNotExist, ValueError) as e:
            raise Http404("Unknown voice provider config") from e

        adapter = VonageVoiceAdapter(cfg)
        request._voice_adapter = adapter
        return adapter

    def verify_signature(self, request) -> bool:
        config_uuid = self.kwargs.get("config_uuid") or ""
        try:
            adapter = self._get_adapter(request, config_uuid)
        except Http404:
            return False
        return adapter.verify_webhook(request)


def _parse_json_body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


class VonageEventHandler(_VonageHandlerBase):
    """Receives every lifecycle event for a call."""

    def get_idempotency_key(self, request):
        body = _parse_json_body(request)
        call_uuid = body.get("uuid") or body.get("conversation_uuid") or ""
        status = body.get("status") or body.get("type") or ""
        if not call_uuid or not status:
            return None
        return f"event:{call_uuid}:{status}"

    def handle_verified(self, request):
        adapter: VonageVoiceAdapter = request._voice_adapter
        event = adapter.parse_webhook(request)

        from voice.tasks import process_call_status

        process_call_status.delay(
            {
                "provider_call_id": event.provider_call_id,
                "status": adapter._normalize_status(_parse_json_body(request).get("status")),
                "event_type": event.event_type,
                "hangup_cause": event.hangup_cause,
                "raw": event.payload,
            }
        )
        return HttpResponse(status=200)


class VonageAnswerHandler(_VonageHandlerBase):
    """Returns the NCCO JSON that Vonage runs once the call answers."""

    def get_idempotency_key(self, request):
        return None  # answer is delivered once; idempotency unhelpful

    def handle_verified(self, request):
        # For #165 we honour ``static_play`` stamped on the VoiceCall;
        # full IVR rendering arrives with #168.
        call = self._find_call(request)
        actions = []
        if call is not None and (call.metadata or {}).get("static_play"):
            actions.append(ncco.play(call.metadata["static_play"], context={}))
        if not actions:
            # Empty NCCO is rejected by Vonage — return a silent talk
            # action so the call ends cleanly.
            actions = [{"action": "talk", "text": ""}]
        return JsonResponse(actions, safe=False)

    @staticmethod
    def _find_call(request):
        from voice.models import VoiceCall

        body = _parse_json_body(request)
        # Vonage answer webhooks carry the call uuid as ``uuid``.
        call_uuid = body.get("uuid") or ""
        if not call_uuid:
            return None
        return VoiceCall.objects.filter(provider_call_id=call_uuid).first()
