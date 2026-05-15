"""Exotel adapter + ExoML dialect + webhook tests (#167)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, TestCase

from tenants.models import Tenant
from voice.adapters.http_voice.exotel import ExotelVoiceAdapter
from voice.constants import (
    CallEventType,
    CallStatus,
    HangupCause,
    VoiceProvider,
)
from voice.ivr.dialects import exotel_xml
from voice.models import VoiceProviderConfig
from voice.webhooks.exotel import ExotelPassthruHandler, ExotelStatusHandler


def _make_exotel_adapter(
    tenant,
    *,
    sid="exo-sid",
    api_key="K",
    api_token="T",
    subdomain="api.exotel.com",
    inbound_webhook_token: str | None = None,
):
    creds = {
        "sid": sid,
        "api_key": api_key,
        "api_token": api_token,
        "subdomain": subdomain,
    }
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Test Exotel",
        provider=VoiceProvider.EXOTEL,
        credentials=json.dumps(creds),
        inbound_webhook_token=inbound_webhook_token or "",
    )
    return ExotelVoiceAdapter(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# ExoML dialect
# ─────────────────────────────────────────────────────────────────────────────


class ExotelXmlDialectTests(SimpleTestCase):
    def test_play_tts(self):
        out = exotel_xml.play({"tts_text": "Hello"}, {})
        self.assertEqual(out, "<Say>Hello</Say>")

    def test_play_tts_with_voice(self):
        out = exotel_xml.play({"tts_text": "Hi", "tts_voice": "WOMAN"}, {})
        self.assertIn('voice="WOMAN"', out)

    def test_play_audio_url(self):
        out = exotel_xml.play({"audio_url": "https://example/a.mp3"}, {})
        self.assertEqual(out, "<Play>https://example/a.mp3</Play>")

    def test_play_tts_escapes_xml(self):
        out = exotel_xml.play({"tts_text": "<script>"}, {})
        self.assertIn("&lt;script&gt;", out)

    def test_play_neither_emits_pause(self):
        self.assertEqual(exotel_xml.play({}, {}), "<Pause/>")

    def test_gather_dtmf(self):
        out = exotel_xml.gather_dtmf({"max_digits": 4, "timeout_seconds": 5}, {})
        self.assertIn('numDigits="4"', out)
        self.assertIn('timeout="5"', out)

    def test_gather_dtmf_with_prompt(self):
        out = exotel_xml.gather_dtmf({"max_digits": 1, "timeout_seconds": 3, "prompt_tts": "Choose"}, {})
        self.assertIn("<Say>Choose</Say>", out)

    def test_gather_speech_falls_back_to_record(self):
        """Exotel has no native speech recognition; the dialect falls
        back to a record action."""
        out = exotel_xml.gather_speech({"language": "en-US", "timeout_seconds": 5}, {})
        self.assertIn("<Record", out)
        self.assertIn('maxLength="5"', out)

    def test_record(self):
        out = exotel_xml.record({"max_duration_seconds": 60}, {})
        self.assertIn('maxLength="60"', out)
        self.assertIn('playBeep="true"', out)

    def test_transfer(self):
        self.assertEqual(
            exotel_xml.transfer({"to_uri": "+14155550100"}, {}),
            "<Dial>+14155550100</Dial>",
        )

    def test_hangup(self):
        self.assertEqual(exotel_xml.hangup({}, {}), "<Hangup/>")

    def test_assemble_wraps_in_response(self):
        out = exotel_xml.assemble(["<Say>hi</Say>", "<Hangup/>"])
        self.assertTrue(out.startswith('<?xml version="1.0"'))
        self.assertIn("<Response>", out)
        self.assertIn("<Say>hi</Say><Hangup/></Response>", out)

    def test_handler_lookup(self):
        for type_id in (
            "voice.play",
            "voice.gather_dtmf",
            "voice.gather_speech",
            "voice.record",
            "voice.transfer",
            "voice.hangup",
        ):
            self.assertIsNotNone(exotel_xml.get_handler(type_id))


# ─────────────────────────────────────────────────────────────────────────────
# Adapter
# ─────────────────────────────────────────────────────────────────────────────


class ExotelInitiateCallTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Exotel Init Tenant")

    @patch("voice.adapters.http_voice.exotel.requests.post")
    def test_initiate_call_posts_form_encoded(self, mock_post):
        resp = MagicMock()
        resp.json.return_value = {"Call": {"Sid": "EXO-1"}}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        adapter = _make_exotel_adapter(self.tenant)
        from voice.adapters.base import CallInstructions

        handle = adapter.initiate_call(
            from_number="+14155550100",
            to_number="+14155550200",
            callback_url="https://app.example.com/voice/answer",
            instructions=CallInstructions(),
        )
        self.assertEqual(handle.provider_call_id, "EXO-1")
        args, kwargs = mock_post.call_args
        # URL uses subdomain + sid + Calls/connect.json
        self.assertIn("api.exotel.com/v1/Accounts/exo-sid/Calls/connect.json", args[0])
        # Form-encoded, not JSON
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/x-www-form-urlencoded")
        # Exotel: From=dialed number, CallerId=our DID — opposite to
        # Twilio/Plivo. We remapped from the adapter's nicer signature.
        body = kwargs["data"]
        self.assertIn("From=%2B14155550200", body)
        self.assertIn("CallerId=%2B14155550100", body)

    def test_subdomain_falls_back_to_default(self):
        # No subdomain in credentials → default api.exotel.com
        cfg = VoiceProviderConfig.objects.create(
            tenant=self.tenant,
            name="No subdomain",
            provider=VoiceProvider.EXOTEL,
            credentials=json.dumps({"sid": "s", "api_key": "k", "api_token": "t"}),
        )
        adapter = ExotelVoiceAdapter(cfg)
        self.assertIn("api.exotel.com", adapter._account_base())

    def test_hangup_raises_not_implemented(self):
        adapter = _make_exotel_adapter(self.tenant)
        with self.assertRaises(NotImplementedError):
            adapter.hangup("EXO-x")

    def test_gather_speech_raises_not_implemented(self):
        adapter = _make_exotel_adapter(self.tenant)
        with self.assertRaises(NotImplementedError):
            adapter.gather_speech("EXO-x", language="en-US", timeout_seconds=5)


# ─────────────────────────────────────────────────────────────────────────────
# Webhook path-token verification
# ─────────────────────────────────────────────────────────────────────────────


class ExotelWebhookVerifyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Exotel Verify Tenant")

    def setUp(self):
        self.factory = RequestFactory()

    def test_accepts_when_no_token_configured(self):
        adapter = _make_exotel_adapter(self.tenant, inbound_webhook_token=None)
        req = self.factory.post("/wh/")
        self.assertTrue(adapter.verify_webhook(req))

    def test_accepts_matching_token_via_post(self):
        adapter = _make_exotel_adapter(self.tenant, inbound_webhook_token="secret-token")
        req = self.factory.post("/wh/", data={"token": "secret-token"})
        self.assertTrue(adapter.verify_webhook(req))

    def test_accepts_matching_token_via_get(self):
        adapter = _make_exotel_adapter(self.tenant, inbound_webhook_token="secret-token")
        req = self.factory.post("/wh/?token=secret-token")
        self.assertTrue(adapter.verify_webhook(req))

    def test_rejects_wrong_token(self):
        adapter = _make_exotel_adapter(self.tenant, inbound_webhook_token="secret-token")
        req = self.factory.post("/wh/", data={"token": "wrong"})
        self.assertFalse(adapter.verify_webhook(req))

    def test_rejects_missing_token(self):
        adapter = _make_exotel_adapter(self.tenant, inbound_webhook_token="secret-token")
        req = self.factory.post("/wh/")
        self.assertFalse(adapter.verify_webhook(req))


# ─────────────────────────────────────────────────────────────────────────────
# parse_webhook
# ─────────────────────────────────────────────────────────────────────────────


class ExotelParseWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Exotel Parse Tenant")

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = _make_exotel_adapter(self.tenant)

    def _parse(self, post):
        return self.adapter.parse_webhook(self.factory.post("/wh/", data=post))

    def test_in_progress_maps_to_answered(self):
        ev = self._parse({"CallSid": "EX1", "Status": "in-progress"})
        self.assertEqual(ev.event_type, CallEventType.ANSWERED)
        self.assertIsNone(ev.hangup_cause)

    def test_completed_with_normal_clearing(self):
        ev = self._parse({"CallSid": "EX2", "Status": "completed"})
        self.assertEqual(ev.event_type, CallEventType.COMPLETED)
        self.assertEqual(ev.hangup_cause, HangupCause.NORMAL_CLEARING)

    def test_busy_with_user_busy(self):
        ev = self._parse({"CallSid": "EX3", "Status": "busy"})
        self.assertEqual(ev.event_type, CallEventType.FAILED)
        self.assertEqual(ev.hangup_cause, HangupCause.USER_BUSY)

    def test_no_answer_with_no_answer_cause(self):
        ev = self._parse({"CallSid": "EX4", "Status": "no-answer"})
        self.assertEqual(ev.event_type, CallEventType.FAILED)
        self.assertEqual(ev.hangup_cause, HangupCause.NO_ANSWER)

    def test_call_status_field_alias_works(self):
        """Some Exotel endpoints use ``CallStatus`` instead of ``Status``."""
        ev = self._parse({"CallSid": "EX5", "CallStatus": "completed"})
        self.assertEqual(ev.event_type, CallEventType.COMPLETED)


# ─────────────────────────────────────────────────────────────────────────────
# Status normalisation
# ─────────────────────────────────────────────────────────────────────────────


class ExotelStatusNormalisationTests(SimpleTestCase):
    def test_known(self):
        a = ExotelVoiceAdapter.__new__(ExotelVoiceAdapter)
        self.assertEqual(a._normalize_status("in-progress"), CallStatus.IN_PROGRESS)
        self.assertEqual(a._normalize_status("completed"), CallStatus.COMPLETED)

    def test_unknown_returns_none(self):
        a = ExotelVoiceAdapter.__new__(ExotelVoiceAdapter)
        self.assertIsNone(a._normalize_status("made-up"))


# ─────────────────────────────────────────────────────────────────────────────
# Webhook endpoints
# ─────────────────────────────────────────────────────────────────────────────


class ExotelWebhookEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Exotel WH Tenant")
        cls.config = VoiceProviderConfig.objects.create(
            tenant=cls.tenant,
            name="Exotel WH",
            provider=VoiceProvider.EXOTEL,
            credentials=json.dumps(
                {
                    "sid": "s-wh",
                    "api_key": "k",
                    "api_token": "t",
                    "subdomain": "api.exotel.com",
                }
            ),
            inbound_webhook_token="wh-token",
        )

    def _request(self, path, data, *, token="wh-token"):
        factory = RequestFactory()
        if token is not None:
            data = {**data, "token": token}
        return factory.post(path, data=data)

    @patch("voice.tasks.process_call_status.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_valid_status_queues_task(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True
        path = f"/voice/v1/webhooks/exotel/{self.config.id}/status/"
        request = self._request(path, {"CallSid": "EX-WH-1", "Status": "completed"})
        resp = ExotelStatusHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once()
        payload = mock_delay.call_args[0][0]
        self.assertEqual(payload["provider_call_id"], "EX-WH-1")

    @patch("voice.tasks.process_call_status.delay")
    def test_wrong_token_returns_403(self, mock_delay):
        path = f"/voice/v1/webhooks/exotel/{self.config.id}/status/"
        request = self._request(
            path,
            {"CallSid": "x", "Status": "completed"},
            token="wrong-token",
        )
        resp = ExotelStatusHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 403)
        mock_delay.assert_not_called()

    @patch("voice.recordings.tasks.download_recording.delay")
    @patch("voice.tasks.process_call_status.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_status_with_recording_url_queues_download(self, mock_redis, _process, mock_dl):
        mock_redis.return_value.set.return_value = True
        # Pre-create VoiceCall for resolution.
        from voice.constants import CallDirection, CallStatus
        from voice.models import VoiceCall

        call = VoiceCall.objects.create(
            tenant=self.tenant,
            name="exotel-rec",
            provider_config=self.config,
            provider_call_id="EX-REC",
            direction=CallDirection.OUTBOUND,
            from_number="+1",
            to_number="+2",
            status=CallStatus.COMPLETED,
        )
        path = f"/voice/v1/webhooks/exotel/{self.config.id}/status/"
        request = self._request(
            path,
            {
                "CallSid": "EX-REC",
                "Status": "completed",
                "RecordingUrl": "https://exotel/recordings/abc.mp3",
            },
        )
        resp = ExotelStatusHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        mock_dl.assert_called_once_with(str(call.id), "https://exotel/recordings/abc.mp3")

    def test_passthru_returns_hangup_xml(self):
        path = f"/voice/v1/webhooks/exotel/{self.config.id}/passthru/"
        request = self._request(path, {"CallSid": "EX-P"})
        resp = ExotelPassthruHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/xml")
        self.assertIn("<Hangup/>", resp.content.decode())
