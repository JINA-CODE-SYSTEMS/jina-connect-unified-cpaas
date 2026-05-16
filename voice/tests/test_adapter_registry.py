"""Tests for the voice adapter registry + voice_adapter_factory (#159)."""

from __future__ import annotations

import pytest
from django.test import TestCase

from jina_connect.channel_registry import get_channel_adapter
from tenants.models import Tenant, TenantVoiceApp
from voice.adapters.base import (
    NormalizedCallEvent,
    ProviderCallHandle,
    VoiceAdapter,
)
from voice.adapters.registry import (
    _ADAPTER_BY_PROVIDER,
    _reset_voice_adapter_registry,
    get_voice_adapter_cls,
    register_voice_adapter,
    voice_adapter_factory,
)
from voice.constants import CallEventType, VoiceProvider
from voice.exceptions import VoiceError
from voice.models import VoiceProviderConfig

# Lightweight fakes — full adapter subclass shapes are tested in
# test_adapter_abc.py; here we only need ``something callable that's a
# VoiceAdapter subclass``.


def _make_fake_adapter(name: str) -> type[VoiceAdapter]:
    """Build a concrete VoiceAdapter subclass with a distinct __name__."""

    class _Fake(VoiceAdapter):
        def __init__(self, config=None):
            self.config = config

        def initiate_call(self, **k):
            return ProviderCallHandle(provider_call_id="x", raw={})

        def hangup(self, provider_call_id):
            pass

        def play(self, provider_call_id, **k):
            pass

        def gather_dtmf(self, provider_call_id, **k):
            pass

        def gather_speech(self, provider_call_id, **k):
            pass

        def transfer(self, provider_call_id, *, to_uri):
            pass

        def start_recording(self, provider_call_id):
            return ""

        def stop_recording(self, provider_call_id, provider_recording_id):
            pass

        def fetch_recording(self, provider_recording_id):
            return b""

        def parse_webhook(self, request):
            return NormalizedCallEvent(
                provider_call_id="x",
                event_type=CallEventType.INITIATED,
                hangup_cause=None,
                payload={},
            )

    _Fake.__name__ = name
    return _Fake


class RegistryTests(TestCase):
    """Pure unit tests — no DB use."""

    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    def test_register_and_lookup(self):
        FakeTwilio = _make_fake_adapter("FakeTwilio")
        register_voice_adapter("twilio", FakeTwilio)
        self.assertIs(get_voice_adapter_cls("twilio"), FakeTwilio)

    def test_register_same_pair_is_idempotent(self):
        FakeTwilio = _make_fake_adapter("FakeTwilio")
        register_voice_adapter("twilio", FakeTwilio)
        register_voice_adapter("twilio", FakeTwilio)  # must not raise
        self.assertIs(get_voice_adapter_cls("twilio"), FakeTwilio)

    def test_register_different_cls_raises(self):
        FakeA = _make_fake_adapter("FakeA")
        FakeB = _make_fake_adapter("FakeB")
        register_voice_adapter("twilio", FakeA)
        with pytest.raises(ValueError):
            register_voice_adapter("twilio", FakeB)

    def test_unknown_provider_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            get_voice_adapter_cls("plivo")


class VoiceAdapterFactoryTests(TestCase):
    """Resolves tenant → config → adapter via voice_adapter_factory."""

    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        self.FakeTwilio = _make_fake_adapter("FakeTwilio")
        register_voice_adapter(VoiceProvider.TWILIO.value, self.FakeTwilio)

        self.tenant = Tenant.objects.create(name="VAF Tenant")
        self.config = VoiceProviderConfig.objects.create(
            tenant=self.tenant,
            provider=VoiceProvider.TWILIO,
            vendor_label="VAF Twilio",
        )

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    def test_factory_returns_registered_adapter_instance(self):
        TenantVoiceApp.objects.create(
            tenant=self.tenant,
            is_enabled=True,
            default_outbound_config=self.config,
        )
        adapter = voice_adapter_factory(self.tenant)
        self.assertIsInstance(adapter, self.FakeTwilio)
        # Adapter received the config in __init__.
        self.assertIs(adapter.config, self.config)

    def test_factory_raises_when_voice_app_missing(self):
        with pytest.raises(VoiceError):
            voice_adapter_factory(self.tenant)

    def test_factory_raises_when_voice_disabled(self):
        TenantVoiceApp.objects.create(
            tenant=self.tenant,
            is_enabled=False,
            default_outbound_config=self.config,
        )
        with pytest.raises(VoiceError):
            voice_adapter_factory(self.tenant)

    def test_factory_raises_when_no_default_outbound(self):
        TenantVoiceApp.objects.create(
            tenant=self.tenant,
            is_enabled=True,
            default_outbound_config=None,
        )
        with pytest.raises(VoiceError):
            voice_adapter_factory(self.tenant)


class ChannelRegistryIntegrationTests(TestCase):
    """The channel registry's ``get_channel_adapter("VOICE", tenant)``
    should dispatch to ``voice_adapter_factory``."""

    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        self.FakeTwilio = _make_fake_adapter("FakeTwilio")
        register_voice_adapter(VoiceProvider.TWILIO.value, self.FakeTwilio)

        self.tenant = Tenant.objects.create(name="CR Tenant")
        self.config = VoiceProviderConfig.objects.create(
            tenant=self.tenant,
            provider=VoiceProvider.TWILIO,
        )
        TenantVoiceApp.objects.create(
            tenant=self.tenant,
            is_enabled=True,
            default_outbound_config=self.config,
        )

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    def test_get_channel_adapter_voice_returns_factory_result(self):
        adapter = get_channel_adapter("VOICE", self.tenant)
        self.assertIsInstance(adapter, self.FakeTwilio)
