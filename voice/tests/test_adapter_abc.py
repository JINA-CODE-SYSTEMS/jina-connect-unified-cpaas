"""Tests for VoiceAdapter ABC + dataclasses (#159).

Verifies:
  * Abstract methods enforce subclassing
  * platform + capabilities defaults are set correctly
  * Inherited text-channel methods raise NotImplementedError with a
    clear message (so callers can introspect and skip them)
  * Dataclasses are frozen + accept the expected fields
"""

from __future__ import annotations

import pytest
from django.test import TestCase

from jina_connect.platform_choices import PlatformChoices
from voice.adapters.base import (
    CallInstructions,
    NormalizedCallEvent,
    PlayInstruction,
    ProviderCallHandle,
    VoiceAdapter,
)
from voice.constants import CallEventType


class _FakeVoiceAdapter(VoiceAdapter):
    """Minimal concrete subclass — exists only to verify the ABC."""

    def initiate_call(self, *, from_number, to_number, callback_url, instructions):
        return ProviderCallHandle(provider_call_id="fake-1", raw={})

    def hangup(self, provider_call_id):
        pass

    def play(self, provider_call_id, *, audio_url=None, tts_text=None, tts_voice=None):
        pass

    def gather_dtmf(self, provider_call_id, *, max_digits, timeout_seconds, finish_on_key=None):
        pass

    def gather_speech(self, provider_call_id, *, language, timeout_seconds):
        pass

    def transfer(self, provider_call_id, *, to_uri):
        pass

    def start_recording(self, provider_call_id):
        return "rec-1"

    def stop_recording(self, provider_call_id, provider_recording_id):
        pass

    def fetch_recording(self, provider_recording_id):
        return b""

    def parse_webhook(self, request):
        return NormalizedCallEvent(
            provider_call_id="fake-1",
            event_type=CallEventType.INITIATED,
            hangup_cause=None,
            payload={},
        )


class VoiceAdapterABCTests(TestCase):
    def test_cannot_instantiate_abstract(self):
        """Instantiating VoiceAdapter directly must raise TypeError."""
        with pytest.raises(TypeError):
            VoiceAdapter()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self):
        """A subclass that implements every abstract method instantiates."""
        adapter = _FakeVoiceAdapter()
        self.assertIsInstance(adapter, VoiceAdapter)

    def test_platform_defaults_to_voice(self):
        self.assertEqual(VoiceAdapter.platform, PlatformChoices.VOICE)

    def test_capabilities_signal_voice_no_text(self):
        caps = VoiceAdapter.capabilities
        self.assertTrue(caps.supports_voice_call)
        self.assertFalse(caps.supports_text)

    def test_text_methods_raise_not_implemented(self):
        """send_text / send_media / send_keyboard inherited from
        BaseChannelAdapter raise on voice adapters."""
        adapter = _FakeVoiceAdapter()
        with pytest.raises(NotImplementedError):
            adapter.send_text("x", "y")
        with pytest.raises(NotImplementedError):
            adapter.send_media("x", "image", "url")
        with pytest.raises(NotImplementedError):
            adapter.send_keyboard("x", "y", [])

    def test_missing_abstract_method_blocks_instantiation(self):
        """A subclass that forgets an abstract method cannot be instantiated."""

        class _Bad(VoiceAdapter):
            # missing every abstract method
            pass

        with pytest.raises(TypeError):
            _Bad()  # type: ignore[abstract]


class DataclassTests(TestCase):
    def test_play_instruction_is_frozen(self):
        p = PlayInstruction(tts_text="hello")
        with pytest.raises(Exception):  # FrozenInstanceError subclass of Exception
            p.tts_text = "different"  # type: ignore[misc]

    def test_call_instructions_defaults_all_none(self):
        ci = CallInstructions()
        self.assertIsNone(ci.flow_id)
        self.assertIsNone(ci.static_play)
        self.assertIsNone(ci.forward_to)

    def test_provider_call_handle_round_trip(self):
        h = ProviderCallHandle(provider_call_id="CA123", raw={"sid": "CA123"})
        self.assertEqual(h.provider_call_id, "CA123")
        self.assertEqual(h.raw["sid"], "CA123")

    def test_normalized_event_required_fields(self):
        ev = NormalizedCallEvent(
            provider_call_id="CA123",
            event_type=CallEventType.ANSWERED,
            hangup_cause=None,
            payload={"k": "v"},
        )
        self.assertEqual(ev.event_type, CallEventType.ANSWERED)
        self.assertIsNone(ev.hangup_cause)
