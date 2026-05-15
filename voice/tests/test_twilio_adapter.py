"""TwilioVoiceAdapter tests (#160).

Mock-based — no real Twilio API calls. Verifies:

  * Signature verification (Twilio HMAC-SHA1 of URL + sorted params)
  * Status normalisation (Twilio CallStatus → our CallStatus)
  * parse_webhook → NormalizedCallEvent shape
  * initiate_call posts the right body / auth
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase

from tenants.models import Tenant
from voice.adapters.http_voice.twilio import TwilioVoiceAdapter
from voice.constants import (
    CallEventType,
    CallStatus,
    HangupCause,
    VoiceProvider,
)
from voice.models import VoiceProviderConfig


def _make_adapter(tenant, auth_token="test_token", account_sid="AC123"):
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Test Twilio",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": account_sid, "auth_token": auth_token}),
    )
    return TwilioVoiceAdapter(cfg)


def _twilio_signature(auth_token: str, url: str, post: dict) -> str:
    """Compute Twilio's signature exactly the way the adapter does, so
    tests can produce valid signatures."""
    params = sorted(post.items())
    data = url + "".join(k + v for k, v in params)
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


class SignatureVerifyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Twilio Sig Tenant")

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = _make_adapter(self.tenant, auth_token="secret_token")

    def _make_request(self, post: dict, signature: str | None):
        request = self.factory.post("/voice/v1/webhooks/twilio/abc/call-status/", data=post)
        if signature is not None:
            request.META["HTTP_X_TWILIO_SIGNATURE"] = signature
        return request

    def test_valid_signature_passes(self):
        post = {"CallSid": "CAabc", "CallStatus": "in-progress"}
        url = self.factory.post("/voice/v1/webhooks/twilio/abc/call-status/", data=post).build_absolute_uri()
        sig = _twilio_signature("secret_token", url, post)
        req = self._make_request(post, sig)
        self.assertTrue(self.adapter.verify_webhook(req))

    def test_missing_header_fails(self):
        req = self._make_request({"CallSid": "x"}, signature=None)
        self.assertFalse(self.adapter.verify_webhook(req))

    def test_wrong_signature_fails(self):
        req = self._make_request({"CallSid": "x"}, signature="wrong")
        self.assertFalse(self.adapter.verify_webhook(req))

    def test_missing_auth_token_fails(self):
        adapter = _make_adapter(self.tenant, auth_token="")
        req = self._make_request({"CallSid": "x"}, signature="anything")
        self.assertFalse(adapter.verify_webhook(req))


class ParseWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Twilio Parse Tenant")

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = _make_adapter(self.tenant)

    def _parse(self, post):
        return self.adapter.parse_webhook(self.factory.post("/wh/", data=post))

    def test_in_progress_maps_to_answered(self):
        event = self._parse({"CallSid": "CA1", "CallStatus": "in-progress"})
        self.assertEqual(event.provider_call_id, "CA1")
        self.assertEqual(event.event_type, CallEventType.ANSWERED)
        self.assertIsNone(event.hangup_cause)

    def test_completed_maps_with_hangup_cause(self):
        event = self._parse({"CallSid": "CA2", "CallStatus": "completed"})
        self.assertEqual(event.event_type, CallEventType.COMPLETED)
        self.assertEqual(event.hangup_cause, HangupCause.NORMAL_CLEARING)

    def test_busy_maps_to_failed_with_user_busy(self):
        event = self._parse({"CallSid": "CA3", "CallStatus": "busy"})
        self.assertEqual(event.event_type, CallEventType.FAILED)
        self.assertEqual(event.hangup_cause, HangupCause.USER_BUSY)

    def test_no_answer_maps_to_failed_with_no_answer_cause(self):
        event = self._parse({"CallSid": "CA4", "CallStatus": "no-answer"})
        self.assertEqual(event.event_type, CallEventType.FAILED)
        self.assertEqual(event.hangup_cause, HangupCause.NO_ANSWER)

    def test_ringing_no_hangup_cause(self):
        event = self._parse({"CallSid": "CA5", "CallStatus": "ringing"})
        self.assertEqual(event.event_type, CallEventType.RINGING)
        self.assertIsNone(event.hangup_cause)


class StatusNormalisationTests(TestCase):
    def test_known_statuses(self):
        adapter = TwilioVoiceAdapter.__new__(TwilioVoiceAdapter)
        self.assertEqual(adapter._normalize_status("in-progress"), CallStatus.IN_PROGRESS)
        self.assertEqual(adapter._normalize_status("completed"), CallStatus.COMPLETED)
        self.assertEqual(adapter._normalize_status("queued"), CallStatus.QUEUED)

    def test_unknown_returns_none(self):
        adapter = TwilioVoiceAdapter.__new__(TwilioVoiceAdapter)
        self.assertIsNone(adapter._normalize_status("made-up-status"))


class InitiateCallTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Twilio Init Tenant")

    def setUp(self):
        self.adapter = _make_adapter(self.tenant)

    @patch.object(TwilioVoiceAdapter, "_request")
    def test_initiate_call_posts_to_calls_endpoint(self, mock_request):
        mock_resp = MagicMock(status_code=201)
        mock_resp.json.return_value = {"sid": "CA_initiated", "status": "queued"}
        mock_resp.raise_for_status.return_value = None
        mock_request.return_value = mock_resp

        from voice.adapters.base import CallInstructions

        handle = self.adapter.initiate_call(
            from_number="+14155550100",
            to_number="+14155550200",
            callback_url="https://app.example.com/voice/answer",
            instructions=CallInstructions(),
        )

        self.assertEqual(handle.provider_call_id, "CA_initiated")
        mock_request.assert_called_once()
        method, url = mock_request.call_args[0]
        self.assertEqual(method, "POST")
        self.assertIn("/Calls.json", url)
        sent_data = mock_request.call_args[1]["data"]
        self.assertEqual(sent_data["From"], "+14155550100")
        self.assertEqual(sent_data["To"], "+14155550200")
