"""Telnyx Call Control webhook handler (#166).

Telnyx delivers every event type through a single webhook URL — call
lifecycle, recording-saved, transcription-finished, etc. The handler
dispatches by ``event_type`` from the JSON envelope.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from django.http import Http404, HttpResponse

from abstract.webhooks import BaseWebhookHandler
from voice.adapters.http_voice.telnyx import TelnyxVoiceAdapter
from voice.constants import VoiceProvider

logger = logging.getLogger(__name__)


def _parse_envelope(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


class TelnyxEventHandler(BaseWebhookHandler):
    """Single webhook for every Telnyx call-control event."""

    redis_key_prefix = "webhook:idempotency:voice:telnyx"

    def _get_adapter(self, request, config_uuid: str) -> TelnyxVoiceAdapter:
        cached = getattr(request, "_voice_adapter", None)
        if cached is not None:
            return cached

        from voice.models import VoiceProviderConfig

        try:
            cfg = VoiceProviderConfig.objects.get(pk=UUID(config_uuid), provider=VoiceProvider.TELNYX)
        except (VoiceProviderConfig.DoesNotExist, ValueError) as e:
            raise Http404("Unknown voice provider config") from e

        adapter = TelnyxVoiceAdapter(cfg)
        request._voice_adapter = adapter
        return adapter

    def verify_signature(self, request) -> bool:
        config_uuid = self.kwargs.get("config_uuid") or ""
        try:
            adapter = self._get_adapter(request, config_uuid)
        except Http404:
            return False
        return adapter.verify_webhook(request)

    def get_idempotency_key(self, request):
        envelope = _parse_envelope(request)
        data = envelope.get("data") or {}
        # Telnyx sends an ``id`` on the data envelope that's unique
        # per event — perfect idempotency key.
        event_id = data.get("id")
        if event_id:
            return f"event:{event_id}"
        # Fallback: call_control_id + event_type.
        payload = data.get("payload") or {}
        cc_id = payload.get("call_control_id")
        et = data.get("event_type")
        if cc_id and et:
            return f"event:{cc_id}:{et}"
        return None

    def handle_verified(self, request):
        adapter: TelnyxVoiceAdapter = request._voice_adapter
        event = adapter.parse_webhook(request)

        envelope = _parse_envelope(request)
        event_type = (envelope.get("data") or {}).get("event_type", "")
        payload = (envelope.get("data") or {}).get("payload") or {}

        if event_type == "call.recording.saved":
            self._handle_recording_saved(payload)
            return HttpResponse(status=200)

        from voice.tasks import process_call_status

        process_call_status.delay(
            {
                "provider_call_id": event.provider_call_id,
                "status": adapter._normalize_status(event_type),
                "event_type": event.event_type,
                "hangup_cause": event.hangup_cause,
                "raw": envelope,
            }
        )
        return HttpResponse(status=200)

    @staticmethod
    def _handle_recording_saved(payload: dict) -> None:
        """Queue ``download_recording`` for a freshly-saved recording."""
        from voice.models import VoiceCall

        call_control_id = payload.get("call_control_id") or ""
        recording_id = payload.get("recording_id") or ""
        if not (call_control_id and recording_id):
            return

        call = VoiceCall.objects.filter(provider_call_id=call_control_id).first()
        if call is None:
            logger.warning(
                "[TelnyxEventHandler] no VoiceCall for call_control_id=%s",
                call_control_id,
            )
            return

        from voice.recordings.tasks import download_recording

        download_recording.delay(str(call.id), recording_id)
