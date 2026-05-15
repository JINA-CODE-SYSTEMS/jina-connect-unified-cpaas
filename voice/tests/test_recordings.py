"""Recording storage + download + retention tests (#161)."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from tenants.models import Tenant, TenantVoiceApp
from voice.adapters.http_voice.twilio import TwilioVoiceAdapter
from voice.adapters.registry import (
    _ADAPTER_BY_PROVIDER,
    _reset_voice_adapter_registry,
    register_voice_adapter,
)
from voice.constants import CallDirection, CallStatus, VoiceProvider
from voice.models import VoiceCall, VoiceProviderConfig, VoiceRecording
from voice.recordings import storage, tasks


def _make_call(tenant, *, provider_call_id="CA_rec_test", duration=42):
    cfg = VoiceProviderConfig.objects.create(
        tenant=tenant,
        name="Rec Twilio",
        provider=VoiceProvider.TWILIO,
        credentials=json.dumps({"account_sid": "AC1", "auth_token": "t"}),
    )
    return VoiceCall.objects.create(
        tenant=tenant,
        name="rec-test",
        provider_config=cfg,
        provider_call_id=provider_call_id,
        direction=CallDirection.OUTBOUND,
        from_number="+14155550100",
        to_number="+14155550200",
        status=CallStatus.COMPLETED,
        duration_seconds=duration,
    )


# ─────────────────────────────────────────────────────────────────────────────
# storage.py — pure boto3 wrappers, fully mocked
# ─────────────────────────────────────────────────────────────────────────────


class StorageTests(TestCase):
    def test_make_storage_key_layout(self):
        key = storage.make_storage_key("t1", "c1", "rec1", "mp3")
        self.assertEqual(key, "t1/c1/rec1.mp3")

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="voice-bkt")
    @patch("voice.recordings.storage._client")
    def test_upload_calls_put_object(self, mock_client):
        s3 = mock_client.return_value
        key = storage.upload("t1/c1/rec1.mp3", b"\x00\x01", "audio/mpeg")
        self.assertEqual(key, "t1/c1/rec1.mp3")
        s3.put_object.assert_called_once()
        kw = s3.put_object.call_args.kwargs
        self.assertEqual(kw["Bucket"], "voice-bkt")
        self.assertEqual(kw["Key"], "t1/c1/rec1.mp3")
        self.assertEqual(kw["Body"], b"\x00\x01")
        self.assertEqual(kw["ContentType"], "audio/mpeg")

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="voice-bkt")
    @patch("voice.recordings.storage._client")
    def test_signed_url_uses_default_ttl(self, mock_client):
        s3 = mock_client.return_value
        s3.generate_presigned_url.return_value = "https://example/signed"
        url = storage.signed_url("t1/c1/rec1.mp3")
        self.assertEqual(url, "https://example/signed")
        kw = s3.generate_presigned_url.call_args.kwargs
        self.assertEqual(kw["ExpiresIn"], storage.DEFAULT_SIGNED_URL_EXPIRES_SECONDS)

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="voice-bkt")
    @patch("voice.recordings.storage._client")
    def test_signed_url_custom_ttl(self, mock_client):
        s3 = mock_client.return_value
        storage.signed_url("k", expires_seconds=60)
        kw = s3.generate_presigned_url.call_args.kwargs
        self.assertEqual(kw["ExpiresIn"], 60)

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="voice-bkt")
    @patch("voice.recordings.storage._client")
    def test_delete_calls_delete_object(self, mock_client):
        s3 = mock_client.return_value
        storage.delete("t1/c1/rec1.mp3")
        s3.delete_object.assert_called_once_with(Bucket="voice-bkt", Key="t1/c1/rec1.mp3")

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="voice-bkt")
    @patch("voice.recordings.storage._client")
    def test_delete_swallows_errors(self, mock_client):
        s3 = mock_client.return_value
        s3.delete_object.side_effect = RuntimeError("S3 boom")
        # Must not raise — retention sweep needs to keep going.
        storage.delete("k")

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="", AWS_STORAGE_BUCKET_NAME="")
    def test_bucket_unconfigured_raises(self):
        with self.assertRaises(ValueError):
            storage._bucket()

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="", AWS_STORAGE_BUCKET_NAME="fallback")
    def test_bucket_falls_back_to_media_bucket(self):
        self.assertEqual(storage._bucket(), "fallback")


# ─────────────────────────────────────────────────────────────────────────────
# download_recording task
# ─────────────────────────────────────────────────────────────────────────────


class DownloadRecordingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Rec DL Tenant")
        TenantVoiceApp.objects.create(tenant=cls.tenant, is_enabled=True, recording_retention_days=30)

    def setUp(self):
        self._snapshot = dict(_ADAPTER_BY_PROVIDER)
        _reset_voice_adapter_registry()
        register_voice_adapter(VoiceProvider.TWILIO.value, TwilioVoiceAdapter)

    def tearDown(self):
        _reset_voice_adapter_registry()
        _ADAPTER_BY_PROVIDER.update(self._snapshot)

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="voice-bkt")
    @patch("voice.recordings.tasks.storage")
    @patch.object(TwilioVoiceAdapter, "fetch_recording")
    def test_creates_recording_row_and_uploads(self, mock_fetch, mock_storage):
        mock_fetch.return_value = b"audio-bytes"
        mock_storage.make_storage_key.side_effect = storage.make_storage_key

        call = _make_call(self.tenant)
        tasks.download_recording(str(call.id), "RE_1")

        rec = VoiceRecording.objects.get(call=call)
        self.assertEqual(rec.provider_recording_id, "RE_1")
        self.assertEqual(rec.size_bytes, len(b"audio-bytes"))
        self.assertEqual(rec.format, "mp3")
        # Storage path includes tenant + call + recording id.
        self.assertIn(str(self.tenant.id), rec.storage_url)
        self.assertIn(str(call.id), rec.storage_url)
        self.assertIn("RE_1", rec.storage_url)
        # Call row mirrors the recording url.
        call.refresh_from_db()
        self.assertEqual(call.recording_url, rec.storage_url)
        # Retention window respects TenantVoiceApp.recording_retention_days.
        delta = rec.retention_expires_at - timezone.now()
        # Allow a tiny clock skew.
        self.assertGreater(delta.total_seconds(), 30 * 86400 - 60)
        self.assertLess(delta.total_seconds(), 30 * 86400 + 60)
        # Storage upload was called once.
        mock_storage.upload.assert_called_once()

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="voice-bkt")
    @patch("voice.recordings.tasks.storage")
    @patch.object(TwilioVoiceAdapter, "fetch_recording")
    def test_idempotent_when_recording_already_exists(self, mock_fetch, mock_storage):
        mock_fetch.return_value = b"x"
        call = _make_call(self.tenant)
        VoiceRecording.objects.create(
            call=call,
            name="existing",
            provider_recording_id="RE_dup",
            storage_url="existing/path.mp3",
            duration_seconds=10,
            size_bytes=1,
            format="mp3",
        )
        tasks.download_recording(str(call.id), "RE_dup")
        # No second row.
        self.assertEqual(VoiceRecording.objects.filter(call=call).count(), 1)
        # Adapter never called.
        mock_fetch.assert_not_called()
        mock_storage.upload.assert_not_called()

    @override_settings(VOICE_RECORDING_STORAGE_BUCKET="voice-bkt")
    @patch("voice.recordings.tasks.storage")
    @patch.object(TwilioVoiceAdapter, "fetch_recording")
    def test_fires_recording_created_signal(self, mock_fetch, mock_storage):
        mock_fetch.return_value = b"abc"
        # make_storage_key must return a real string — the DB insert
        # otherwise sees a MagicMock and rejects it.
        mock_storage.make_storage_key.side_effect = storage.make_storage_key

        captured = {}

        def listener(sender, recording, audio_bytes, **kw):
            captured["recording_id"] = recording.id
            captured["audio_len"] = len(audio_bytes)

        tasks.recording_created.connect(listener)
        try:
            call = _make_call(self.tenant)
            tasks.download_recording(str(call.id), "RE_sig")
            rec = VoiceRecording.objects.get(call=call)
            self.assertEqual(captured["recording_id"], rec.id)
            self.assertEqual(captured["audio_len"], 3)
        finally:
            tasks.recording_created.disconnect(listener)

    def test_missing_call_is_logged_not_raised(self):
        """Unknown call_id → task no-ops cleanly (no retry storm)."""
        import uuid

        # Must not raise.
        tasks.download_recording(str(uuid.uuid4()), "RE_x")
        # No recording created.
        self.assertEqual(VoiceRecording.objects.count(), 0)


# ─────────────────────────────────────────────────────────────────────────────
# enforce_retention sweep
# ─────────────────────────────────────────────────────────────────────────────


class EnforceRetentionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Retention Tenant")

    @patch("voice.recordings.tasks.storage")
    def test_deletes_only_expired(self, mock_storage):
        call = _make_call(self.tenant, provider_call_id="CA_retn")
        # Expired
        expired = VoiceRecording.objects.create(
            call=call,
            name="expired",
            provider_recording_id="RE_old",
            storage_url="t1/c1/old.mp3",
            duration_seconds=10,
            size_bytes=1,
            format="mp3",
            retention_expires_at=timezone.now() - timedelta(days=1),
        )
        # Still in window
        fresh = VoiceRecording.objects.create(
            call=call,
            name="fresh",
            provider_recording_id="RE_new",
            storage_url="t1/c1/new.mp3",
            duration_seconds=10,
            size_bytes=1,
            format="mp3",
            retention_expires_at=timezone.now() + timedelta(days=30),
        )

        deleted = tasks.enforce_retention()
        self.assertEqual(deleted, 1)

        self.assertFalse(VoiceRecording.objects.filter(pk=expired.pk).exists())
        self.assertTrue(VoiceRecording.objects.filter(pk=fresh.pk).exists())
        # S3 delete only called for the expired one.
        mock_storage.delete.assert_called_once_with("t1/c1/old.mp3")

    @patch("voice.recordings.tasks.storage")
    def test_skips_recordings_without_storage_url(self, mock_storage):
        """Defensive: rows without storage_url shouldn't crash the sweep."""
        call = _make_call(self.tenant, provider_call_id="CA_no_url")
        VoiceRecording.objects.create(
            call=call,
            name="urlless",
            provider_recording_id="RE_no_url",
            storage_url="",
            duration_seconds=10,
            size_bytes=1,
            format="mp3",
            retention_expires_at=timezone.now() - timedelta(days=1),
        )
        deleted = tasks.enforce_retention()
        self.assertEqual(deleted, 1)
        mock_storage.delete.assert_not_called()

    @patch("voice.recordings.tasks.storage")
    def test_returns_zero_when_nothing_expired(self, mock_storage):
        deleted = tasks.enforce_retention()
        self.assertEqual(deleted, 0)
        mock_storage.delete.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Twilio recording-status webhook wiring
# ─────────────────────────────────────────────────────────────────────────────


class RecordingWebhookIntegrationTests(TestCase):
    """The Twilio recording-status webhook queues ``download_recording``
    on ``RecordingStatus=completed``."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="WH Rec Tenant")
        cls.auth_token = "rec_token"
        cls.config = VoiceProviderConfig.objects.create(
            tenant=cls.tenant,
            name="WH Rec Twilio",
            provider=VoiceProvider.TWILIO,
            credentials=json.dumps({"account_sid": "AC1", "auth_token": cls.auth_token}),
        )

    def _signed_request(self, path, post):
        import base64
        import hashlib
        import hmac

        url = f"http://testserver{path}"
        params = sorted(post.items())
        data = url + "".join(k + v for k, v in params)
        sig = base64.b64encode(hmac.new(self.auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()
        factory = RequestFactory()
        request = factory.post(path, data=post)
        request.META["HTTP_X_TWILIO_SIGNATURE"] = sig
        return request

    @patch("voice.recordings.tasks.download_recording.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_completed_status_queues_download(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True

        call = _make_call(self.tenant, provider_call_id="CA_wh_rec")
        path = f"/voice/v1/webhooks/twilio/{self.config.id}/recording-status/"
        post = {
            "RecordingSid": "RE_wh_1",
            "RecordingStatus": "completed",
            "CallSid": "CA_wh_rec",
        }
        request = self._signed_request(path, post)

        from voice.webhooks.twilio import TwilioRecordingStatusHandler

        view = TwilioRecordingStatusHandler.as_view()
        resp = view(request, config_uuid=str(self.config.id))

        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once_with(str(call.id), "RE_wh_1")

    @patch("voice.recordings.tasks.download_recording.delay")
    @patch("abstract.webhooks._get_redis_client")
    def test_in_progress_status_does_not_queue(self, mock_redis, mock_delay):
        mock_redis.return_value.set.return_value = True

        path = f"/voice/v1/webhooks/twilio/{self.config.id}/recording-status/"
        post = {
            "RecordingSid": "RE_wh_2",
            "RecordingStatus": "in-progress",
            "CallSid": "CA_wh_rec",
        }
        request = self._signed_request(path, post)

        from voice.webhooks.twilio import TwilioRecordingStatusHandler

        view = TwilioRecordingStatusHandler.as_view()
        resp = view(request, config_uuid=str(self.config.id))

        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_not_called()
