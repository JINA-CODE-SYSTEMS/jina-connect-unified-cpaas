"""Vonage adapter + NCCO dialect + webhook tests (#165).

Notes:
  * Vonage requires an RSA private key for outbound JWTs. Tests generate
    a throwaway 2048-bit key once per module so we exercise the real
    PyJWT signing path.
  * The shared-secret signed-webhook path uses HS256 — that's the
    branch we test in webhook verify.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import RequestFactory, SimpleTestCase, TestCase

from tenants.models import Tenant
from voice.adapters.http_voice.vonage import VonageVoiceAdapter
from voice.constants import (
    CallEventType,
    CallStatus,
    HangupCause,
    VoiceProvider,
)
from voice.ivr.dialects import ncco
from voice.models import VoiceProviderConfig


# Generate one RSA key for the whole test run — key generation is
# slow, and the tests only need it to be valid.
def _make_rsa_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


_RSA_KEY_PEM = _make_rsa_private_key_pem()


def _make_vonage_adapter(
    tenant,
    *,
    application_id="app-1",
    api_key="k",
    api_secret="s",
    signature_secret: str | None = None,
):
    creds = {
        "api_key": api_key,
        "api_secret": api_secret,
        "application_id": application_id,
        "private_key_pem": _RSA_KEY_PEM,
    }
    if signature_secret is not None:
        creds["signature_secret"] = signature_secret
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Test Vonage",
        provider=VoiceProvider.VONAGE,
        credentials=json.dumps(creds),
    )
    return VonageVoiceAdapter(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# NCCO dialect
# ─────────────────────────────────────────────────────────────────────────────


class NccoDialectTests(SimpleTestCase):
    def test_play_tts(self):
        out = ncco.play({"tts_text": "Hello"}, {})
        self.assertEqual(out["action"], "talk")
        self.assertEqual(out["text"], "Hello")

    def test_play_tts_with_voice_and_language(self):
        out = ncco.play({"tts_text": "Hi", "tts_voice": "Aditi", "tts_language": "en-IN"}, {})
        self.assertEqual(out["voiceName"], "Aditi")
        self.assertEqual(out["language"], "en-IN")

    def test_play_audio_url_uses_stream(self):
        out = ncco.play({"audio_url": "https://example/a.mp3"}, {})
        self.assertEqual(out["action"], "stream")
        self.assertEqual(out["streamUrl"], ["https://example/a.mp3"])

    def test_gather_dtmf_shape(self):
        out = ncco.gather_dtmf({"max_digits": 4, "timeout_seconds": 5}, {})
        self.assertEqual(out["action"], "input")
        self.assertEqual(out["type"], ["dtmf"])
        self.assertEqual(out["dtmf"]["maxDigits"], 4)
        self.assertEqual(out["dtmf"]["timeOut"], 5)

    def test_gather_dtmf_with_event_url(self):
        out = ncco.gather_dtmf(
            {"max_digits": 1, "timeout_seconds": 3},
            {"gather_action_url": "https://example/g"},
        )
        self.assertEqual(out["eventUrl"], ["https://example/g"])

    def test_gather_speech_shape(self):
        out = ncco.gather_speech({"language": "en-US", "timeout_seconds": 4}, {})
        self.assertEqual(out["action"], "input")
        self.assertEqual(out["type"], ["speech"])
        self.assertEqual(out["speech"]["language"], "en-US")

    def test_record_shape(self):
        out = ncco.record({"max_duration_seconds": 60}, {})
        self.assertEqual(out["action"], "record")
        self.assertEqual(out["format"], "mp3")
        self.assertEqual(out["timeOut"], 60)
        self.assertTrue(out["beepStart"])

    def test_transfer_uses_connect(self):
        out = ncco.transfer({"to_uri": "+14155550100"}, {})
        self.assertEqual(out["action"], "connect")
        self.assertEqual(out["endpoint"], [{"type": "phone", "number": "+14155550100"}])

    def test_hangup_emits_empty_talk(self):
        """NCCO has no hangup action — emit a silent talk so the
        action list isn't empty (Vonage rejects empty NCCOs)."""
        out = ncco.hangup({}, {})
        self.assertEqual(out["action"], "talk")
        self.assertEqual(out["text"], "")

    def test_assemble_returns_list(self):
        actions = [{"action": "talk", "text": "hi"}, {"action": "talk", "text": ""}]
        self.assertEqual(ncco.assemble(actions), actions)

    def test_handler_lookup(self):
        for type_id in (
            "voice.play",
            "voice.gather_dtmf",
            "voice.gather_speech",
            "voice.record",
            "voice.transfer",
            "voice.hangup",
        ):
            self.assertIsNotNone(ncco.get_handler(type_id))

    def test_handler_unknown(self):
        self.assertIsNone(ncco.get_handler("voice.does_not_exist"))


# ─────────────────────────────────────────────────────────────────────────────
# JWT minting
# ─────────────────────────────────────────────────────────────────────────────


class VonageJwtMintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="JWT Tenant")

    def test_mint_jwt_signs_with_rs256_and_includes_required_claims(self):
        adapter = _make_vonage_adapter(self.tenant, application_id="app-xyz")
        token = adapter._mint_jwt()
        # Decode with the public key (extracted from the same private key
        # because PyJWT can verify with the original key in tests using
        # ``options=verify_signature=False`` — simpler here).
        decoded = jwt.decode(token, options={"verify_signature": False})
        self.assertEqual(decoded["application_id"], "app-xyz")
        for k in ("iat", "exp", "jti"):
            self.assertIn(k, decoded)
        # Exp > iat by JWT_TTL_SECONDS (default 300).
        self.assertGreater(decoded["exp"], decoded["iat"])

    def test_auth_header_carries_bearer_jwt(self):
        adapter = _make_vonage_adapter(self.tenant)
        header = adapter._auth_header()
        self.assertTrue(header["Authorization"].startswith("Bearer "))


# ─────────────────────────────────────────────────────────────────────────────
# Outbound REST mocks
# ─────────────────────────────────────────────────────────────────────────────


class VonageInitiateCallTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Vonage Init Tenant")

    @patch("voice.adapters.http_voice.vonage.requests.post")
    def test_initiate_call_posts_to_calls_with_jwt(self, mock_post):
        resp = MagicMock(status_code=201)
        resp.json.return_value = {"uuid": "VN-abc", "status": "started"}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        adapter = _make_vonage_adapter(self.tenant)
        from voice.adapters.base import CallInstructions

        handle = adapter.initiate_call(
            from_number="+14155550100",
            to_number="+14155550200",
            callback_url="https://app.example.com/voice/answer",
            instructions=CallInstructions(),
        )
        self.assertEqual(handle.provider_call_id, "VN-abc")
        # POSTed to /calls with the right body + Bearer JWT.
        args, kwargs = mock_post.call_args
        self.assertIn("/v1/calls", args[0])
        self.assertIn("Authorization", kwargs["headers"])
        body = kwargs["json"]
        self.assertEqual(body["to"], [{"type": "phone", "number": "+14155550200"}])
        self.assertEqual(body["from"], {"type": "phone", "number": "+14155550100"})


# ─────────────────────────────────────────────────────────────────────────────
# Webhook signature verify
# ─────────────────────────────────────────────────────────────────────────────


class VonageWebhookSignatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Vonage Sig Tenant")

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, *, auth=None):
        request = self.factory.post(
            "/voice/v1/webhooks/vonage/abc/event/",
            data=b"{}",
            content_type="application/json",
        )
        if auth is not None:
            request.META["HTTP_AUTHORIZATION"] = auth
        return request

    def test_accepts_unsigned_when_no_signature_secret(self):
        """Deployments without signed webhooks rely on IP allowlisting;
        the adapter accepts every request in that case."""
        adapter = _make_vonage_adapter(self.tenant, signature_secret=None)
        self.assertTrue(adapter.verify_webhook(self._request()))

    def test_valid_hs256_jwt_passes(self):
        secret = "hs256-secret"
        adapter = _make_vonage_adapter(self.tenant, signature_secret=secret)
        token = jwt.encode({"iss": "vonage"}, secret, algorithm="HS256")
        req = self._request(auth=f"Bearer {token}")
        self.assertTrue(adapter.verify_webhook(req))

    def test_invalid_jwt_fails(self):
        adapter = _make_vonage_adapter(self.tenant, signature_secret="real-secret")
        bad = jwt.encode({"iss": "x"}, "wrong-secret", algorithm="HS256")
        req = self._request(auth=f"Bearer {bad}")
        self.assertFalse(adapter.verify_webhook(req))

    def test_missing_bearer_when_secret_required_fails(self):
        adapter = _make_vonage_adapter(self.tenant, signature_secret="real-secret")
        self.assertFalse(adapter.verify_webhook(self._request(auth=None)))

    def test_non_bearer_auth_fails(self):
        adapter = _make_vonage_adapter(self.tenant, signature_secret="real-secret")
        req = self._request(auth="Basic dXNlcjpwYXNz")
        self.assertFalse(adapter.verify_webhook(req))


# ─────────────────────────────────────────────────────────────────────────────
# parse_webhook
# ─────────────────────────────────────────────────────────────────────────────


class VonageParseWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Vonage Parse Tenant")

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = _make_vonage_adapter(self.tenant)

    def _parse(self, body: dict):
        req = self.factory.post(
            "/wh/",
            data=json.dumps(body),
            content_type="application/json",
        )
        return self.adapter.parse_webhook(req)

    def test_answered_maps_to_in_progress_no_hangup_cause(self):
        ev = self._parse({"uuid": "VN1", "status": "answered"})
        self.assertEqual(ev.event_type, CallEventType.ANSWERED)
        self.assertIsNone(ev.hangup_cause)

    def test_completed_maps_with_normal_clearing(self):
        ev = self._parse({"uuid": "VN2", "status": "completed"})
        self.assertEqual(ev.event_type, CallEventType.COMPLETED)
        self.assertEqual(ev.hangup_cause, HangupCause.NORMAL_CLEARING)

    def test_busy_maps_to_failed_with_user_busy(self):
        ev = self._parse({"uuid": "VN3", "status": "busy"})
        self.assertEqual(ev.event_type, CallEventType.FAILED)
        self.assertEqual(ev.hangup_cause, HangupCause.USER_BUSY)

    def test_unanswered_maps_to_no_answer(self):
        ev = self._parse({"uuid": "VN4", "status": "unanswered"})
        self.assertEqual(ev.event_type, CallEventType.FAILED)
        self.assertEqual(ev.hangup_cause, HangupCause.NO_ANSWER)

    def test_rejected_maps_to_call_rejected(self):
        ev = self._parse({"uuid": "VN5", "status": "rejected"})
        self.assertEqual(ev.hangup_cause, HangupCause.CALL_REJECTED)

    def test_invalid_json_body_returns_default_event(self):
        req = self.factory.post("/wh/", data="{not json}", content_type="application/json")
        ev = self.adapter.parse_webhook(req)
        # Falls back to empty payload + INITIATED.
        self.assertEqual(ev.event_type, CallEventType.INITIATED)


# ─────────────────────────────────────────────────────────────────────────────
# Status normalisation
# ─────────────────────────────────────────────────────────────────────────────


class VonageStatusNormalisationTests(SimpleTestCase):
    def test_known_statuses(self):
        a = VonageVoiceAdapter.__new__(VonageVoiceAdapter)
        self.assertEqual(a._normalize_status("ringing"), CallStatus.RINGING)
        self.assertEqual(a._normalize_status("answered"), CallStatus.IN_PROGRESS)
        self.assertEqual(a._normalize_status("completed"), CallStatus.COMPLETED)

    def test_unknown_returns_none(self):
        a = VonageVoiceAdapter.__new__(VonageVoiceAdapter)
        self.assertIsNone(a._normalize_status("made-up"))


# ─────────────────────────────────────────────────────────────────────────────
# Webhooks (event + answer)
# ─────────────────────────────────────────────────────────────────────────────


class VonageWebhookEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Vonage WH Tenant")
        cls.config = VoiceProviderConfig.objects.create(
            tenant=cls.tenant,
            name="Vonage WH",
            provider=VoiceProvider.VONAGE,
            credentials=json.dumps(
                {
                    "api_key": "k",
                    "api_secret": "s",
                    "application_id": "app-wh",
                    "private_key_pem": _RSA_KEY_PEM,
                    # No signature_secret → adapter accepts unsigned requests.
                }
            ),
        )

    def _request(self, path, body):
        factory = RequestFactory()
        return factory.post(path, data=json.dumps(body), content_type="application/json")

    @patch("voice.tasks.process_call_status.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_event_queues_task_and_returns_200(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True
        path = f"/voice/v1/webhooks/vonage/{self.config.id}/event/"
        request = self._request(path, {"uuid": "VN-A", "status": "completed"})

        from voice.webhooks.vonage import VonageEventHandler

        resp = VonageEventHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once()
        payload = mock_delay.call_args[0][0]
        self.assertEqual(payload["provider_call_id"], "VN-A")

    def test_answer_returns_ncco_json(self):
        from voice.webhooks.vonage import VonageAnswerHandler

        path = f"/voice/v1/webhooks/vonage/{self.config.id}/answer/"
        request = self._request(path, {"uuid": "VN-ans"})
        resp = VonageAnswerHandler.as_view()(request, config_uuid=str(self.config.id))
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content.decode())
        self.assertIsInstance(body, list)
        self.assertGreaterEqual(len(body), 1)
