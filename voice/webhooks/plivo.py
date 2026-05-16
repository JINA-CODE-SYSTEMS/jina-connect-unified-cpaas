"""Plivo webhook handlers (#164).

Three handlers, all subclasses of ``BaseWebhookHandler``:

  * ``PlivoCallStatusHandler``   — call lifecycle (queued → completed)
  * ``PlivoAnswerHandler``       — returns Plivo XML on answer
  * ``PlivoRecordingHandler``    — recording-status callback, queues
                                    ``download_recording`` on completion

Each handler instantiates a ``PlivoVoiceAdapter`` from the
``VoiceProviderConfig`` referenced in the URL path. Signature
verification + idempotency are inherited from the base.
"""

from __future__ import annotations

import logging
from uuid import UUID

from django.http import Http404, HttpResponse

from abstract.webhooks import BaseWebhookHandler
from voice.adapters.http_voice.plivo import PlivoVoiceAdapter
from voice.constants import VoiceProvider
from voice.ivr.dialects import plivo_xml

logger = logging.getLogger(__name__)


class _PlivoHandlerBase(BaseWebhookHandler):
    """Shared scaffolding: resolve ``config_uuid`` → adapter, then verify."""

    redis_key_prefix = "webhook:idempotency:voice:plivo"

    def _get_adapter(self, request, config_uuid: str) -> PlivoVoiceAdapter:
        cached = getattr(request, "_voice_adapter", None)
        if cached is not None:
            return cached

        from voice.models import VoiceProviderConfig

        try:
            cfg = VoiceProviderConfig.objects.get(pk=UUID(config_uuid), provider=VoiceProvider.PLIVO)
        except (VoiceProviderConfig.DoesNotExist, ValueError) as e:
            raise Http404("Unknown voice provider config") from e

        adapter = PlivoVoiceAdapter(cfg)
        request._voice_adapter = adapter
        return adapter

    def verify_signature(self, request) -> bool:
        config_uuid = self.kwargs.get("config_uuid") or ""
        try:
            adapter = self._get_adapter(request, config_uuid)
        except Http404:
            return False
        return adapter.verify_webhook(request)


class PlivoCallStatusHandler(_PlivoHandlerBase):
    """Plivo lifecycle status callbacks."""

    def get_idempotency_key(self, request):
        call_uuid = request.POST.get("CallUUID", "")
        status = request.POST.get("CallStatus", "")
        if not call_uuid or not status:
            return None
        return f"call-status:{call_uuid}:{status}"

    def handle_verified(self, request):
        adapter: PlivoVoiceAdapter = request._voice_adapter
        event = adapter.parse_webhook(request)

        from voice.tasks import process_call_status

        process_call_status.delay(
            {
                "provider_call_id": event.provider_call_id,
                "status": adapter._normalize_status(request.POST.get("CallStatus")),
                "event_type": event.event_type,
                "hangup_cause": event.hangup_cause,
                "raw": event.payload,
            }
        )
        # Plivo expects an empty XML body on status callbacks.
        return HttpResponse("<Response></Response>", content_type="application/xml")


class PlivoAnswerHandler(_PlivoHandlerBase):
    """Plivo "answer" webhook: returns Plivo XML that controls the call."""

    def get_idempotency_key(self, request):
        # Same reasoning as Twilio's answer handler — one delivery,
        # re-running is safe (XML output is deterministic).
        return None

    def handle_verified(self, request):
        call = self._find_call(request)
        chunks: list[str] = []
        if call is not None and (call.metadata or {}).get("static_play"):
            static = call.metadata["static_play"]
            chunks.append(plivo_xml.play(static, context={}))
        chunks.append(plivo_xml.hangup({}, context={}))
        return HttpResponse(plivo_xml.assemble(chunks), content_type="application/xml")

    @staticmethod
    def _find_call(request):
        from voice.models import VoiceCall

        call_uuid = request.POST.get("CallUUID", "")
        if not call_uuid:
            return None
        return VoiceCall.objects.filter(provider_call_id=call_uuid).first()


class PlivoRecordingHandler(_PlivoHandlerBase):
    """Notification that a recording finished. Queues
    ``download_recording`` on completion."""

    def get_idempotency_key(self, request):
        rec_id = request.POST.get("RecordingID", "")
        status = request.POST.get("RecordingStatus", "completed")
        if not rec_id:
            return None
        return f"recording:{rec_id}:{status}"

    def handle_verified(self, request):
        rec_id = request.POST.get("RecordingID", "")
        call_uuid = request.POST.get("CallUUID", "")
        # Plivo sends a "completed" status when the file is ready; some
        # accounts skip the field entirely. Default to treating any
        # delivery as completion.
        status = request.POST.get("RecordingStatus", "completed")

        if status != "completed":
            logger.info("[PlivoRecordingHandler] %s status=%s (ack)", rec_id, status)
            return HttpResponse(status=200)

        from voice.models import VoiceCall

        call = VoiceCall.objects.filter(provider_call_id=call_uuid).first()
        if call is None:
            logger.warning("[PlivoRecordingHandler] no VoiceCall for CallUUID=%s", call_uuid)
            return HttpResponse(status=200)

        from voice.recordings.tasks import download_recording

        download_recording.delay(str(call.id), rec_id)
        return HttpResponse(status=200)
