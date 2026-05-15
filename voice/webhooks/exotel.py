"""Exotel webhook handlers (#167).

Two handlers per the plan:

  * ``ExotelStatusHandler``    — status callbacks (queued / in-progress
                                  / completed / busy / no-answer / failed)
  * ``ExotelPassthruHandler``  — in-call decision routing (used when
                                  a flow needs to branch mid-call)

Exotel doesn't sign webhooks, so verification is a path-token
(``inbound_webhook_token`` on the provider config). See the adapter
``verify_webhook`` for details.
"""

from __future__ import annotations

import logging
from uuid import UUID

from django.http import Http404, HttpResponse

from abstract.webhooks import BaseWebhookHandler
from voice.adapters.http_voice.exotel import ExotelVoiceAdapter
from voice.constants import VoiceProvider
from voice.ivr.dialects import exotel_xml

logger = logging.getLogger(__name__)


class _ExotelHandlerBase(BaseWebhookHandler):
    redis_key_prefix = "webhook:idempotency:voice:exotel"

    def _get_adapter(self, request, config_uuid: str) -> ExotelVoiceAdapter:
        cached = getattr(request, "_voice_adapter", None)
        if cached is not None:
            return cached

        from voice.models import VoiceProviderConfig

        try:
            cfg = VoiceProviderConfig.objects.get(pk=UUID(config_uuid), provider=VoiceProvider.EXOTEL)
        except (VoiceProviderConfig.DoesNotExist, ValueError) as e:
            raise Http404("Unknown voice provider config") from e

        adapter = ExotelVoiceAdapter(cfg)
        request._voice_adapter = adapter
        return adapter

    def verify_signature(self, request) -> bool:
        config_uuid = self.kwargs.get("config_uuid") or ""
        try:
            adapter = self._get_adapter(request, config_uuid)
        except Http404:
            return False
        return adapter.verify_webhook(request)


class ExotelStatusHandler(_ExotelHandlerBase):
    """Status callback — Exotel POSTs lifecycle events here."""

    def get_idempotency_key(self, request):
        call_sid = request.POST.get("CallSid") or request.GET.get("CallSid", "")
        status = (
            request.POST.get("Status")
            or request.POST.get("CallStatus")
            or request.GET.get("Status")
            or request.GET.get("CallStatus")
            or ""
        )
        if not call_sid or not status:
            return None
        return f"status:{call_sid}:{status}"

    def handle_verified(self, request):
        adapter: ExotelVoiceAdapter = request._voice_adapter
        event = adapter.parse_webhook(request)

        params = {**request.GET.dict(), **request.POST.dict()}
        exotel_status = params.get("Status") or params.get("CallStatus")

        from voice.tasks import process_call_status

        process_call_status.delay(
            {
                "provider_call_id": event.provider_call_id,
                "status": adapter._normalize_status(exotel_status),
                "event_type": event.event_type,
                "hangup_cause": event.hangup_cause,
                "raw": event.payload,
            }
        )

        # If the status callback carries a RecordingUrl (Exotel attaches
        # it on completed calls), queue download.
        recording_url = params.get("RecordingUrl")
        if recording_url:
            self._queue_recording_download(event.provider_call_id, recording_url)

        return HttpResponse(status=200)

    @staticmethod
    def _queue_recording_download(call_sid: str, recording_url: str) -> None:
        from voice.models import VoiceCall

        call = VoiceCall.objects.filter(provider_call_id=call_sid).first()
        if call is None:
            logger.warning("[ExotelStatusHandler] no VoiceCall for CallSid=%s", call_sid)
            return

        from voice.recordings.tasks import download_recording

        # We pass the recording URL as the provider_recording_id since
        # Exotel surfaces the audio at a CDN URL rather than a SID.
        download_recording.delay(str(call.id), recording_url)


class ExotelPassthruHandler(_ExotelHandlerBase):
    """Passthru applet — Exotel POSTs here mid-call for routing decisions.

    The handler should return ExoML telling Exotel what to do next. For
    #167 we return a simple hangup; full IVR flow rendering lands with
    #168.
    """

    def get_idempotency_key(self, request):
        return None  # passthru is per-decision, not idempotent

    def handle_verified(self, request):
        return HttpResponse(
            exotel_xml.assemble([exotel_xml.hangup({}, context={})]),
            content_type="application/xml",
        )
