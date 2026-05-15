"""Voice broadcast dispatcher tests (#162).

Covers:
  * ``handle_voice_message`` creates VoiceCall + queues initiate_call
  * Concurrency semaphore acquire / release
  * Status sync — VoiceCall terminal status updates BroadcastMessage
  * VOICE price returns Decimal("0") at broadcast creation
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from broadcast.models import (
    Broadcast,
    BroadcastMessage,
    BroadcastPlatformChoices,
    MessageStatusChoices,
)
from broadcast.tasks import handle_voice_message
from contacts.models import TenantContact
from tenants.models import Tenant, TenantVoiceApp
from voice.adapters.http_voice.twilio import TwilioVoiceAdapter
from voice.adapters.registry import (
    _ADAPTER_BY_PROVIDER,
    _reset_voice_adapter_registry,
    register_voice_adapter,
)
from voice.concurrency import _key as semaphore_key
from voice.constants import (
    CallDirection,
    CallStatus,
    HangupCause,
    VoiceProvider,
)
from voice.models import VoiceCall, VoiceProviderConfig, VoiceTemplate
from voice.signals import call_completed


def _make_setup(tenant_name="VB Tenant", from_numbers=None, max_concurrent=10):
    tenant = Tenant.objects.create(name=tenant_name)
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="VB Twilio",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
        from_numbers=from_numbers if from_numbers is not None else ["+14155550100"],
        max_concurrent_calls=max_concurrent,
    )
    TenantVoiceApp.objects.create(tenant=tenant, is_enabled=True, default_outbound_config=cfg)
    return tenant, cfg


def _make_broadcast(tenant, *, voice_template=None):
    bcast = Broadcast.objects.create(
        name="VB campaign",
        tenant=tenant,
        platform=BroadcastPlatformChoices.VOICE,
        voice_template=voice_template,
    )
    contact = TenantContact.objects.create(tenant=tenant, phone="+14155550199")
    # BroadcastMessage inherits its tenant via broadcast.tenant — no
    # direct ``tenant`` field on the model.
    msg = BroadcastMessage.objects.create(
        broadcast=bcast,
        contact=contact,
        status=MessageStatusChoices.QUEUED,
    )
    return bcast, msg


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────


class HandleVoiceMessageTests(TestCase):
    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    @patch("voice.tasks.initiate_call.delay")
    @patch("voice.concurrency._get_redis_client")
    def test_dispatch_creates_call_and_queues_initiate(self, mock_redis, mock_initiate):
        # First INCR returns 1 (semaphore claimed).
        mock_redis.return_value.incr.return_value = 1

        tenant, cfg = _make_setup()
        tpl = VoiceTemplate.objects.create(
            tenant=tenant,
            name="tpl",
            template_kind="tts_script",
            tts_text="Hello {{contact.name}}",
        )
        _, msg = _make_broadcast(tenant, voice_template=tpl)

        result = handle_voice_message(msg)

        self.assertTrue(result["success"], result.get("error"))
        # VoiceCall row created.
        call = VoiceCall.objects.get(broadcast__pk=msg.broadcast_id)
        self.assertEqual(call.direction, CallDirection.OUTBOUND)
        self.assertEqual(call.to_number, "+14155550199")
        self.assertEqual(call.metadata["broadcast_message_id"], msg.id)
        self.assertEqual(call.metadata["static_play"]["tts_text"], "Hello {{contact.name}}")
        # initiate_call queued.
        mock_initiate.assert_called_once_with(str(call.id))

    @patch("voice.tasks.initiate_call.delay")
    @patch("voice.concurrency._get_redis_client")
    def test_dispatch_uses_default_outbound_config_from_tenant(self, mock_redis, mock_initiate):
        mock_redis.return_value.incr.return_value = 1
        tenant, cfg = _make_setup()
        _, msg = _make_broadcast(tenant)
        result = handle_voice_message(msg)
        self.assertTrue(result["success"])
        call = VoiceCall.objects.get(broadcast__pk=msg.broadcast_id)
        self.assertEqual(call.provider_config_id, cfg.id)

    @patch("voice.tasks.initiate_call.delay")
    @patch("voice.concurrency._get_redis_client")
    def test_dispatch_falls_back_when_no_default_outbound(self, mock_redis, mock_initiate):
        mock_redis.return_value.incr.return_value = 1
        tenant, cfg = _make_setup(tenant_name="VB Tenant Fallback")
        # Clear the default — dispatcher should pick the highest-priority
        # enabled config instead.
        tenant.voice_app.default_outbound_config = None
        tenant.voice_app.save(update_fields=["default_outbound_config"])
        _, msg = _make_broadcast(tenant)
        result = handle_voice_message(msg)
        self.assertTrue(result["success"], result.get("error"))

    @patch("voice.concurrency._get_redis_client")
    def test_dispatch_fails_when_no_config(self, mock_redis):
        mock_redis.return_value.incr.return_value = 1
        tenant = Tenant.objects.create(name="No Config Tenant")
        _, msg = _make_broadcast(tenant)
        result = handle_voice_message(msg)
        self.assertFalse(result["success"])
        self.assertIn("No active VoiceProviderConfig", result["error"])

    @patch("voice.tasks.initiate_call.delay")
    @patch("voice.concurrency._get_redis_client")
    def test_dispatch_fails_when_config_has_no_from_numbers(self, mock_redis, mock_initiate):
        mock_redis.return_value.incr.return_value = 1
        tenant, cfg = _make_setup(tenant_name="No DID Tenant", from_numbers=[])
        _, msg = _make_broadcast(tenant)
        result = handle_voice_message(msg)
        self.assertFalse(result["success"])
        self.assertIn("from_numbers", result["error"])
        mock_initiate.assert_not_called()

    @patch("voice.tasks.initiate_call.delay")
    @patch("voice.concurrency._get_redis_client")
    def test_dispatch_blocked_by_concurrency_cap(self, mock_redis, mock_initiate):
        """When the semaphore is at the cap, dispatch returns success=False
        so the broadcast retry mechanism can pick it up later."""
        # INCR returns max+1 → over cap.
        mock_redis.return_value.incr.return_value = 11

        tenant, cfg = _make_setup(tenant_name="Cap Tenant", max_concurrent=10)
        _, msg = _make_broadcast(tenant)
        result = handle_voice_message(msg)
        self.assertFalse(result["success"])
        self.assertIn("concurrency cap reached", result["error"])
        # No VoiceCall created.
        self.assertEqual(VoiceCall.objects.filter(broadcast=msg.broadcast).count(), 0)
        mock_initiate.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Concurrency semaphore
# ─────────────────────────────────────────────────────────────────────────────


class ConcurrencySemaphoreTests(TestCase):
    def test_key_layout(self):
        self.assertEqual(semaphore_key("t1", "c1"), "voice:concurrency:t1:c1")

    @patch("voice.concurrency._get_redis_client")
    def test_acquire_under_cap_succeeds(self, mock_client):
        from voice.concurrency import acquire

        mock_client.return_value.incr.return_value = 1
        self.assertTrue(acquire("t1", "c1", 5))
        # First increment sets TTL.
        mock_client.return_value.expire.assert_called_once()

    @patch("voice.concurrency._get_redis_client")
    def test_acquire_at_cap_fails_and_rolls_back(self, mock_client):
        from voice.concurrency import acquire

        mock_client.return_value.incr.return_value = 6  # exceeds cap=5
        self.assertFalse(acquire("t1", "c1", 5))
        # Counter rolled back.
        mock_client.return_value.decr.assert_called_once()

    @patch("voice.concurrency._get_redis_client")
    def test_acquire_unlimited_when_max_zero(self, mock_client):
        from voice.concurrency import acquire

        self.assertTrue(acquire("t1", "c1", 0))
        mock_client.return_value.incr.assert_not_called()

    @patch("voice.concurrency._get_redis_client")
    def test_release_clamps_negative(self, mock_client):
        from voice.concurrency import release

        mock_client.return_value.decr.return_value = -1
        release("t1", "c1")
        mock_client.return_value.set.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Status sync — VoiceCall terminal → BroadcastMessage
# ─────────────────────────────────────────────────────────────────────────────


class BroadcastStatusSyncTests(TestCase):
    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    def _make_broadcast_call(self, *, status, duration, hangup_cause=""):
        tenant, cfg = _make_setup(tenant_name=f"Sync {status} Tenant")
        _, msg = _make_broadcast(tenant)
        call = VoiceCall.objects.create(
            tenant=tenant,
            name="sync-test",
            provider_config=cfg,
            provider_call_id=f"CA_{status}",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550199",
            broadcast=msg.broadcast,
            status=status,
            duration_seconds=duration,
            hangup_cause=hangup_cause,
            metadata={"broadcast_message_id": msg.id},
        )
        return msg, call

    @patch("voice.billing.tasks.fetch_provider_cost.apply_async")
    @patch("voice.concurrency.release")
    def test_completed_with_duration_marks_delivered(self, *_):
        msg, call = self._make_broadcast_call(
            status=CallStatus.COMPLETED,
            duration=42,
            hangup_cause=HangupCause.NORMAL_CLEARING,
        )
        call_completed.send(sender=VoiceCall, call=call)
        msg.refresh_from_db()
        self.assertEqual(msg.status, MessageStatusChoices.DELIVERED)

    @patch("voice.billing.tasks.fetch_provider_cost.apply_async")
    @patch("voice.concurrency.release")
    def test_completed_zero_duration_marks_failed(self, *_):
        msg, call = self._make_broadcast_call(
            status=CallStatus.COMPLETED, duration=0, hangup_cause=HangupCause.NO_ANSWER
        )
        call_completed.send(sender=VoiceCall, call=call)
        msg.refresh_from_db()
        self.assertEqual(msg.status, MessageStatusChoices.FAILED)

    @patch("voice.billing.tasks.fetch_provider_cost.apply_async")
    @patch("voice.concurrency.release")
    def test_failed_marks_failed(self, *_):
        msg, call = self._make_broadcast_call(status=CallStatus.FAILED, duration=0, hangup_cause=HangupCause.USER_BUSY)
        call_completed.send(sender=VoiceCall, call=call)
        msg.refresh_from_db()
        self.assertEqual(msg.status, MessageStatusChoices.FAILED)

    @patch("voice.billing.tasks.fetch_provider_cost.apply_async")
    @patch("voice.concurrency.release")
    def test_call_without_broadcast_is_no_op(self, *_):
        """Non-broadcast calls don't touch BroadcastMessage rows."""
        tenant, cfg = _make_setup(tenant_name="Standalone Tenant")
        call = VoiceCall.objects.create(
            tenant=tenant,
            name="standalone",
            provider_config=cfg,
            provider_call_id="CA_standalone",
            direction=CallDirection.OUTBOUND,
            from_number="+14155550100",
            to_number="+14155550299",
            status=CallStatus.COMPLETED,
            duration_seconds=42,
        )
        # Must not raise — no broadcast linkage means no BroadcastMessage
        # to update.
        call_completed.send(sender=VoiceCall, call=call)


# ─────────────────────────────────────────────────────────────────────────────
# Price
# ─────────────────────────────────────────────────────────────────────────────


class VoiceBroadcastPriceTests(TestCase):
    def test_voice_message_price_is_zero(self):
        tenant, _cfg = _make_setup(tenant_name="Price Tenant")
        bcast = Broadcast.objects.create(
            name="price-test",
            tenant=tenant,
            platform=BroadcastPlatformChoices.VOICE,
        )
        self.assertEqual(bcast.get_message_price(), Decimal("0"))
