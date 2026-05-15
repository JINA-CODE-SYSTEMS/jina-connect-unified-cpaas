"""Twilio webhook handler tests (#160).

Uses ``RequestFactory`` + ``ConcreteHandler.as_view()`` directly instead
of the Django test client + URL dispatch. Same coverage, but avoids the
URL resolver / middleware overhead that hangs unpredictably on this dev
box's Windows + DRF setup.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from tenants.models import Tenant
from voice.constants import VoiceProvider
from voice.models import VoiceProviderConfig
from voice.webhooks.twilio import (
    TwilioAnswerHandler,
    TwilioCallStatusHandler,
)


def _twilio_signature(auth_token: str, url: str, post: dict) -> str:
    params = sorted(post.items())
    data = url + "".join(k + v for k, v in params)
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


class TwilioCallStatusWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Webhook Tenant")
        cls.auth_token = "wh_test_token"
        cls.config = VoiceProviderConfig.objects.create(
            tenant=cls.tenant,
            name="Webhook Twilio",
            provider=VoiceProvider.TWILIO,
            credentials=json.dumps({"account_sid": "AC123", "auth_token": cls.auth_token}),
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.path = f"/voice/v1/webhooks/twilio/{self.config.id}/call-status/"

    def _signed_request(self, post: dict, signature: str | None = None):
        """Build a POST request with a valid (or specified) Twilio sig."""
        if signature is None:
            url = f"http://testserver{self.path}"
            signature = _twilio_signature(self.auth_token, url, post)
        request = self.factory.post(self.path, data=post)
        request.META["HTTP_X_TWILIO_SIGNATURE"] = signature
        return request

    def _dispatch(self, request):
        """Invoke the view exactly the way Django's URL resolver would."""
        view = TwilioCallStatusHandler.as_view()
        return view(request, config_uuid=str(self.config.id))

    @patch("voice.tasks.process_call_status.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_valid_signature_queues_task_and_returns_200(self, mock_redis, mock_delay):
        # SETNX returns True → key newly claimed → handler proceeds.
        mock_redis.return_value.set.return_value = True

        post = {"CallSid": "CA_ok", "CallStatus": "completed"}
        resp = self._dispatch(self._signed_request(post))

        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once()
        payload = mock_delay.call_args[0][0]
        self.assertEqual(payload["provider_call_id"], "CA_ok")

    @patch("voice.tasks.process_call_status.delay")
    def test_bad_signature_returns_403(self, mock_delay):
        request = self._signed_request(
            {"CallSid": "CA_x", "CallStatus": "completed"},
            signature="not-the-right-sig",
        )
        resp = self._dispatch(request)
        self.assertEqual(resp.status_code, 403)
        mock_delay.assert_not_called()

    @patch("voice.tasks.process_call_status.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_duplicate_event_returns_200_silent(self, mock_redis, mock_delay):
        """Same (CallSid, CallStatus) twice — second is a no-op silent ack.

        BaseWebhookHandler's SETNX returns ``None`` on a duplicate key.
        """
        # First call: True (new). Second call: None (duplicate).
        mock_redis.return_value.set.side_effect = [True, None]

        post = {"CallSid": "CA_dup", "CallStatus": "completed"}
        resp1 = self._dispatch(self._signed_request(post))
        resp2 = self._dispatch(self._signed_request(post))

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        # Task queued only on the first hit.
        self.assertEqual(mock_delay.call_count, 1)

    def test_unknown_config_uuid_returns_403(self):
        """A signature against an unknown config can't be verified — fail
        closed with 403 rather than leaking a 404."""
        bogus_uuid = str(uuid.uuid4())
        request = self.factory.post(
            f"/voice/v1/webhooks/twilio/{bogus_uuid}/call-status/",
            data={"CallSid": "x", "CallStatus": "completed"},
        )
        request.META["HTTP_X_TWILIO_SIGNATURE"] = "any"
        view = TwilioCallStatusHandler.as_view()
        resp = view(request, config_uuid=bogus_uuid)
        self.assertEqual(resp.status_code, 403)


class TwilioAnswerWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Answer Tenant")
        cls.auth_token = "ans_token"
        cls.config = VoiceProviderConfig.objects.create(
            tenant=cls.tenant,
            name="Answer Twilio",
            provider=VoiceProvider.TWILIO,
            credentials=json.dumps({"account_sid": "AC1", "auth_token": cls.auth_token}),
        )

    def test_answer_returns_hangup_twiml(self):
        factory = RequestFactory()
        path = f"/voice/v1/webhooks/twilio/{self.config.id}/answer/"
        post = {"CallSid": "CA_ans"}
        url = f"http://testserver{path}"
        sig = _twilio_signature(self.auth_token, url, post)

        request = factory.post(path, data=post)
        request.META["HTTP_X_TWILIO_SIGNATURE"] = sig

        view = TwilioAnswerHandler.as_view()
        resp = view(request, config_uuid=str(self.config.id))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/xml")
        body = resp.content.decode()
        self.assertIn("<Hangup/>", body)
