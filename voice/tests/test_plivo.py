"""Plivo adapter + dialect + webhook tests (#164)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, TestCase

from tenants.models import Tenant
from voice.adapters.http_voice.plivo import PlivoVoiceAdapter
from voice.constants import (
    CallEventType,
    CallStatus,
    HangupCause,
    VoiceProvider,
)
from voice.ivr.dialects import plivo_xml
from voice.models import VoiceProviderConfig
from voice.webhooks.plivo import (
    PlivoAnswerHandler,
    PlivoCallStatusHandler,
    PlivoRecordingHandler,
)


def _plivo_v3_signature(auth_token: str, nonce: str, url: str, post: dict) -> str:
    """Compute Plivo's V3 signature the same way the adapter does."""
    data = nonce + url
    for k, v in sorted(post.items()):
        data += k + v
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha256).digest()).decode()


# ─────────────────────────────────────────────────────────────────────────────
# Plivo XML dialect
# ─────────────────────────────────────────────────────────────────────────────


class PlivoXmlDialectTests(SimpleTestCase):
    def test_play_tts(self):
        out = plivo_xml.play({"tts_text": "Hello"}, {})
        self.assertIn("<Speak>Hello</Speak>", out)

    def test_play_tts_with_voice_and_language(self):
        out = plivo_xml.play({"tts_text": "Hi", "tts_voice": "WOMAN", "tts_language": "en-IN"}, {})
        self.assertIn('voice="WOMAN"', out)
        self.assertIn('language="en-IN"', out)

    def test_play_audio_url(self):
        out = plivo_xml.play({"audio_url": "https://example/a.mp3"}, {})
        self.assertEqual(out, "<Play>https://example/a.mp3</Play>")

    def test_play_tts_escapes_xml(self):
        out = plivo_xml.play({"tts_text": "<script>"}, {})
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_play_neither_emits_wait(self):
        self.assertEqual(plivo_xml.play({}, {}), '<Wait length="1"/>')

    def test_gather_dtmf(self):
        out = plivo_xml.gather_dtmf({"max_digits": 4, "timeout_seconds": 5}, {})
        self.assertIn("<GetDigits", out)
        self.assertIn('numDigits="4"', out)
        self.assertIn('timeout="5"', out)

    def test_gather_dtmf_with_prompt(self):
        out = plivo_xml.gather_dtmf({"max_digits": 1, "timeout_seconds": 3, "prompt_tts": "Choose"}, {})
        self.assertIn("<Speak>Choose</Speak>", out)

    def test_gather_speech(self):
        out = plivo_xml.gather_speech({"language": "en-US", "timeout_seconds": 5}, {})
        self.assertIn('inputType="speech"', out)
        self.assertIn('language="en-US"', out)

    def test_record(self):
        out = plivo_xml.record({"max_duration_seconds": 60}, {})
        self.assertIn('maxLength="60"', out)
        self.assertIn('playBeep="true"', out)

    def test_transfer_wraps_in_dial_number(self):
        out = plivo_xml.transfer({"to_uri": "+14155550100"}, {})
        self.assertEqual(out, "<Dial><Number>+14155550100</Number></Dial>")

    def test_hangup(self):
        self.assertEqual(plivo_xml.hangup({}, {}), "<Hangup/>")

    def test_assemble_wraps_in_response(self):
        out = plivo_xml.assemble(["<Speak>hi</Speak>", "<Hangup/>"])
        self.assertTrue(out.startswith('<?xml version="1.0"'))
        self.assertIn("<Response>", out)
        self.assertIn("<Speak>hi</Speak><Hangup/></Response>", out)

    def test_handler_lookup_known_types(self):
        for type_id in (
            "voice.play",
            "voice.gather_dtmf",
            "voice.gather_speech",
            "voice.record",
            "voice.transfer",
            "voice.hangup",
        ):
            self.assertIsNotNone(plivo_xml.get_handler(type_id))

    def test_handler_unknown(self):
        self.assertIsNone(plivo_xml.get_handler("voice.does_not_exist"))


# ─────────────────────────────────────────────────────────────────────────────
# Plivo adapter
# ─────────────────────────────────────────────────────────────────────────────


def _make_plivo_adapter(tenant, auth_id="MA123", auth_token="plivo_token"):
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Test Plivo",
        provider=VoiceProvider.PLIVO,
        credentials=json.dumps({"auth_id": auth_id, "auth_token": auth_token}),
    )
    return PlivoVoiceAdapter(cfg)


class PlivoSignatureVerifyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Plivo Sig Tenant")

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = _make_plivo_adapter(self.tenant, auth_token="plivo_secret")

    def _request(self, post, *, signature=None, nonce="nonce-123"):
        request = self.factory.post("/voice/v1/webhooks/plivo/abc/call-status/", data=post)
        if signature is not None:
            request.META["HTTP_X_PLIVO_SIGNATURE_V3"] = signature
        if nonce is not None:
            request.META["HTTP_X_PLIVO_SIGNATURE_V3_NONCE"] = nonce
        return request

    def test_valid_signature_passes(self):
        post = {"CallUUID": "PV1", "CallStatus": "in-progress"}
        nonce = "nonce-good"
        url = self.factory.post("/voice/v1/webhooks/plivo/abc/call-status/", data=post).build_absolute_uri()
        sig = _plivo_v3_signature("plivo_secret", nonce, url, post)
        req = self._request(post, signature=sig, nonce=nonce)
        self.assertTrue(self.adapter.verify_webhook(req))

    def test_missing_signature_header_fails(self):
        req = self._request({"CallUUID": "x"}, signature=None)
        self.assertFalse(self.adapter.verify_webhook(req))

    def test_missing_nonce_header_fails(self):
        req = self._request({"CallUUID": "x"}, signature="something", nonce=None)
        self.assertFalse(self.adapter.verify_webhook(req))

    def test_wrong_signature_fails(self):
        req = self._request({"CallUUID": "x"}, signature="wrong")
        self.assertFalse(self.adapter.verify_webhook(req))

    def test_empty_auth_token_fails(self):
        adapter = _make_plivo_adapter(self.tenant, auth_token="")
        req = self._request({"CallUUID": "x"}, signature="anything")
        self.assertFalse(adapter.verify_webhook(req))


class PlivoParseWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Plivo Parse Tenant")

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = _make_plivo_adapter(self.tenant)

    def _parse(self, post):
        return self.adapter.parse_webhook(self.factory.post("/wh/", data=post))

    def test_in_progress_maps_to_answered(self):
        ev = self._parse({"CallUUID": "PV1", "CallStatus": "in-progress"})
        self.assertEqual(ev.event_type, CallEventType.ANSWERED)
        self.assertIsNone(ev.hangup_cause)

    def test_completed_maps_with_normal_clearing(self):
        ev = self._parse({"CallUUID": "PV2", "CallStatus": "completed"})
        self.assertEqual(ev.event_type, CallEventType.COMPLETED)
        self.assertEqual(ev.hangup_cause, HangupCause.NORMAL_CLEARING)

    def test_busy_maps_with_user_busy(self):
        ev = self._parse({"CallUUID": "PV3", "CallStatus": "busy"})
        self.assertEqual(ev.event_type, CallEventType.FAILED)
        self.assertEqual(ev.hangup_cause, HangupCause.USER_BUSY)

    def test_rejected_maps_with_call_rejected(self):
        ev = self._parse({"CallUUID": "PV4", "CallStatus": "rejected"})
        self.assertEqual(ev.event_type, CallEventType.FAILED)
        self.assertEqual(ev.hangup_cause, HangupCause.CALL_REJECTED)


class PlivoStatusNormalisationTests(SimpleTestCase):
    def test_known_statuses(self):
        a = PlivoVoiceAdapter.__new__(PlivoVoiceAdapter)
        self.assertEqual(a._normalize_status("in-progress"), CallStatus.IN_PROGRESS)
        self.assertEqual(a._normalize_status("completed"), CallStatus.COMPLETED)
        self.assertEqual(a._normalize_status("ringing"), CallStatus.RINGING)

    def test_unknown_returns_none(self):
        a = PlivoVoiceAdapter.__new__(PlivoVoiceAdapter)
        self.assertIsNone(a._normalize_status("made-up"))


class PlivoInitiateCallTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Plivo Init Tenant")

    def setUp(self):
        self.adapter = _make_plivo_adapter(self.tenant)

    @patch.object(PlivoVoiceAdapter, "_request")
    def test_initiate_call_posts_to_call_endpoint(self, mock_request):
        resp = MagicMock(status_code=201)
        resp.json.return_value = {"request_uuid": "PV-init"}
        resp.raise_for_status.return_value = None
        mock_request.return_value = resp

        from voice.adapters.base import CallInstructions

        handle = self.adapter.initiate_call(
            from_number="+14155550100",
            to_number="+14155550200",
            callback_url="https://app.example.com/voice/answer",
            instructions=CallInstructions(),
        )
        self.assertEqual(handle.provider_call_id, "PV-init")
        method, url = mock_request.call_args[0]
        self.assertEqual(method, "POST")
        self.assertIn("/Call/", url)
        body = mock_request.call_args[1]["json"]
        self.assertEqual(body["from"], "+14155550100")
        self.assertEqual(body["to"], "+14155550200")
        self.assertEqual(body["answer_url"], "https://app.example.com/voice/answer")


# ─────────────────────────────────────────────────────────────────────────────
# Plivo webhooks
# ─────────────────────────────────────────────────────────────────────────────


class PlivoWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Plivo WH Tenant")
        cls.auth_token = "plivo_wh_token"
        cls.config = VoiceProviderConfig.objects.create(
            tenant=cls.tenant,
            name="Plivo WH",
            provider=VoiceProvider.PLIVO,
            credentials=json.dumps({"auth_id": "MA1", "auth_token": cls.auth_token}),
        )

    def _signed(self, path, post):
        url = f"http://testserver{path}"
        nonce = "nonce-test"
        sig = _plivo_v3_signature(self.auth_token, nonce, url, post)
        factory = RequestFactory()
        request = factory.post(path, data=post)
        request.META["HTTP_X_PLIVO_SIGNATURE_V3"] = sig
        request.META["HTTP_X_PLIVO_SIGNATURE_V3_NONCE"] = nonce
        return request

    @patch("voice.tasks.process_call_status.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_call_status_valid_signature_queues_task(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True
        path = f"/voice/v1/webhooks/plivo/{self.config.id}/call-status/"
        post = {"CallUUID": "PV-A", "CallStatus": "completed"}
        request = self._signed(path, post)
        resp = PlivoCallStatusHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once()
        payload = mock_delay.call_args[0][0]
        self.assertEqual(payload["provider_call_id"], "PV-A")

    @patch("voice.tasks.process_call_status.delay")
    def test_call_status_bad_signature_returns_403(self, mock_delay):
        factory = RequestFactory()
        path = f"/voice/v1/webhooks/plivo/{self.config.id}/call-status/"
        request = factory.post(path, data={"CallUUID": "x", "CallStatus": "completed"})
        request.META["HTTP_X_PLIVO_SIGNATURE_V3"] = "wrong"
        request.META["HTTP_X_PLIVO_SIGNATURE_V3_NONCE"] = "n"
        resp = PlivoCallStatusHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 403)
        mock_delay.assert_not_called()

    def test_answer_returns_hangup_xml(self):
        path = f"/voice/v1/webhooks/plivo/{self.config.id}/answer/"
        post = {"CallUUID": "PV-ans"}
        request = self._signed(path, post)
        resp = PlivoAnswerHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/xml")
        body = resp.content.decode()
        self.assertIn("<Hangup/>", body)

    @patch("voice.recordings.tasks.download_recording.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_recording_completed_queues_download(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True

        # Pre-create the VoiceCall so the handler can resolve it.
        from voice.constants import CallDirection, CallStatus
        from voice.models import VoiceCall

        call = VoiceCall.objects.create(
            tenant=self.tenant,
            name="plivo-rec-test",
            provider_config=self.config,
            provider_call_id="PV-REC",
            direction=CallDirection.OUTBOUND,
            from_number="+1",
            to_number="+2",
            status=CallStatus.COMPLETED,
        )

        path = f"/voice/v1/webhooks/plivo/{self.config.id}/recording/"
        post = {
            "RecordingID": "REC-1",
            "RecordingStatus": "completed",
            "CallUUID": "PV-REC",
        }
        request = self._signed(path, post)
        resp = PlivoRecordingHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once_with(str(call.id), "REC-1")

    @patch("voice.recordings.tasks.download_recording.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_recording_non_completed_does_not_queue(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True
        path = f"/voice/v1/webhooks/plivo/{self.config.id}/recording/"
        post = {"RecordingID": "REC-2", "RecordingStatus": "in-progress", "CallUUID": "X"}
        request = self._signed(path, post)
        resp = PlivoRecordingHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_not_called()
