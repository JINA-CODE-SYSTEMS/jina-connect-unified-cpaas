"""Telnyx Call Control adapter + dialect + webhook tests (#166)."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import RequestFactory, SimpleTestCase, TestCase

from tenants.models import Tenant
from voice.adapters.http_voice.telnyx import TelnyxVoiceAdapter
from voice.constants import (
    CallEventType,
    CallStatus,
    HangupCause,
    VoiceProvider,
)
from voice.ivr.dialects import telnyx_cc
from voice.models import VoiceProviderConfig
from voice.webhooks.telnyx import TelnyxEventHandler


# Generate an Ed25519 keypair once per test run for signature tests.
def _make_ed25519_keypair() -> tuple[Ed25519PrivateKey, str]:
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, base64.b64encode(pub_bytes).decode()


_PRIV_KEY, _PUB_KEY_B64 = _make_ed25519_keypair()


def _make_telnyx_adapter(
    tenant,
    *,
    api_key="KEY-1",
    connection_id="conn-1",
    public_key: str | None = _PUB_KEY_B64,
):
    creds = {
        "api_key": api_key,
        "connection_id": connection_id,
    }
    if public_key is not None:
        creds["public_key"] = public_key
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Test Telnyx",
        provider=VoiceProvider.TELNYX,
        credentials=json.dumps(creds),
    )
    return TelnyxVoiceAdapter(cfg)


def _sign_telnyx_webhook(timestamp: str, body: bytes) -> str:
    """Sign a webhook payload the same way Telnyx does."""
    message = f"{timestamp}|".encode() + body
    sig = _PRIV_KEY.sign(message)
    return base64.b64encode(sig).decode()


# ─────────────────────────────────────────────────────────────────────────────
# Telnyx CC dialect
# ─────────────────────────────────────────────────────────────────────────────


class TelnyxCcDialectTests(SimpleTestCase):
    def test_play_tts(self):
        out = telnyx_cc.play({"tts_text": "Hello"}, {})
        self.assertEqual(out["verb"], "speak")
        self.assertEqual(out["payload"]["payload"], "Hello")

    def test_play_tts_with_voice_and_language(self):
        out = telnyx_cc.play({"tts_text": "Hi", "tts_voice": "female", "tts_language": "en-IN"}, {})
        self.assertEqual(out["payload"]["voice"], "female")
        self.assertEqual(out["payload"]["language"], "en-IN")

    def test_play_audio_url_uses_playback_start(self):
        out = telnyx_cc.play({"audio_url": "https://example/a.mp3"}, {})
        self.assertEqual(out["verb"], "playback_start")
        self.assertEqual(out["payload"]["audio_url"], "https://example/a.mp3")

    def test_gather_dtmf_bare(self):
        out = telnyx_cc.gather_dtmf({"max_digits": 4, "timeout_seconds": 5}, {})
        self.assertEqual(out["verb"], "gather")
        self.assertEqual(out["payload"]["maximum_digits"], 4)
        self.assertEqual(out["payload"]["timeout_millis"], 5000)

    def test_gather_dtmf_with_tts_prompt_uses_gather_using_speak(self):
        out = telnyx_cc.gather_dtmf({"max_digits": 1, "timeout_seconds": 3, "prompt_tts": "Choose"}, {})
        self.assertEqual(out["verb"], "gather_using_speak")
        self.assertEqual(out["payload"]["payload"], "Choose")

    def test_gather_dtmf_with_audio_prompt_uses_gather_using_audio(self):
        out = telnyx_cc.gather_dtmf(
            {
                "max_digits": 1,
                "timeout_seconds": 3,
                "prompt_audio_url": "https://example/a.mp3",
            },
            {},
        )
        self.assertEqual(out["verb"], "gather_using_audio")
        self.assertEqual(out["payload"]["audio_url"], "https://example/a.mp3")

    def test_gather_dtmf_with_finish_on_key(self):
        out = telnyx_cc.gather_dtmf({"max_digits": 10, "timeout_seconds": 5, "finish_on_key": "#"}, {})
        self.assertEqual(out["payload"]["terminating_digit"], "#")

    def test_gather_speech_uses_transcription_start(self):
        out = telnyx_cc.gather_speech({"language": "en-US", "timeout_seconds": 5}, {})
        self.assertEqual(out["verb"], "transcription_start")
        self.assertEqual(out["payload"]["language"], "en-US")

    def test_record(self):
        out = telnyx_cc.record({"max_duration_seconds": 60}, {})
        self.assertEqual(out["verb"], "record_start")
        self.assertEqual(out["payload"]["max_length"], 60)
        self.assertTrue(out["payload"]["play_beep"])

    def test_transfer(self):
        out = telnyx_cc.transfer({"to_uri": "+14155550100"}, {})
        self.assertEqual(out, {"verb": "transfer", "payload": {"to": "+14155550100"}})

    def test_hangup(self):
        self.assertEqual(telnyx_cc.hangup({}, {}), {"verb": "hangup", "payload": {}})

    def test_assemble_preserves_order(self):
        ops = [{"verb": "speak", "payload": {}}, {"verb": "hangup", "payload": {}}]
        self.assertEqual(telnyx_cc.assemble(ops), ops)

    def test_handler_lookup(self):
        for type_id in (
            "voice.play",
            "voice.gather_dtmf",
            "voice.gather_speech",
            "voice.record",
            "voice.transfer",
            "voice.hangup",
        ):
            self.assertIsNotNone(telnyx_cc.get_handler(type_id))

    def test_handler_unknown(self):
        self.assertIsNone(telnyx_cc.get_handler("voice.x"))


# ─────────────────────────────────────────────────────────────────────────────
# Adapter — outbound REST (mocked)
# ─────────────────────────────────────────────────────────────────────────────


class TelnyxInitiateCallTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Telnyx Init Tenant")

    @patch("voice.adapters.http_voice.telnyx.requests.post")
    def test_initiate_call_posts_to_calls(self, mock_post):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": {"call_control_id": "CC-1"}}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        adapter = _make_telnyx_adapter(self.tenant)
        from voice.adapters.base import CallInstructions

        handle = adapter.initiate_call(
            from_number="+14155550100",
            to_number="+14155550200",
            callback_url="https://app.example.com/voice/event",
            instructions=CallInstructions(),
        )
        self.assertEqual(handle.provider_call_id, "CC-1")
        args, kwargs = mock_post.call_args
        self.assertIn("/v2/calls", args[0])
        body = kwargs["json"]
        self.assertEqual(body["from"], "+14155550100")
        self.assertEqual(body["to"], "+14155550200")
        self.assertEqual(body["connection_id"], "conn-1")
        # Bearer auth header.
        self.assertTrue(kwargs["headers"]["Authorization"].startswith("Bearer "))

    @patch("voice.adapters.http_voice.telnyx.requests.post")
    def test_initiate_call_includes_outbound_profile_when_set(self, mock_post):
        resp = MagicMock()
        resp.json.return_value = {"data": {"call_control_id": "CC-2"}}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        cfg = VoiceProviderConfig.objects.create(
            tenant=self.tenant,
            name="Telnyx with profile",
            provider=VoiceProvider.TELNYX,
            credentials=json.dumps(
                {
                    "api_key": "K",
                    "connection_id": "conn-2",
                    "outbound_voice_profile_id": "PROF-1",
                }
            ),
        )
        adapter = TelnyxVoiceAdapter(cfg)
        from voice.adapters.base import CallInstructions

        adapter.initiate_call(
            from_number="+1",
            to_number="+2",
            callback_url="https://app/event",
            instructions=CallInstructions(),
        )
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["outbound_voice_profile_id"], "PROF-1")


class TelnyxPerCommandActionsTests(TestCase):
    """Telnyx is command-driven — each action POSTs to a dedicated path."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Telnyx Actions Tenant")

    @patch("voice.adapters.http_voice.telnyx.requests.post")
    def test_hangup_posts_to_actions_hangup(self, mock_post):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        adapter = _make_telnyx_adapter(self.tenant)
        adapter.hangup("CC-1")
        url = mock_post.call_args[0][0]
        self.assertTrue(url.endswith("/calls/CC-1/actions/hangup"))

    @patch("voice.adapters.http_voice.telnyx.requests.post")
    def test_play_tts_posts_to_speak(self, mock_post):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        adapter = _make_telnyx_adapter(self.tenant)
        adapter.play("CC-1", tts_text="hi", tts_voice="female")
        url = mock_post.call_args[0][0]
        self.assertTrue(url.endswith("/actions/speak"))
        self.assertEqual(mock_post.call_args[1]["json"]["payload"], "hi")
        self.assertEqual(mock_post.call_args[1]["json"]["voice"], "female")

    @patch("voice.adapters.http_voice.telnyx.requests.post")
    def test_play_audio_posts_to_playback_start(self, mock_post):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        adapter = _make_telnyx_adapter(self.tenant)
        adapter.play("CC-1", audio_url="https://example/a.mp3")
        url = mock_post.call_args[0][0]
        self.assertTrue(url.endswith("/actions/playback_start"))

    @patch("voice.adapters.http_voice.telnyx.requests.post")
    def test_transfer_posts_to_actions_transfer(self, mock_post):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        adapter = _make_telnyx_adapter(self.tenant)
        adapter.transfer("CC-1", to_uri="+14155550199")
        url = mock_post.call_args[0][0]
        self.assertTrue(url.endswith("/actions/transfer"))
        self.assertEqual(mock_post.call_args[1]["json"]["to"], "+14155550199")


# ─────────────────────────────────────────────────────────────────────────────
# Webhook Ed25519 signature verify
# ─────────────────────────────────────────────────────────────────────────────


class TelnyxSignatureVerifyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Telnyx Sig Tenant")

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = _make_telnyx_adapter(self.tenant)

    def _request(self, body: bytes, *, ts: str | None = None, sig: str | None = None):
        request = self.factory.post(
            "/voice/v1/webhooks/telnyx/abc/event/",
            data=body,
            content_type="application/json",
        )
        if ts is not None:
            request.META["HTTP_TELNYX_TIMESTAMP"] = ts
        if sig is not None:
            request.META["HTTP_TELNYX_SIGNATURE_ED25519"] = sig
        return request

    def test_valid_signature_passes(self):
        body = b'{"data": {"event_type": "call.answered"}}'
        ts = str(int(time.time()))
        sig = _sign_telnyx_webhook(ts, body)
        self.assertTrue(self.adapter.verify_webhook(self._request(body, ts=ts, sig=sig)))

    def test_missing_headers_fail(self):
        body = b'{"data": {}}'
        self.assertFalse(self.adapter.verify_webhook(self._request(body)))

    def test_wrong_signature_fails(self):
        body = b'{"data": {}}'
        ts = str(int(time.time()))
        self.assertFalse(self.adapter.verify_webhook(self._request(body, ts=ts, sig="not-a-sig")))

    def test_tampered_body_fails(self):
        body = b'{"data": {"event_type": "call.answered"}}'
        ts = str(int(time.time()))
        sig = _sign_telnyx_webhook(ts, body)
        tampered_body = b'{"data": {"event_type": "call.hangup"}}'
        self.assertFalse(self.adapter.verify_webhook(self._request(tampered_body, ts=ts, sig=sig)))

    def test_old_timestamp_fails(self):
        body = b'{"data": {}}'
        ts = str(int(time.time()) - 3600)  # 1 hour old
        sig = _sign_telnyx_webhook(ts, body)
        self.assertFalse(self.adapter.verify_webhook(self._request(body, ts=ts, sig=sig)))

    def test_missing_public_key_fails(self):
        adapter = _make_telnyx_adapter(self.tenant, public_key=None)
        body = b'{"data": {}}'
        ts = str(int(time.time()))
        sig = _sign_telnyx_webhook(ts, body)
        self.assertFalse(adapter.verify_webhook(self._request(body, ts=ts, sig=sig)))


# ─────────────────────────────────────────────────────────────────────────────
# parse_webhook
# ─────────────────────────────────────────────────────────────────────────────


class TelnyxParseWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Telnyx Parse Tenant")

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = _make_telnyx_adapter(self.tenant)

    def _parse(self, envelope: dict):
        req = self.factory.post("/wh/", data=json.dumps(envelope), content_type="application/json")
        return self.adapter.parse_webhook(req)

    def test_call_initiated(self):
        ev = self._parse(
            {
                "data": {
                    "event_type": "call.initiated",
                    "payload": {"call_control_id": "CC-1"},
                }
            }
        )
        self.assertEqual(ev.provider_call_id, "CC-1")
        self.assertEqual(ev.event_type, CallEventType.INITIATED)
        self.assertIsNone(ev.hangup_cause)

    def test_call_answered(self):
        ev = self._parse(
            {
                "data": {
                    "event_type": "call.answered",
                    "payload": {"call_control_id": "CC-2"},
                }
            }
        )
        self.assertEqual(ev.event_type, CallEventType.ANSWERED)

    def test_call_hangup_with_user_busy(self):
        ev = self._parse(
            {
                "data": {
                    "event_type": "call.hangup",
                    "payload": {
                        "call_control_id": "CC-3",
                        "hangup_cause": "user_busy",
                    },
                }
            }
        )
        self.assertEqual(ev.event_type, CallEventType.COMPLETED)
        self.assertEqual(ev.hangup_cause, HangupCause.USER_BUSY)

    def test_call_hangup_unknown_cause_maps_to_unknown(self):
        ev = self._parse(
            {
                "data": {
                    "event_type": "call.hangup",
                    "payload": {
                        "call_control_id": "CC-4",
                        "hangup_cause": "made_up_cause",
                    },
                }
            }
        )
        self.assertEqual(ev.hangup_cause, HangupCause.UNKNOWN)

    def test_invalid_json_returns_default_event(self):
        req = self.factory.post("/wh/", data="{not json}", content_type="application/json")
        ev = self.adapter.parse_webhook(req)
        self.assertEqual(ev.event_type, CallEventType.INITIATED)


# ─────────────────────────────────────────────────────────────────────────────
# Webhook endpoint
# ─────────────────────────────────────────────────────────────────────────────


class TelnyxWebhookEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Telnyx WH Tenant")
        cls.config = VoiceProviderConfig.objects.create(
            tenant=cls.tenant,
            name="Telnyx WH",
            provider=VoiceProvider.TELNYX,
            credentials=json.dumps(
                {
                    "api_key": "K-wh",
                    "connection_id": "conn-wh",
                    "public_key": _PUB_KEY_B64,
                }
            ),
        )

    def _signed(self, path: str, envelope: dict):
        body = json.dumps(envelope).encode()
        ts = str(int(time.time()))
        sig = _sign_telnyx_webhook(ts, body)
        factory = RequestFactory()
        request = factory.post(path, data=body, content_type="application/json")
        request.META["HTTP_TELNYX_TIMESTAMP"] = ts
        request.META["HTTP_TELNYX_SIGNATURE_ED25519"] = sig
        return request

    @patch("voice.tasks.process_call_status.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_valid_event_queues_task(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True
        path = f"/voice/v1/webhooks/telnyx/{self.config.id}/event/"
        envelope = {
            "data": {
                "id": "evt-1",
                "event_type": "call.answered",
                "payload": {"call_control_id": "CC-WH-1"},
            }
        }
        resp = TelnyxEventHandler.as_view()(self._signed(path, envelope), config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once()
        payload = mock_delay.call_args[0][0]
        self.assertEqual(payload["provider_call_id"], "CC-WH-1")

    @patch("voice.tasks.process_call_status.delay")
    def test_bad_signature_returns_403(self, mock_delay):
        path = f"/voice/v1/webhooks/telnyx/{self.config.id}/event/"
        factory = RequestFactory()
        request = factory.post(
            path,
            data=b'{"data": {"event_type": "call.answered"}}',
            content_type="application/json",
        )
        request.META["HTTP_TELNYX_TIMESTAMP"] = str(int(time.time()))
        request.META["HTTP_TELNYX_SIGNATURE_ED25519"] = "bogus"
        resp = TelnyxEventHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 403)
        mock_delay.assert_not_called()

    @patch("voice.recordings.tasks.download_recording.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_recording_saved_queues_download(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True
        # Pre-create the VoiceCall so the handler can resolve it.
        from voice.constants import CallDirection
        from voice.models import VoiceCall

        call = VoiceCall.objects.create(
            tenant=self.tenant,
            name="telnyx-rec",
            provider_config=self.config,
            provider_call_id="CC-REC",
            direction=CallDirection.OUTBOUND,
            from_number="+1",
            to_number="+2",
            status=CallStatus.COMPLETED,
        )
        path = f"/voice/v1/webhooks/telnyx/{self.config.id}/event/"
        envelope = {
            "data": {
                "id": "evt-rec",
                "event_type": "call.recording.saved",
                "payload": {
                    "call_control_id": "CC-REC",
                    "recording_id": "REC-1",
                },
            }
        }
        resp = TelnyxEventHandler.as_view()(self._signed(path, envelope), config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once_with(str(call.id), "REC-1")
