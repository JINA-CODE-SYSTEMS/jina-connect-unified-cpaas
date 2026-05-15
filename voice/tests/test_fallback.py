"""SMS fallback tests (#172).

Covers:

  * ``render_template`` — happy path, missing variable, no template.
  * ``maybe_send_sms_fallback`` — success case dispatches SMS,
    failure → no dispatch, idempotency, cause filter, disabled config,
    no-app config, empty template, broadcast override (True / False /
    None), broken SMS sender swallowed.
  * Signal wiring — ``call_completed`` invokes ``maybe_send_sms_fallback``.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase

from contacts.models import TenantContact
from sms.models import SMSApp
from tenants.models import Tenant
from voice.adapters.http_voice.twilio import TwilioVoiceAdapter
from voice.adapters.registry import (
    _ADAPTER_BY_PROVIDER,
    _reset_voice_adapter_registry,
    register_voice_adapter,
)
from voice.constants import CallDirection, CallStatus, HangupCause, VoiceProvider
from voice.fallback import (
    DEFAULT_FALLBACK_CAUSES,
    maybe_send_sms_fallback,
    render_template,
)
from voice.models import VoiceCall, VoiceProviderConfig
from voice.signals import call_completed


def _make_sms_app(tenant):
    return SMSApp.objects.create(
        tenant=tenant,
        provider="TWILIO",
        sender_id="+14155550999",
        provider_credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
    )


def _make_voice_config(tenant, sms_app=None, *, enabled=True, template="Hi {{first_name}}, missed call."):
    return VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Fb Twilio",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
        fallback_sms_enabled=enabled,
        fallback_sms_config=sms_app,
        fallback_sms_template=template,
        fallback_on_causes=list(DEFAULT_FALLBACK_CAUSES),
    )


def _make_call(
    tenant,
    cfg,
    contact,
    *,
    status=CallStatus.FAILED,
    hangup_cause=HangupCause.NO_ANSWER,
    duration=0,
    provider_call_id="CA_fb",
    broadcast=None,
):
    return VoiceCall.objects.create(
        tenant=tenant,
        name="fb-call",
        provider_config=cfg,
        provider_call_id=provider_call_id,
        direction=CallDirection.OUTBOUND,
        from_number="+14155550100",
        to_number="+14155550199",
        contact=contact,
        status=status,
        hangup_cause=hangup_cause,
        duration_seconds=duration,
        broadcast=broadcast,
    )


# ─────────────────────────────────────────────────────────────────────────────
# render_template
# ─────────────────────────────────────────────────────────────────────────────


class RenderTemplateTests(TestCase):
    def test_substitutes_placeholders(self):
        result = render_template(
            "Hi {{first_name}}, from {{ from_number }}.",
            {
                "first_name": "Riya",
                "from_number": "+14155550100",
            },
        )
        self.assertEqual(result, "Hi Riya, from +14155550100.")

    def test_missing_key_becomes_empty(self):
        result = render_template("Hi {{name}}!", {})
        self.assertEqual(result, "Hi !")

    def test_none_value_becomes_empty(self):
        result = render_template("X={{x}}", {"x": None})
        self.assertEqual(result, "X=")

    def test_empty_template_yields_empty(self):
        self.assertEqual(render_template("", {"a": "b"}), "")


# ─────────────────────────────────────────────────────────────────────────────
# maybe_send_sms_fallback
# ─────────────────────────────────────────────────────────────────────────────


class MaybeSendSmsFallbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="FB Tenant")
        cls.sms_app = _make_sms_app(cls.tenant)
        cls.contact = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+14155550199",
            first_name="Riya",
        )

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_dispatches_on_no_answer(self, mock_sender_cls):
        mock_sender = mock_sender_cls.return_value
        mock_sender.send_text.return_value = {"success": True, "message_id": "SM_1", "error": ""}

        cfg = _make_voice_config(self.tenant, self.sms_app)
        call = _make_call(self.tenant, cfg, self.contact)

        result = maybe_send_sms_fallback(call)

        self.assertTrue(result["attempted"])
        self.assertTrue(result["success"])
        mock_sender_cls.assert_called_once_with(self.sms_app)
        mock_sender.send_text.assert_called_once()
        args, _ = mock_sender.send_text.call_args
        self.assertEqual(args[0], "+14155550199")
        self.assertIn("Riya", args[1])

        # Idempotency stamp landed.
        call.refresh_from_db()
        self.assertTrue(call.metadata.get("sms_fallback_sent"))
        self.assertEqual(call.metadata["sms_fallback"]["sms_message_id"], "SM_1")

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_is_idempotent(self, mock_sender_cls):
        mock_sender_cls.return_value.send_text.return_value = {"success": True, "message_id": "SM_1"}

        cfg = _make_voice_config(self.tenant, self.sms_app)
        call = _make_call(self.tenant, cfg, self.contact)

        maybe_send_sms_fallback(call)
        result2 = maybe_send_sms_fallback(call)

        self.assertFalse(result2["attempted"])
        self.assertEqual(result2["skipped_reason"], "already_sent")
        # Sender invoked exactly once across both calls.
        self.assertEqual(mock_sender_cls.return_value.send_text.call_count, 1)

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_skips_when_call_succeeded(self, mock_sender_cls):
        cfg = _make_voice_config(self.tenant, self.sms_app)
        call = _make_call(
            self.tenant,
            cfg,
            self.contact,
            status=CallStatus.COMPLETED,
            hangup_cause=HangupCause.NORMAL_CLEARING,
            duration=42,
        )

        result = maybe_send_sms_fallback(call)
        self.assertFalse(result["attempted"])
        self.assertEqual(result["skipped_reason"], "call_succeeded")
        mock_sender_cls.assert_not_called()

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_skips_when_config_disabled(self, mock_sender_cls):
        cfg = _make_voice_config(self.tenant, self.sms_app, enabled=False)
        call = _make_call(self.tenant, cfg, self.contact)

        result = maybe_send_sms_fallback(call)
        self.assertFalse(result["attempted"])
        self.assertEqual(result["skipped_reason"], "fallback_disabled")
        mock_sender_cls.assert_not_called()

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_skips_when_no_sms_app_attached(self, mock_sender_cls):
        cfg = _make_voice_config(self.tenant, sms_app=None)
        call = _make_call(self.tenant, cfg, self.contact)

        result = maybe_send_sms_fallback(call)
        self.assertFalse(result["attempted"])
        self.assertEqual(result["skipped_reason"], "no_sms_app")
        mock_sender_cls.assert_not_called()

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_cause_filter_excludes_unconfigured_hangup(self, mock_sender_cls):
        cfg = _make_voice_config(self.tenant, self.sms_app)
        cfg.fallback_on_causes = ["USER_BUSY"]
        cfg.save(update_fields=["fallback_on_causes"])

        call = _make_call(self.tenant, cfg, self.contact, hangup_cause=HangupCause.NO_ANSWER)
        result = maybe_send_sms_fallback(call)
        self.assertFalse(result["attempted"])
        self.assertEqual(result["skipped_reason"], "cause_filtered")
        mock_sender_cls.assert_not_called()

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_empty_cause_list_disables_filter(self, mock_sender_cls):
        mock_sender_cls.return_value.send_text.return_value = {"success": True, "message_id": "SM_1"}
        cfg = _make_voice_config(self.tenant, self.sms_app)
        cfg.fallback_on_causes = []
        cfg.save(update_fields=["fallback_on_causes"])

        call = _make_call(self.tenant, cfg, self.contact, hangup_cause=HangupCause.NORMAL_CLEARING)
        result = maybe_send_sms_fallback(call)
        self.assertTrue(result["attempted"])
        mock_sender_cls.return_value.send_text.assert_called_once()

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_empty_template_body_skips(self, mock_sender_cls):
        cfg = _make_voice_config(self.tenant, self.sms_app, template="")
        call = _make_call(self.tenant, cfg, self.contact)

        result = maybe_send_sms_fallback(call)
        self.assertFalse(result["attempted"])
        self.assertEqual(result["skipped_reason"], "empty_body")
        mock_sender_cls.assert_not_called()

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_broken_sender_is_swallowed(self, mock_sender_cls):
        mock_sender_cls.side_effect = RuntimeError("SMS provider down")

        cfg = _make_voice_config(self.tenant, self.sms_app)
        call = _make_call(self.tenant, cfg, self.contact)

        # Must not raise.
        result = maybe_send_sms_fallback(call)

        self.assertTrue(result["attempted"])
        self.assertFalse(result["success"])
        # Stamp still applied so we don't retry forever on a known-broken provider.
        call.refresh_from_db()
        self.assertTrue(call.metadata.get("sms_fallback_sent"))


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast override semantics
# ─────────────────────────────────────────────────────────────────────────────


class BroadcastOverrideTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Override Tenant")
        cls.sms_app = _make_sms_app(cls.tenant)
        cls.contact = TenantContact.objects.create(tenant=cls.tenant, phone="+14155550199")

    def _make_broadcast(self, override):
        from broadcast.models import Broadcast, BroadcastPlatformChoices

        return Broadcast.objects.create(
            tenant=self.tenant,
            name="fb broadcast",
            platform=BroadcastPlatformChoices.VOICE,
            fallback_sms_enabled=override,
        )

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_broadcast_true_forces_send_even_when_config_off(self, mock_sender_cls):
        mock_sender_cls.return_value.send_text.return_value = {"success": True, "message_id": "SM_1"}

        cfg = _make_voice_config(self.tenant, self.sms_app, enabled=False)
        broadcast = self._make_broadcast(True)
        call = _make_call(self.tenant, cfg, self.contact, provider_call_id="CA_ov_t", broadcast=broadcast)

        result = maybe_send_sms_fallback(call)
        self.assertTrue(result["attempted"])

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_broadcast_false_disables_send_even_when_config_on(self, mock_sender_cls):
        cfg = _make_voice_config(self.tenant, self.sms_app, enabled=True)
        broadcast = self._make_broadcast(False)
        call = _make_call(self.tenant, cfg, self.contact, provider_call_id="CA_ov_f", broadcast=broadcast)

        result = maybe_send_sms_fallback(call)
        self.assertFalse(result["attempted"])
        self.assertEqual(result["skipped_reason"], "fallback_disabled")
        mock_sender_cls.assert_not_called()

    @patch("sms.services.message_sender.SMSMessageSender")
    def test_broadcast_none_inherits_config(self, mock_sender_cls):
        mock_sender_cls.return_value.send_text.return_value = {"success": True, "message_id": "SM_1"}

        cfg = _make_voice_config(self.tenant, self.sms_app, enabled=True)
        broadcast = self._make_broadcast(None)
        call = _make_call(self.tenant, cfg, self.contact, provider_call_id="CA_ov_n", broadcast=broadcast)

        result = maybe_send_sms_fallback(call)
        self.assertTrue(result["attempted"])


# ─────────────────────────────────────────────────────────────────────────────
# Signal wiring — call_completed → trigger_sms_fallback
# ─────────────────────────────────────────────────────────────────────────────


class SignalWiringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Signal FB Tenant")
        cls.sms_app = _make_sms_app(cls.tenant)
        cls.contact = TenantContact.objects.create(tenant=cls.tenant, phone="+14155550199")

    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    @patch("voice.signals.write_to_team_inbox")
    @patch("voice.signals.release_concurrency_semaphore")
    @patch("voice.billing.tasks.fetch_provider_cost.apply_async")
    @patch("voice.fallback.maybe_send_sms_fallback")
    def test_call_completed_invokes_fallback(self, mock_fb, *_):
        cfg = _make_voice_config(self.tenant, self.sms_app)
        call = _make_call(self.tenant, cfg, self.contact, provider_call_id="CA_sig_fb")

        call_completed.send(sender=VoiceCall, call=call)
        mock_fb.assert_called_once_with(call)
