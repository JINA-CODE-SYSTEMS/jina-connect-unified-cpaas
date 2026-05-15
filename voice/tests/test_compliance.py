"""Voice compliance tests (#171).

Covers:

  * ``time_of_day.is_within_allowed_hours`` — same-day window,
    wrap-around window, missing / malformed window, timezone resolve.
  * ``time_of_day.next_allowed_time`` — same-day vs next-day reschedule.
  * ``time_of_day.resolve_recipient_timezone`` — phonenumbers happy path
    + bad input fallback to UTC.
  * Broadcast dispatcher — out-of-window message gets rescheduled via
    ``process_broadcast_messages_batch.apply_async``; in-window proceeds.
  * ``consent.recording_allowed`` — tenant without app → True; tenant
    with consent off → True; tenant with consent on + missing row →
    False + logged; with row present → True.
  * ``voice.tasks.initiate_call`` stamps ``recording_allowed`` into
    metadata.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from contacts.models import TenantContact
from tenants.models import Tenant, TenantVoiceApp
from voice.compliance.consent import recording_allowed
from voice.compliance.time_of_day import (
    is_within_allowed_hours,
    next_allowed_time,
    resolve_recipient_timezone,
)
from voice.constants import CallDirection, CallStatus, VoiceProvider
from voice.models import RecordingConsent, VoiceCall, VoiceProviderConfig

KOLKATA = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
# is_within_allowed_hours
# ─────────────────────────────────────────────────────────────────────────────


class IsWithinAllowedHoursTests(TestCase):
    def test_inside_same_day_window(self):
        now = datetime(2025, 5, 1, 10, 0, tzinfo=KOLKATA)
        self.assertTrue(is_within_allowed_hours({"start": "09:00", "end": "21:00"}, KOLKATA, now=now))

    def test_below_window(self):
        now = datetime(2025, 5, 1, 8, 59, tzinfo=KOLKATA)
        self.assertFalse(is_within_allowed_hours({"start": "09:00", "end": "21:00"}, KOLKATA, now=now))

    def test_at_start_is_inside(self):
        now = datetime(2025, 5, 1, 9, 0, tzinfo=KOLKATA)
        self.assertTrue(is_within_allowed_hours({"start": "09:00", "end": "21:00"}, KOLKATA, now=now))

    def test_at_end_is_outside(self):
        # ``end`` is exclusive — 21:00 sharp is outside a 9-21 window.
        now = datetime(2025, 5, 1, 21, 0, tzinfo=KOLKATA)
        self.assertFalse(is_within_allowed_hours({"start": "09:00", "end": "21:00"}, KOLKATA, now=now))

    def test_wrap_around_window_after_midnight(self):
        # 02:00 inside a 22-06 window.
        now = datetime(2025, 5, 1, 2, 0, tzinfo=KOLKATA)
        self.assertTrue(is_within_allowed_hours({"start": "22:00", "end": "06:00"}, KOLKATA, now=now))

    def test_wrap_around_window_before_midnight(self):
        # 23:30 inside a 22-06 window.
        now = datetime(2025, 5, 1, 23, 30, tzinfo=KOLKATA)
        self.assertTrue(is_within_allowed_hours({"start": "22:00", "end": "06:00"}, KOLKATA, now=now))

    def test_wrap_around_window_outside(self):
        # 09:00 outside a 22-06 window.
        now = datetime(2025, 5, 1, 9, 0, tzinfo=KOLKATA)
        self.assertFalse(is_within_allowed_hours({"start": "22:00", "end": "06:00"}, KOLKATA, now=now))

    def test_no_window_means_no_gate(self):
        now = datetime(2025, 5, 1, 3, 0, tzinfo=KOLKATA)
        self.assertTrue(is_within_allowed_hours(None, KOLKATA, now=now))
        self.assertTrue(is_within_allowed_hours({}, KOLKATA, now=now))

    def test_malformed_window_means_no_gate(self):
        now = datetime(2025, 5, 1, 3, 0, tzinfo=KOLKATA)
        self.assertTrue(is_within_allowed_hours({"start": "9-AM"}, KOLKATA, now=now))
        self.assertTrue(is_within_allowed_hours({"start": "09:00"}, KOLKATA, now=now))

    def test_zero_length_window_matches_nothing(self):
        now = datetime(2025, 5, 1, 9, 0, tzinfo=KOLKATA)
        self.assertFalse(is_within_allowed_hours({"start": "09:00", "end": "09:00"}, KOLKATA, now=now))

    def test_timezone_string_is_accepted(self):
        now = datetime(2025, 5, 1, 10, 0, tzinfo=KOLKATA)
        self.assertTrue(is_within_allowed_hours({"start": "09:00", "end": "21:00"}, "Asia/Kolkata", now=now))

    def test_unknown_timezone_falls_back_to_utc(self):
        # 03:00 UTC is outside 9-21 — proves we fell back to UTC.
        now = datetime(2025, 5, 1, 3, 0, tzinfo=ZoneInfo("UTC"))
        self.assertFalse(is_within_allowed_hours({"start": "09:00", "end": "21:00"}, "Bogus/Zone", now=now))


# ─────────────────────────────────────────────────────────────────────────────
# next_allowed_time
# ─────────────────────────────────────────────────────────────────────────────


class NextAllowedTimeTests(TestCase):
    def test_returns_now_when_inside_window(self):
        now = datetime(2025, 5, 1, 10, 0, tzinfo=KOLKATA)
        result = next_allowed_time({"start": "09:00", "end": "21:00"}, KOLKATA, now=now)
        self.assertEqual(result, now)

    def test_reschedules_to_same_day_when_before_window(self):
        now = datetime(2025, 5, 1, 6, 0, tzinfo=KOLKATA)
        result = next_allowed_time({"start": "09:00", "end": "21:00"}, KOLKATA, now=now)
        self.assertEqual(result, datetime(2025, 5, 1, 9, 0, tzinfo=KOLKATA))

    def test_reschedules_to_next_day_when_after_window(self):
        now = datetime(2025, 5, 1, 22, 30, tzinfo=KOLKATA)
        result = next_allowed_time({"start": "09:00", "end": "21:00"}, KOLKATA, now=now)
        self.assertEqual(result, datetime(2025, 5, 2, 9, 0, tzinfo=KOLKATA))

    def test_no_window_returns_now(self):
        now = datetime(2025, 5, 1, 22, 30, tzinfo=KOLKATA)
        self.assertEqual(next_allowed_time(None, KOLKATA, now=now), now)


# ─────────────────────────────────────────────────────────────────────────────
# resolve_recipient_timezone
# ─────────────────────────────────────────────────────────────────────────────


class ResolveRecipientTimezoneTests(TestCase):
    def test_indian_number_resolves_to_india_tz(self):
        tz = resolve_recipient_timezone("+919999000111")
        # phonenumbers returns "Asia/Calcutta" — both names are valid IANA.
        self.assertIn(str(tz), {"Asia/Calcutta", "Asia/Kolkata"})

    def test_invalid_number_falls_back_to_utc(self):
        self.assertEqual(str(resolve_recipient_timezone("not-a-number")), "UTC")


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast dispatcher — time-of-day enforcement
# ─────────────────────────────────────────────────────────────────────────────


def _make_voice_provider_config(tenant):
    return VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Comp Twilio",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
        from_numbers=["+14155550100"],
    )


class DispatcherTimeOfDayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Comp Tenant")
        cls.cfg = _make_voice_provider_config(cls.tenant)
        cls.contact = TenantContact.objects.create(tenant=cls.tenant, phone="+919999000111")

    def _make_broadcast(self, allowed_hours_local=None):
        from broadcast.models import Broadcast, BroadcastMessage, BroadcastPlatformChoices, MessageStatusChoices

        broadcast = Broadcast.objects.create(
            tenant=self.tenant,
            name="comp broadcast",
            platform=BroadcastPlatformChoices.VOICE,
            allowed_hours_local=allowed_hours_local,
        )
        msg = BroadcastMessage.objects.create(
            name="comp msg",
            broadcast=broadcast,
            contact=self.contact,
            status=MessageStatusChoices.SENDING,
        )
        return msg

    @patch("broadcast.tasks.process_broadcast_messages_batch.apply_async")
    @patch("voice.tasks.initiate_call.delay")
    def test_out_of_window_reschedules_via_batch_task(self, mock_initiate, mock_apply):
        # The dispatcher imports compliance helpers inside the function —
        # patch the source module so the imports pick up the stubs.
        from voice.compliance import time_of_day as tod

        target_eta = datetime(2025, 5, 2, 9, 0, tzinfo=KOLKATA)
        with (
            patch.object(tod, "is_within_allowed_hours", return_value=False),
            patch.object(tod, "next_allowed_time", return_value=target_eta),
            patch.object(tod, "resolve_recipient_timezone", return_value=KOLKATA),
        ):
            from broadcast.tasks import handle_voice_message

            msg = self._make_broadcast(allowed_hours_local={"start": "09:00", "end": "21:00"})
            result = handle_voice_message(msg)

        self.assertTrue(result["success"])
        self.assertIn("rescheduled_for", result["response"])
        mock_initiate.assert_not_called()
        mock_apply.assert_called_once()
        eta = mock_apply.call_args.kwargs.get("eta")
        self.assertEqual(eta, target_eta)

    @patch("broadcast.tasks.process_broadcast_messages_batch.apply_async")
    @patch("voice.tasks.initiate_call.delay")
    def test_in_window_dispatches_immediately(self, mock_initiate, mock_apply):
        # No allowed_hours_local → in-window by default.
        msg = self._make_broadcast(allowed_hours_local=None)
        from broadcast.tasks import handle_voice_message

        with patch("voice.concurrency.acquire", return_value=True):
            result = handle_voice_message(msg)

        self.assertTrue(result["success"])
        self.assertNotIn("rescheduled_for", result.get("response", {}))
        mock_apply.assert_not_called()
        mock_initiate.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# recording_allowed
# ─────────────────────────────────────────────────────────────────────────────


class RecordingAllowedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Consent Tenant")
        cls.cfg = _make_voice_provider_config(cls.tenant)
        cls.contact = TenantContact.objects.create(tenant=cls.tenant, phone="+14155551111")

    def _make_call(self, contact=None):
        return VoiceCall.objects.create(
            tenant=self.tenant,
            name="cnst-call",
            provider_config=self.cfg,
            provider_call_id="CA_cnst",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155551111",
            contact=contact or self.contact,
            status=CallStatus.QUEUED,
        )

    def test_no_tenant_voice_app_allows(self):
        call = self._make_call()
        self.assertTrue(recording_allowed(call))

    def test_consent_disabled_allows(self):
        TenantVoiceApp.objects.create(tenant=self.tenant, is_enabled=True, recording_requires_consent=False)
        call = self._make_call()
        self.assertTrue(recording_allowed(call))

    def test_consent_required_without_row_denies(self):
        TenantVoiceApp.objects.create(tenant=self.tenant, is_enabled=True, recording_requires_consent=True)
        call = self._make_call()
        self.assertFalse(recording_allowed(call))

    def test_consent_required_with_row_allows(self):
        TenantVoiceApp.objects.create(tenant=self.tenant, is_enabled=True, recording_requires_consent=True)
        RecordingConsent.objects.create(
            tenant=self.tenant,
            name="cnst",
            contact=self.contact,
            consent_given=True,
            consent_method="web_form",
        )
        call = self._make_call()
        self.assertTrue(recording_allowed(call))

    def test_consent_row_with_false_flag_denies(self):
        TenantVoiceApp.objects.create(tenant=self.tenant, is_enabled=True, recording_requires_consent=True)
        RecordingConsent.objects.create(
            tenant=self.tenant,
            name="cnst-no",
            contact=self.contact,
            consent_given=False,
            consent_method="verbal_ivr",
        )
        call = self._make_call()
        self.assertFalse(recording_allowed(call))

    def test_no_contact_denies_when_consent_required(self):
        TenantVoiceApp.objects.create(tenant=self.tenant, is_enabled=True, recording_requires_consent=True)
        call = VoiceCall.objects.create(
            tenant=self.tenant,
            name="cnst-no-contact",
            provider_config=self.cfg,
            provider_call_id="CA_cnst_anon",
            direction=CallDirection.INBOUND,
            from_number="+14155557777",
            to_number="+14155550100",
            status=CallStatus.QUEUED,
        )
        self.assertFalse(recording_allowed(call))


# ─────────────────────────────────────────────────────────────────────────────
# initiate_call stamps the consent decision into metadata
# ─────────────────────────────────────────────────────────────────────────────


class InitiateCallStampsConsentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Stamp Tenant")
        cls.cfg = _make_voice_provider_config(cls.tenant)
        cls.contact = TenantContact.objects.create(tenant=cls.tenant, phone="+14155551111")

    @patch("voice.adapters.registry.get_voice_adapter_cls")
    def test_stamps_recording_allowed_into_metadata(self, mock_get_cls):
        from voice.adapters.base import ProviderCallHandle

        mock_adapter = mock_get_cls.return_value.return_value
        mock_adapter.initiate_call.return_value = ProviderCallHandle(provider_call_id="CA_stamp", raw={})

        TenantVoiceApp.objects.create(tenant=self.tenant, is_enabled=True, recording_requires_consent=True)

        call = VoiceCall.objects.create(
            tenant=self.tenant,
            name="stamp",
            provider_config=self.cfg,
            provider_call_id="pending",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155551111",
            contact=self.contact,
            status=CallStatus.QUEUED,
        )

        from voice.tasks import initiate_call

        initiate_call(str(call.id))

        call.refresh_from_db()
        self.assertEqual(call.metadata.get("recording_allowed"), False)
