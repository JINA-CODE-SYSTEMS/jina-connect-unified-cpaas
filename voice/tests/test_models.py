"""Voice model smoke tests (#158).

Confirms that:
  * Each model can be created and round-trips through the DB
  * Encrypted ``credentials`` survives save/refresh (encryption is
    handled by ``django-encrypted-model-fields``; this catches misuse)
  * The unique ``(provider_config, provider_call_id)`` constraint holds
  * ``TenantVoiceApp`` is created per tenant

Adapter logic, webhook flow, and IVR live in later PRs and are NOT
exercised here.

HOW TO RUN:
    DJANGO_SETTINGS_MODULE=jina_connect.settings \\
        python -m pytest voice/tests/test_models.py -v
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from tenants.models import Tenant, TenantVoiceApp
from voice.constants import (
    AudioFormat,
    CallDirection,
    CallEventType,
    CallStatus,
    CostSource,
    HangupCause,
    TemplateKind,
    VoiceProvider,
)
from voice.models import (
    VoiceCall,
    VoiceCallEvent,
    VoiceProviderConfig,
    VoiceRateCard,
    VoiceRecording,
    VoiceTemplate,
)

_PROVIDER_CREDENTIALS = {
    VoiceProvider.TWILIO: {"account_sid": "AC123", "auth_token": "secret"},
    VoiceProvider.PLIVO: {"auth_id": "MA123", "auth_token": "secret"},
    VoiceProvider.SIP: {
        "sip_username": "u",
        "sip_password": "p",
        "sip_realm": "sip.example.com",
        "sip_proxy": "sip.example.com",
    },
    VoiceProvider.EXOTEL: {"sid": "s", "api_key": "k", "api_token": "t"},
}


def _make_provider_config(tenant: Tenant, provider: str = VoiceProvider.TWILIO, **extra) -> VoiceProviderConfig:
    # Pre-save signal validates credentials per-provider (#179 review),
    # so the test fixture has to use a schema that matches.
    creds = _PROVIDER_CREDENTIALS.get(provider, _PROVIDER_CREDENTIALS[VoiceProvider.TWILIO])
    return VoiceProviderConfig.objects.create(
        tenant=tenant,
        provider=provider,
        vendor_label=extra.pop("vendor_label", "Test config"),
        credentials=json.dumps(creds),
        from_numbers=["+14155550100"],
        currency="USD",
        max_concurrent_calls=5,
        **extra,
    )


class VoiceProviderConfigTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="VC Tenant")

    def test_create_and_retrieve(self):
        cfg = _make_provider_config(self.tenant)
        self.assertIsNotNone(cfg.pk)
        self.assertEqual(cfg.from_numbers, ["+14155550100"])

    def test_credentials_round_trip(self):
        """EncryptedTextField persists and decrypts correctly."""
        cfg = _make_provider_config(self.tenant)
        cfg.refresh_from_db()
        # After refresh the field is back to its plaintext value.
        creds = json.loads(cfg.credentials)
        self.assertEqual(creds["account_sid"], "AC123")
        self.assertEqual(creds["auth_token"], "secret")


class VoiceCallTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="VCall Tenant")
        cls.config = _make_provider_config(cls.tenant)

    def test_create_inbound_call(self):
        call = VoiceCall.objects.create(
            tenant=self.tenant,
            provider_config=self.config,
            provider_call_id="CA_inbound_1",
            direction=CallDirection.INBOUND,
            from_number="+14155550199",
            to_number="+14155550100",
            status=CallStatus.IN_PROGRESS,
        )
        self.assertEqual(call.status, CallStatus.IN_PROGRESS)
        self.assertEqual(call.direction, CallDirection.INBOUND)

    def test_unique_provider_call_id_per_config(self):
        VoiceCall.objects.create(
            tenant=self.tenant,
            provider_config=self.config,
            provider_call_id="CA_unique",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550200",
            status=CallStatus.QUEUED,
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            VoiceCall.objects.create(
                tenant=self.tenant,
                provider_config=self.config,
                provider_call_id="CA_unique",  # duplicate
                direction=CallDirection.OUTBOUND,
                from_number="+14155550100",
                to_number="+14155550201",
                status=CallStatus.QUEUED,
            )

    def test_same_provider_call_id_allowed_on_different_config(self):
        other = _make_provider_config(self.tenant, vendor_label="Other")
        VoiceCall.objects.create(
            tenant=self.tenant,
            provider_config=self.config,
            provider_call_id="CA_dup_ok",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550200",
            status=CallStatus.QUEUED,
        )
        # Same provider_call_id but different config — must NOT collide.
        VoiceCall.objects.create(
            tenant=self.tenant,
            provider_config=other,
            provider_call_id="CA_dup_ok",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550200",
            status=CallStatus.QUEUED,
        )
        self.assertEqual(VoiceCall.objects.filter(provider_call_id="CA_dup_ok").count(), 2)

    def test_terminal_call_with_cost(self):
        call = VoiceCall.objects.create(
            tenant=self.tenant,
            provider_config=self.config,
            provider_call_id="CA_completed",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550299",
            status=CallStatus.COMPLETED,
            started_at=timezone.now() - timedelta(seconds=120),
            ended_at=timezone.now(),
            duration_seconds=120,
            hangup_cause=HangupCause.NORMAL_CLEARING,
            cost_amount="0.024000",
            cost_currency="USD",
            cost_source=CostSource.PROVIDER,
        )
        self.assertEqual(call.hangup_cause, HangupCause.NORMAL_CLEARING)
        self.assertEqual(call.cost_source, CostSource.PROVIDER)


class VoiceCallEventTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="VE Tenant")
        cls.config = _make_provider_config(cls.tenant)
        cls.call = VoiceCall.objects.create(
            tenant=cls.tenant,
            provider_config=cls.config,
            provider_call_id="CA_ev",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550200",
            status=CallStatus.IN_PROGRESS,
        )

    def test_append_only_log(self):
        now = timezone.now()
        for seq, et in enumerate(
            [
                CallEventType.INITIATED,
                CallEventType.RINGING,
                CallEventType.ANSWERED,
                CallEventType.COMPLETED,
            ],
            start=1,
        ):
            VoiceCallEvent.objects.create(
                call=self.call,
                event_type=et,
                payload={"k": seq},
                occurred_at=now + timedelta(seconds=seq),
                sequence=seq,
            )

        events = list(VoiceCallEvent.objects.filter(call=self.call).order_by("sequence"))
        self.assertEqual([e.sequence for e in events], [1, 2, 3, 4])
        self.assertEqual(events[0].event_type, CallEventType.INITIATED)
        self.assertEqual(events[-1].event_type, CallEventType.COMPLETED)


class VoiceTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="VT Tenant")

    def test_tts_template(self):
        tpl = VoiceTemplate.objects.create(
            tenant=self.tenant,
            name="welcome",
            template_kind=TemplateKind.TTS_SCRIPT,
            tts_text="Hello {{name}}, this is a test call.",
            tts_voice="Polly.Aditi",
            tts_language="en-IN",
        )
        self.assertIn("{{name}}", tpl.tts_text)

    def test_audio_url_template(self):
        tpl = VoiceTemplate.objects.create(
            tenant=self.tenant,
            name="recorded",
            template_kind=TemplateKind.AUDIO_URL,
            audio_url="https://example.com/audio.mp3",
            audio_format=AudioFormat.MP3,
        )
        self.assertEqual(tpl.audio_format, AudioFormat.MP3)


class VoiceRecordingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="VR Tenant")
        cls.config = _make_provider_config(cls.tenant)
        cls.call = VoiceCall.objects.create(
            tenant=cls.tenant,
            provider_config=cls.config,
            provider_call_id="CA_rec",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550200",
            status=CallStatus.COMPLETED,
        )

    def test_create_recording(self):
        rec = VoiceRecording.objects.create(
            call=self.call,
            provider_recording_id="REC_1",
            storage_url="tenants/abc/calls/x/REC_1.mp3",
            duration_seconds=42,
            size_bytes=512_000,
            format=AudioFormat.MP3,
            retention_expires_at=timezone.now() + timedelta(days=90),
        )
        self.assertEqual(rec.format, AudioFormat.MP3)


class VoiceRateCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="VRC Tenant")
        cls.config = _make_provider_config(cls.tenant, provider=VoiceProvider.SIP)

    def test_create_rate_card(self):
        VoiceRateCard.objects.create(
            provider_config=self.config,
            destination_prefix="+91",
            rate_per_minute="0.012000",
            currency="USD",
            billing_increment_seconds=60,
            valid_from=timezone.now(),
        )
        self.assertEqual(self.config.rate_cards.count(), 1)


class TenantVoiceAppTests(TestCase):
    def test_default_disabled(self):
        tenant = Tenant.objects.create(name="TVA Tenant")
        app = TenantVoiceApp.objects.create(tenant=tenant)
        self.assertFalse(app.is_enabled)
        self.assertEqual(app.recording_retention_days, 90)

    def test_one_to_one_with_tenant(self):
        tenant = Tenant.objects.create(name="TVA2 Tenant")
        TenantVoiceApp.objects.create(tenant=tenant)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            TenantVoiceApp.objects.create(tenant=tenant)  # duplicate
