"""Tests for the call-lifecycle signal fan-out + billing path (#160)."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from contacts.models import TenantContact
from team_inbox.models import MessagePlatformChoices, Messages
from tenants.models import Tenant
from voice.adapters.http_voice.twilio import TwilioVoiceAdapter
from voice.adapters.registry import (
    _ADAPTER_BY_PROVIDER,
    _reset_voice_adapter_registry,
    register_voice_adapter,
)
from voice.constants import (
    CallDirection,
    CallStatus,
    CostSource,
    HangupCause,
    VoiceProvider,
)
from voice.models import VoiceCall, VoiceProviderConfig
from voice.signals import call_completed


def _make_call(tenant, *, direction=CallDirection.OUTBOUND, duration=42, **extra):
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Signal Twilio",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
    )
    contact = TenantContact.objects.create(tenant=tenant, phone="+14155550199")
    return VoiceCall.objects.create(
        tenant=tenant,
        name="signal-test",
        provider_config=cfg,
        provider_call_id=extra.pop("provider_call_id", "CA_sig"),
        direction=direction,
        from_number="+14155550100",
        to_number="+14155550199",
        contact=contact,
        status=CallStatus.COMPLETED,
        duration_seconds=duration,
        hangup_cause=HangupCause.NORMAL_CLEARING,
        **extra,
    )


class TeamInboxSignalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Inbox Tenant")

    def setUp(self):
        # Register a fake Twilio so the billing-trigger handler doesn't
        # raise NotImplementedError and abort the signal chain.
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    @patch("voice.signals.trigger_provider_cost_billing")  # disable billing fan-out
    def test_completed_call_writes_messages_row(self, _no_billing):
        call = _make_call(self.tenant)
        with patch("voice.billing.tasks.fetch_provider_cost.apply_async"):
            call_completed.send(sender=VoiceCall, call=call)

        self.assertEqual(Messages.objects.filter(tenant=self.tenant).count(), 1)
        msg = Messages.objects.get(tenant=self.tenant)
        self.assertEqual(msg.platform, MessagePlatformChoices.VOICE)
        self.assertEqual(msg.direction, "OUTGOING")
        self.assertEqual(msg.author, "BOT")
        # body.text mentions the from/to numbers.
        self.assertIn("+14155550100", msg.content["body"]["text"])
        self.assertIn("+14155550199", msg.content["body"]["text"])
        # Call row now points at the inbox message.
        call.refresh_from_db()
        self.assertEqual(call.team_inbox_message_id, msg.id)

    @patch("voice.signals.trigger_provider_cost_billing")
    def test_inbound_call_uses_incoming_direction_and_contact_author(self, _):
        call = _make_call(self.tenant, direction=CallDirection.INBOUND)
        with patch("voice.billing.tasks.fetch_provider_cost.apply_async"):
            call_completed.send(sender=VoiceCall, call=call)

        msg = Messages.objects.get(tenant=self.tenant)
        self.assertEqual(msg.direction, "INCOMING")
        self.assertEqual(msg.author, "CONTACT")

    @patch("voice.signals.trigger_provider_cost_billing")
    def test_idempotent_when_called_twice(self, _):
        call = _make_call(self.tenant)
        with patch("voice.billing.tasks.fetch_provider_cost.apply_async"):
            call_completed.send(sender=VoiceCall, call=call)
            call.refresh_from_db()
            call_completed.send(sender=VoiceCall, call=call)
        # Only one inbox row created.
        self.assertEqual(Messages.objects.filter(tenant=self.tenant).count(), 1)


class ProviderCostTriggerTests(TestCase):
    """Signal fans out a delayed billing task only when the adapter
    declares ``supports_provider_cost``."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Cost Tenant")

    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    @patch("voice.signals.write_to_team_inbox")  # silence team_inbox signal
    @patch("voice.billing.tasks.fetch_provider_cost.apply_async")
    def test_schedules_delayed_billing_task(self, mock_apply, _):
        call = _make_call(self.tenant)
        call_completed.send(sender=VoiceCall, call=call)
        mock_apply.assert_called_once()
        kwargs = mock_apply.call_args.kwargs
        self.assertEqual(kwargs.get("countdown"), 30)
        self.assertEqual(kwargs.get("args"), [str(call.id)])


class FetchProviderCostTests(TestCase):
    """``fetch_provider_cost`` reads the provider's published price and
    writes a ``TenantTransaction``."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Fetch Tenant")

    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    @patch.object(TwilioVoiceAdapter, "_request")
    def test_writes_transaction_when_price_published(self, mock_request):
        # Twilio returns a negative price string; we store positive.
        resp = MagicMock()
        resp.json.return_value = {"price": "-0.01400", "price_unit": "USD"}
        resp.raise_for_status.return_value = None
        mock_request.return_value = resp

        call = _make_call(self.tenant)

        from transaction.models import TenantTransaction
        from voice.billing.tasks import fetch_provider_cost

        # Call the task synchronously by invoking the underlying function.
        fetch_provider_cost(str(call.id))

        call.refresh_from_db()
        self.assertEqual(call.cost_amount, Decimal("0.014000"))
        self.assertEqual(call.cost_currency, "USD")
        self.assertEqual(call.cost_source, CostSource.PROVIDER)
        self.assertEqual(TenantTransaction.objects.filter(tenant=self.tenant).count(), 1)

    @patch.object(TwilioVoiceAdapter, "_request")
    def test_no_double_billing(self, mock_request):
        """If the call already has cost_amount set, the task no-ops."""
        call = _make_call(self.tenant)
        call.cost_amount = Decimal("1.0")
        call.save(update_fields=["cost_amount", "updated_at"])

        from voice.billing.tasks import fetch_provider_cost

        fetch_provider_cost(str(call.id))
        # Adapter was never called.
        mock_request.assert_not_called()
