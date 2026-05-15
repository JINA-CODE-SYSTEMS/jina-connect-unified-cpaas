"""Transcription tests (#169).

Covers:

  * The base registry (``register_transcription_backend`` /
    ``get_transcription_provider``) including idempotency and the
    "different class under same name" guard.
  * Deepgram backend — happy path, missing key, non-200, malformed
    payload.
  * Whisper backend — happy path, confidence-from-logprob calculation,
    error mapping.
  * Provider-native shim — raises ``TranscriptionError``.
  * ``transcribe_recording`` task — fetches audio, persists, idempotent,
    handles missing rows / unknown backends.
  * ``recording_created`` signal → ``transcribe_recording.delay`` plumbing.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from tenants.models import Tenant
from voice.constants import CallDirection, CallStatus, VoiceProvider
from voice.models import VoiceCall, VoiceProviderConfig, VoiceRecording
from voice.transcription import deepgram, provider_native, whisper
from voice.transcription.base import (
    _BACKENDS,
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionResult,
    _reset_transcription_backend_registry,
    get_transcription_provider,
    register_transcription_backend,
)


def _make_recording(tenant):
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="T Cfg",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
    )
    call = VoiceCall.objects.create(
        tenant=tenant,
        name="t-call",
        provider_config=cfg,
        provider_call_id="CA_trans_1",
        direction=CallDirection.OUTBOUND,
        from_number="+14155550100",
        to_number="+14155550200",
        status=CallStatus.COMPLETED,
        duration_seconds=10,
        metadata={"language": "en-IN"},
    )
    return VoiceRecording.objects.create(
        call=call,
        name="rec",
        provider_recording_id="RE_t1",
        storage_url="t/c/rec.mp3",
        duration_seconds=10,
        size_bytes=42,
        format="mp3",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────


class _Dummy(TranscriptionProvider):
    name = "dummy"

    def transcribe(self, audio_bytes, *, language=None):
        return TranscriptionResult(
            text="hi",
            language=language or "",
            confidence=1.0,
            provider=self.name,
            raw={},
        )


class _OtherDummy(TranscriptionProvider):
    name = "other"

    def transcribe(self, audio_bytes, *, language=None):
        return TranscriptionResult("ok", "", 0.5, "other", {})


class RegistryTests(TestCase):
    def setUp(self):
        self._snapshot = dict(_BACKENDS)

    def tearDown(self):
        _reset_transcription_backend_registry()
        _BACKENDS.update(self._snapshot)

    def test_register_and_get(self):
        _reset_transcription_backend_registry()
        register_transcription_backend("dummy", _Dummy)
        provider = get_transcription_provider("dummy")
        self.assertIsInstance(provider, _Dummy)

    def test_get_unknown_raises(self):
        _reset_transcription_backend_registry()
        with self.assertRaises(KeyError):
            get_transcription_provider("nope")

    def test_register_is_idempotent_for_same_class(self):
        _reset_transcription_backend_registry()
        register_transcription_backend("dummy", _Dummy)
        # Re-registering the same class should not raise.
        register_transcription_backend("dummy", _Dummy)
        self.assertIs(_BACKENDS["dummy"], _Dummy)

    def test_register_rejects_conflicting_class(self):
        _reset_transcription_backend_registry()
        register_transcription_backend("dummy", _Dummy)
        with self.assertRaises(ValueError):
            register_transcription_backend("dummy", _OtherDummy)

    def test_shipped_backends_self_register(self):
        """Importing the package wires deepgram / whisper / provider_native."""
        # Just touching the module loads it via the import at top of file.
        self.assertIn("deepgram", _BACKENDS)
        self.assertIn("whisper", _BACKENDS)
        self.assertIn("provider_native", _BACKENDS)


# ─────────────────────────────────────────────────────────────────────────────
# Deepgram
# ─────────────────────────────────────────────────────────────────────────────


class DeepgramTests(TestCase):
    @override_settings(DEEPGRAM_API_KEY="dg_key")
    @patch("voice.transcription.deepgram.requests.post")
    def test_happy_path_with_language(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "results": {
                    "channels": [
                        {
                            "detected_language": "en",
                            "alternatives": [{"transcript": "hello world", "confidence": 0.91}],
                        }
                    ]
                }
            },
        )

        result = deepgram.DeepgramProvider().transcribe(b"\x00\x01", language="en-IN")

        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.provider, "deepgram")
        self.assertAlmostEqual(result.confidence, 0.91, places=4)
        self.assertEqual(result.language, "en")

        kw = mock_post.call_args.kwargs
        self.assertEqual(kw["params"]["language"], "en-IN")
        self.assertNotIn("detect_language", kw["params"])
        self.assertIn("Authorization", kw["headers"])
        self.assertEqual(kw["headers"]["Authorization"], "Token dg_key")

    @override_settings(DEEPGRAM_API_KEY="dg_key")
    @patch("voice.transcription.deepgram.requests.post")
    def test_no_language_requests_detection(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "results": {
                    "channels": [
                        {
                            "alternatives": [{"transcript": "x", "confidence": 0.5}],
                        }
                    ]
                }
            },
        )
        deepgram.DeepgramProvider().transcribe(b"x")
        kw = mock_post.call_args.kwargs
        self.assertEqual(kw["params"]["detect_language"], "true")

    @override_settings(DEEPGRAM_API_KEY="")
    def test_missing_key_raises(self):
        with self.assertRaises(TranscriptionError):
            deepgram.DeepgramProvider().transcribe(b"x")

    @override_settings(DEEPGRAM_API_KEY="dg_key")
    @patch("voice.transcription.deepgram.requests.post")
    def test_non_200_raises(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text="boom")
        with self.assertRaises(TranscriptionError):
            deepgram.DeepgramProvider().transcribe(b"x")

    @override_settings(DEEPGRAM_API_KEY="dg_key")
    @patch("voice.transcription.deepgram.requests.post")
    def test_malformed_payload_raises(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"results": {}})
        with self.assertRaises(TranscriptionError):
            deepgram.DeepgramProvider().transcribe(b"x")

    @override_settings(DEEPGRAM_API_KEY="dg_key")
    @patch("voice.transcription.deepgram.requests.post")
    def test_request_exception_raises_transcription_error(self, mock_post):
        import requests

        mock_post.side_effect = requests.ConnectionError("down")
        with self.assertRaises(TranscriptionError):
            deepgram.DeepgramProvider().transcribe(b"x")


# ─────────────────────────────────────────────────────────────────────────────
# Whisper
# ─────────────────────────────────────────────────────────────────────────────


class WhisperTests(TestCase):
    @override_settings(OPENAI_API_KEY="sk-key")
    @patch("voice.transcription.whisper.requests.post")
    def test_happy_path_with_language_and_confidence(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "text": "namaste",
                "language": "hi",
                "segments": [
                    {"avg_logprob": -0.2},
                    {"avg_logprob": -0.4},
                ],
            },
        )

        result = whisper.WhisperProvider().transcribe(b"\x00", language="hi-IN")

        self.assertEqual(result.text, "namaste")
        self.assertEqual(result.language, "hi")
        self.assertEqual(result.provider, "whisper")
        # exp((-0.2 + -0.4) / 2) ≈ exp(-0.3) ≈ 0.7408
        self.assertGreater(result.confidence, 0.7)
        self.assertLess(result.confidence, 0.8)

        kw = mock_post.call_args.kwargs
        self.assertEqual(kw["headers"]["Authorization"], "Bearer sk-key")
        # Language hint normalised to ISO-639-1.
        self.assertEqual(kw["data"]["language"], "hi")
        self.assertEqual(kw["data"]["model"], "whisper-1")
        self.assertEqual(kw["data"]["response_format"], "verbose_json")

    @override_settings(OPENAI_API_KEY="sk-key")
    @patch("voice.transcription.whisper.requests.post")
    def test_no_segments_yields_zero_confidence(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"text": "hi", "language": "en", "segments": []},
        )
        result = whisper.WhisperProvider().transcribe(b"x")
        self.assertEqual(result.confidence, 0.0)

    @override_settings(OPENAI_API_KEY="")
    def test_missing_key_raises(self):
        with self.assertRaises(TranscriptionError):
            whisper.WhisperProvider().transcribe(b"x")

    @override_settings(OPENAI_API_KEY="sk-key")
    @patch("voice.transcription.whisper.requests.post")
    def test_non_200_raises(self, mock_post):
        mock_post.return_value = MagicMock(status_code=401, text="bad key")
        with self.assertRaises(TranscriptionError):
            whisper.WhisperProvider().transcribe(b"x")

    def test_logprob_helper_handles_garbage_segments(self):
        # Non-dict / missing avg_logprob entries are skipped, not crashes.
        result = whisper._avg_logprob_to_confidence([{"foo": "bar"}, "not a dict", {"avg_logprob": -0.1}])
        self.assertGreater(result, 0.8)


# ─────────────────────────────────────────────────────────────────────────────
# Provider-native shim
# ─────────────────────────────────────────────────────────────────────────────


class ProviderNativeTests(TestCase):
    def test_transcribe_raises(self):
        with self.assertRaises(TranscriptionError):
            provider_native.ProviderNativeProvider().transcribe(b"x")


# ─────────────────────────────────────────────────────────────────────────────
# transcribe_recording task
# ─────────────────────────────────────────────────────────────────────────────


class TranscribeRecordingTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Trans Tenant")

    def setUp(self):
        self._snapshot = dict(_BACKENDS)

    def tearDown(self):
        _reset_transcription_backend_registry()
        _BACKENDS.update(self._snapshot)

    @override_settings(VOICE_TRANSCRIPTION_PROVIDER="dummy")
    @patch("voice.transcription.tasks.storage.fetch")
    def test_persists_transcript_using_configured_backend(self, mock_fetch):
        from voice.transcription import tasks

        _reset_transcription_backend_registry()
        register_transcription_backend("dummy", _Dummy)
        mock_fetch.return_value = b"audio"

        rec = _make_recording(self.tenant)
        tasks.transcribe_recording(str(rec.id), language="en-IN")

        rec.refresh_from_db()
        self.assertEqual(rec.transcription, "hi")
        self.assertEqual(rec.transcription_provider, "dummy")
        self.assertAlmostEqual(rec.transcription_confidence, 1.0, places=4)
        mock_fetch.assert_called_once_with(rec.storage_url)

    @override_settings(VOICE_TRANSCRIPTION_PROVIDER="dummy")
    @patch("voice.transcription.tasks.storage.fetch")
    def test_no_op_when_transcription_already_present(self, mock_fetch):
        from voice.transcription import tasks

        _reset_transcription_backend_registry()
        register_transcription_backend("dummy", _Dummy)

        rec = _make_recording(self.tenant)
        rec.transcription = "already there"
        rec.transcription_provider = "hand-edit"
        rec.save(update_fields=["transcription", "transcription_provider"])

        tasks.transcribe_recording(str(rec.id))

        rec.refresh_from_db()
        self.assertEqual(rec.transcription, "already there")
        self.assertEqual(rec.transcription_provider, "hand-edit")
        mock_fetch.assert_not_called()

    @override_settings(VOICE_TRANSCRIPTION_PROVIDER="nope")
    @patch("voice.transcription.tasks.storage.fetch")
    def test_unknown_backend_is_logged_not_raised(self, mock_fetch):
        from voice.transcription import tasks

        rec = _make_recording(self.tenant)
        tasks.transcribe_recording(str(rec.id))

        rec.refresh_from_db()
        self.assertEqual(rec.transcription, "")
        mock_fetch.assert_not_called()

    @override_settings(VOICE_TRANSCRIPTION_PROVIDER="dummy")
    def test_missing_recording_is_logged_not_raised(self):
        import uuid

        from voice.transcription import tasks

        # Must not raise.
        tasks.transcribe_recording(str(uuid.uuid4()))

    @override_settings(VOICE_TRANSCRIPTION_PROVIDER="dummy")
    @patch("voice.transcription.tasks.storage.fetch")
    def test_backend_failure_does_not_corrupt_row(self, mock_fetch):
        from voice.transcription import tasks

        class _Boom(TranscriptionProvider):
            name = "dummy"

            def transcribe(self, audio_bytes, *, language=None):
                raise TranscriptionError("nope")

        _reset_transcription_backend_registry()
        register_transcription_backend("dummy", _Boom)
        mock_fetch.return_value = b"x"

        rec = _make_recording(self.tenant)
        tasks.transcribe_recording(str(rec.id))

        rec.refresh_from_db()
        self.assertEqual(rec.transcription, "")
        self.assertEqual(rec.transcription_provider, "")


# ─────────────────────────────────────────────────────────────────────────────
# recording_created signal → transcribe_recording.delay
# ─────────────────────────────────────────────────────────────────────────────


class RecordingCreatedSignalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Sig Tenant")

    @override_settings(VOICE_TRANSCRIPTION_PROVIDER="deepgram")
    @patch("voice.transcription.tasks.transcribe_recording.delay")
    def test_signal_queues_task(self, mock_delay):
        from voice.models import VoiceRecording
        from voice.recordings.tasks import recording_created

        rec = _make_recording(self.tenant)
        recording_created.send(sender=VoiceRecording, recording=rec, audio_bytes=b"x")

        mock_delay.assert_called_once_with(str(rec.id), "en-IN")

    @override_settings(VOICE_TRANSCRIPTION_PROVIDER="")
    @patch("voice.transcription.tasks.transcribe_recording.delay")
    def test_signal_skips_when_disabled(self, mock_delay):
        from voice.models import VoiceRecording
        from voice.recordings.tasks import recording_created

        rec = _make_recording(self.tenant)
        recording_created.send(sender=VoiceRecording, recording=rec, audio_bytes=b"x")

        mock_delay.assert_not_called()

    @override_settings(VOICE_TRANSCRIPTION_PROVIDER="deepgram")
    @patch("voice.transcription.tasks.transcribe_recording.delay")
    def test_signal_skips_when_transcription_already_present(self, mock_delay):
        from voice.models import VoiceRecording
        from voice.recordings.tasks import recording_created

        rec = _make_recording(self.tenant)
        rec.transcription = "pre-existing"
        rec.save(update_fields=["transcription"])

        recording_created.send(sender=VoiceRecording, recording=rec, audio_bytes=b"x")

        mock_delay.assert_not_called()
