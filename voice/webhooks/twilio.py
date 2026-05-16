"""Twilio webhook handlers (#160).

Four handlers, all subclasses of ``BaseWebhookHandler`` (from #156):

  * ``TwilioCallStatusHandler``   — call lifecycle (initiated → completed)
  * ``TwilioAnswerHandler``       — returns TwiML when Twilio asks
                                    "what should I do now that the call
                                    is answered?"
  * ``TwilioGatherHandler``       — receives DTMF / speech input
                                    (full handling lands with #168)
  * ``TwilioRecordingStatusHandler`` — recording finished; queues
                                    download (lands with #161)

Each handler instantiates a ``TwilioVoiceAdapter`` from the
``VoiceProviderConfig`` referenced in the URL path. Signature
verification + idempotency are inherited from the base.
"""

from __future__ import annotations

import logging
from uuid import UUID

from django.http import Http404, HttpResponse

from abstract.webhooks import BaseWebhookHandler
from voice.adapters.http_voice.twilio import TwilioVoiceAdapter
from voice.constants import VoiceProvider
from voice.ivr.dialects import twiml

logger = logging.getLogger(__name__)


class _TwilioHandlerBase(BaseWebhookHandler):
    """Shared scaffolding: resolve ``config_uuid`` → adapter, then verify."""

    redis_key_prefix = "webhook:idempotency:voice:twilio"

    def _get_adapter(self, request, config_uuid: str) -> TwilioVoiceAdapter:
        """Resolve the ``VoiceProviderConfig`` from the URL → adapter.

        Cached on ``request`` so signature verify + handle_verified don't
        hit the DB twice.
        """
        cached = getattr(request, "_voice_adapter", None)
        if cached is not None:
            return cached

        # Lazy import — avoid the model layer at module load time.
        from voice.models import VoiceProviderConfig

        try:
            cfg = VoiceProviderConfig.objects.get(pk=UUID(config_uuid), provider=VoiceProvider.TWILIO)
        except (VoiceProviderConfig.DoesNotExist, ValueError) as e:
            raise Http404("Unknown voice provider config") from e

        adapter = TwilioVoiceAdapter(cfg)
        request._voice_adapter = adapter
        return adapter

    def verify_signature(self, request) -> bool:
        config_uuid = self.kwargs.get("config_uuid") or ""
        try:
            adapter = self._get_adapter(request, config_uuid)
        except Http404:
            return False
        return adapter.verify_webhook(request)


class TwilioCallStatusHandler(_TwilioHandlerBase):
    """Twilio status callbacks: ``initiated``, ``ringing``, ``answered``,
    ``completed``, ``failed``, etc."""

    def get_idempotency_key(self, request):
        sid = request.POST.get("CallSid", "")
        status = request.POST.get("CallStatus", "")
        if not sid or not status:
            return None
        return f"call-status:{sid}:{status}"

    def handle_verified(self, request):
        # Re-use the cached adapter from signature verify.
        adapter: TwilioVoiceAdapter = request._voice_adapter
        event = adapter.parse_webhook(request)

        # Queue the DB write so we hit Twilio's 200-OK budget (<200ms p95).
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
        # Status-only endpoints get an empty TwiML response — Twilio
        # doesn't apply it but expects something.
        return HttpResponse("<Response></Response>", content_type="application/xml")


class TwilioAnswerHandler(_TwilioHandlerBase):
    """Twilio "answer" webhook: returns the TwiML that controls the call."""

    def get_idempotency_key(self, request):
        # Answer webhooks aren't idempotent in the usual sense — Twilio
        # only ever sends one, and re-running handle_verified is safe
        # (it just re-emits TwiML). Skip the SETNX claim.
        return None

    def handle_verified(self, request):
        # For #160 we support static plays only. Full IVR flow
        # rendering lands with #168. (Adapter is already attached to
        # ``request._voice_adapter`` from verify_signature — kept implicit
        # here since the static-play path doesn't need it.)
        call = self._find_call(request)

        chunks: list[str] = []
        if call is not None and call.metadata.get("static_play"):
            static = call.metadata["static_play"]
            chunks.append(twiml.play(static, context={}))
        chunks.append(twiml.hangup({}, context={}))

        body = twiml.assemble(chunks)
        return HttpResponse(body, content_type="application/xml")

    @staticmethod
    def _find_call(request):
        from voice.models import VoiceCall

        sid = request.POST.get("CallSid", "")
        if not sid:
            return None
        return VoiceCall.objects.filter(provider_call_id=sid).first()


class TwilioGatherHandler(_TwilioHandlerBase):
    """Receives the result of a ``<Gather>`` — DTMF digits or speech.

    Full session-state and next-step lookup lands with #168 (IVR
    compiler + Redis session). For #160 we ack and hang up.
    """

    def get_idempotency_key(self, request):
        sid = request.POST.get("CallSid", "")
        digits = request.POST.get("Digits", "")
        if not sid:
            return None
        return f"gather:{sid}:{digits}"

    def handle_verified(self, request):
        return HttpResponse(
            twiml.assemble([twiml.hangup({}, context={})]),
            content_type="application/xml",
        )


class TwilioRecordingStatusHandler(_TwilioHandlerBase):
    """Notification that a recording has finished and is available.

    Queues a download task (lands with #161). For #160 we just ack so
    Twilio stops retrying.
    """

    def get_idempotency_key(self, request):
        recording_sid = request.POST.get("RecordingSid", "")
        status = request.POST.get("RecordingStatus", "")
        if not recording_sid:
            return None
        return f"recording:{recording_sid}:{status}"

    def handle_verified(self, request):
        recording_sid = request.POST.get("RecordingSid", "")
        status = request.POST.get("RecordingStatus", "")
        call_sid = request.POST.get("CallSid", "")

        if status != "completed":
            logger.info(
                "[TwilioRecordingStatusHandler] %s status=%s (ack)",
                recording_sid,
                status,
            )
            return HttpResponse(status=200)

        # Resolve the VoiceCall row from the Twilio CallSid so the task
        # has both ids it needs.
        from voice.models import VoiceCall

        call = VoiceCall.objects.filter(provider_call_id=call_sid).first()
        if call is None:
            logger.warning(
                "[TwilioRecordingStatusHandler] no VoiceCall for CallSid=%s",
                call_sid,
            )
            return HttpResponse(status=200)

        from voice.recordings.tasks import download_recording

        download_recording.delay(str(call.id), recording_sid)
        return HttpResponse(status=200)
