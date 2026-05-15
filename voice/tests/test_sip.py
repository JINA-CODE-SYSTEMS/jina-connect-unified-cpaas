"""SIP adapter + Asterisk ARI integration tests (#163).

Covers:
  * All 12 PJSIP vendor profiles load + have required keys
  * pjsip_writer renders endpoint/auth/aor/registration with substitutions
  * AriClient hits the right URLs + raises AriError on non-2xx
  * ari_consumer.translate_event maps ARI events to NormalizedCallEvent shape
  * ARI command dialect emitters for each voice node type
  * SIPVoiceAdapter calls ARI for initiate/play/hangup/record/transfer
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from voice.adapters.sip import SIPVoiceAdapter
from voice.constants import CallEventType, VoiceProvider
from voice.ivr.dialects import ari_commands
from voice.sip_config import pjsip_writer, templates
from voice.sip_config.ari_client import AriClient, AriError
from voice.sip_config.ari_consumer import _Q850, translate_event

# ─────────────────────────────────────────────────────────────────────────────
# Profile YAML loading
# ─────────────────────────────────────────────────────────────────────────────


class ProfileLoadingTests(SimpleTestCase):
    EXPECTED_PROFILES = {
        "airtel",
        "dialogic",
        "exotel_sip",
        "generic",
        "knowlarity",
        "myoperator",
        "plivo_sip",
        "servetel",
        "tata_tele",
        "telnyx_sip",
        "twilio_elastic_sip",
        "vonage_sip",
    }

    def setUp(self):
        templates._PROFILE_CACHE.clear()

    def test_all_expected_profiles_present(self):
        self.assertEqual(set(templates.list_profiles()), self.EXPECTED_PROFILES)

    def test_every_profile_has_required_keys(self):
        for name in self.EXPECTED_PROFILES:
            with self.subTest(profile=name):
                p = templates.load_profile(name)
                self.assertTrue(p.endpoint_template.strip(), f"{name} endpoint")
                self.assertTrue(p.auth_template.strip(), f"{name} auth")
                self.assertTrue(p.aor_template.strip(), f"{name} aor")

    def test_unknown_profile_raises(self):
        from voice.exceptions import VoiceCredentialError

        with self.assertRaises(VoiceCredentialError):
            templates.load_profile("does-not-exist")

    def test_ip_auth_profile_has_empty_registration(self):
        # vonage_sip / telnyx_sip / tata_tele / airtel / twilio_elastic_sip
        # are IP-auth-friendly — registration template is empty.
        for name in {"vonage_sip", "telnyx_sip", "tata_tele", "airtel", "twilio_elastic_sip"}:
            with self.subTest(profile=name):
                p = templates.load_profile(name)
                self.assertFalse(p.requires_registration, f"{name} should be IP-auth")


# ─────────────────────────────────────────────────────────────────────────────
# pjsip_writer.render_config
# ─────────────────────────────────────────────────────────────────────────────


class _FakeProviderConfig:
    """Lightweight stand-in for a VoiceProviderConfig — render_config
    only reads ``id`` + ``credentials``."""

    def __init__(self, id_, credentials):
        self.id = id_
        self.credentials = credentials


class PjsipWriterTests(SimpleTestCase):
    def setUp(self):
        templates._PROFILE_CACHE.clear()

    def test_render_with_dialogic_profile_inserts_credentials(self):
        cfg = _FakeProviderConfig(
            "abc-1234",
            json.dumps(
                {
                    "vendor_profile": "dialogic",
                    "sip_username": "tenant_user",
                    "sip_password": "tenant_pass",
                    "sip_realm": "sbc.dialogic.com",
                    "sip_proxy": "sbc.dialogic.com:5060",
                }
            ),
        )
        rendered = pjsip_writer.render_config(cfg)
        self.assertEqual(rendered.profile.name, "dialogic")
        # Section ids derived from the config id.
        self.assertEqual(rendered.endpoint_id, "voice-abc-1234-endpoint")
        # Credentials substituted into all sections.
        self.assertIn("tenant_user", rendered.auth)
        self.assertIn("tenant_pass", rendered.auth)
        self.assertIn("sbc.dialogic.com", rendered.aor)
        # Dialogic requires registration.
        self.assertTrue(rendered.registration)

    def test_render_with_ip_auth_profile_skips_registration(self):
        cfg = _FakeProviderConfig(
            "ipauth-1",
            json.dumps(
                {
                    "vendor_profile": "telnyx_sip",
                    "sip_username": "u",
                    "sip_password": "p",
                    "sip_realm": "sip.telnyx.com",
                    "sip_proxy": "sip.telnyx.com:5060",
                    "registration_required": False,
                }
            ),
        )
        rendered = pjsip_writer.render_config(cfg)
        # IP-auth → no registration emitted even though profile has the template.
        self.assertEqual(rendered.registration, "")

    def test_render_defaults_to_generic_when_no_vendor_profile(self):
        cfg = _FakeProviderConfig(
            "gen-1",
            json.dumps(
                {
                    "sip_username": "u",
                    "sip_password": "p",
                    "sip_realm": "r",
                    "sip_proxy": "p:5060",
                }
            ),
        )
        rendered = pjsip_writer.render_config(cfg)
        self.assertEqual(rendered.profile.name, "generic")

    def test_invalid_json_credentials_raises(self):
        from voice.exceptions import VoiceCredentialError

        cfg = _FakeProviderConfig("bad", "{not json}")
        with self.assertRaises(VoiceCredentialError):
            pjsip_writer.render_config(cfg)

    def test_rendered_to_pjsip_text_concatenates_sections(self):
        cfg = _FakeProviderConfig(
            "txt-1",
            json.dumps(
                {
                    "vendor_profile": "dialogic",
                    "sip_username": "u",
                    "sip_password": "p",
                    "sip_realm": "r",
                    "sip_proxy": "p:5060",
                }
            ),
        )
        text = pjsip_writer.render_config(cfg).to_pjsip_text()
        # Contains every section header.
        self.assertIn("voice-txt-1-endpoint", text)
        self.assertIn("voice-txt-1-auth", text)
        self.assertIn("voice-txt-1-aor", text)
        self.assertIn("voice-txt-1-reg", text)


# ─────────────────────────────────────────────────────────────────────────────
# AriClient (mocked HTTP)
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(
    ASTERISK_ARI_URL="http://asterisk.test:8088",
    ASTERISK_ARI_USER="user",
    ASTERISK_ARI_PASSWORD="pass",
    ASTERISK_ARI_APP_NAME="test-app",
)
class AriClientTests(SimpleTestCase):
    def _make_client(self):
        return AriClient()

    @patch("voice.sip_config.ari_client.requests.Session.request")
    def test_originate_posts_to_channels(self, mock_request):
        mock_resp = MagicMock(ok=True)
        mock_resp.json.return_value = {"id": "ch-1", "state": "Down"}
        mock_request.return_value = mock_resp

        client = self._make_client()
        result = client.originate(
            endpoint="PJSIP/+14155550100@voice-x-endpoint",
            callerid="+14155550199",
        )
        self.assertEqual(result["id"], "ch-1")
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "POST")
        self.assertIn("ari/channels", args[1])
        # App name from settings propagates.
        self.assertEqual(kwargs["json"]["app"], "test-app")

    @patch("voice.sip_config.ari_client.requests.Session.request")
    def test_non_ok_response_raises_ari_error(self, mock_request):
        mock_resp = MagicMock(ok=False, status_code=503, text="upstream down")
        mock_request.return_value = mock_resp

        client = self._make_client()
        with self.assertRaises(AriError) as ctx:
            client.hangup_channel("ch-bad")
        self.assertEqual(ctx.exception.status_code, 503)

    @patch("voice.sip_config.ari_client.requests.Session.request")
    def test_play_media_uses_correct_path(self, mock_request):
        mock_resp = MagicMock(ok=True)
        mock_resp.json.return_value = {"id": "play-1"}
        mock_request.return_value = mock_resp

        self._make_client().play_media("ch-1", media="sound:hello")
        args, kwargs = mock_request.call_args
        self.assertIn("ari/channels/ch-1/play", args[1])
        self.assertEqual(kwargs["params"], {"media": "sound:hello"})


class AriClientConfigTests(SimpleTestCase):
    @override_settings(ASTERISK_ARI_URL="")
    def test_unconfigured_raises_ari_error(self):
        client = AriClient()
        with self.assertRaises(AriError) as ctx:
            client.hangup_channel("ch-x")
        self.assertEqual(ctx.exception.status_code, 503)


# ─────────────────────────────────────────────────────────────────────────────
# ari_consumer.translate_event
# ─────────────────────────────────────────────────────────────────────────────


class AriConsumerTranslateTests(SimpleTestCase):
    def test_stasis_start_maps_to_initiated(self):
        ev = {
            "type": "StasisStart",
            "channel": {"id": "ch-1", "state": "Ring"},
            "args": ["CallID-1"],
        }
        out = translate_event(ev)
        assert out is not None
        self.assertEqual(out["provider_call_id"], "CallID-1")
        self.assertEqual(out["event_type"], CallEventType.INITIATED)

    def test_channel_state_change_up_maps_to_answered(self):
        ev = {
            "type": "ChannelStateChange",
            "channel": {"id": "ch-2", "state": "Up"},
        }
        out = translate_event(ev)
        assert out is not None
        self.assertEqual(out["event_type"], CallEventType.ANSWERED)

    def test_channel_state_change_other_returns_none(self):
        ev = {
            "type": "ChannelStateChange",
            "channel": {"id": "ch-3", "state": "Down"},
        }
        self.assertIsNone(translate_event(ev))

    def test_stasis_end_with_cause_maps_to_completed(self):
        ev = {
            "type": "StasisEnd",
            "channel": {"id": "ch-4"},
            "args": ["CallID-4"],
            "cause": 17,  # Q.850 User busy
        }
        out = translate_event(ev)
        assert out is not None
        self.assertEqual(out["event_type"], CallEventType.COMPLETED)
        self.assertEqual(out["hangup_cause"], "USER_BUSY")

    def test_unknown_q850_code_maps_to_unknown(self):
        ev = {
            "type": "StasisEnd",
            "channel": {"id": "ch-5"},
            "args": ["X"],
            "cause": 999,
        }
        out = translate_event(ev)
        assert out is not None
        self.assertEqual(out["hangup_cause"], "UNKNOWN")

    def test_irrelevant_event_returns_none(self):
        self.assertIsNone(translate_event({"type": "DeviceStateChanged"}))

    def test_dtmf_event_maps(self):
        ev = {"type": "ChannelDtmfReceived", "channel": {"id": "ch-6"}, "args": ["X"]}
        out = translate_event(ev)
        assert out is not None
        self.assertEqual(out["event_type"], CallEventType.DTMF)


# ─────────────────────────────────────────────────────────────────────────────
# ARI dialect (ari_commands)
# ─────────────────────────────────────────────────────────────────────────────


class AriCommandsDialectTests(SimpleTestCase):
    def test_play_tts(self):
        out = ari_commands.play({"tts_text": "Hello"}, {})
        self.assertEqual(out["op"], "play")
        self.assertEqual(out["media"], "synthesis:Hello")

    def test_play_audio_url(self):
        out = ari_commands.play({"audio_url": "https://example/file.wav"}, {})
        self.assertEqual(out["media"], "sound:https://example/file.wav")

    def test_play_neither_emits_silence(self):
        out = ari_commands.play({}, {})
        self.assertEqual(out["media"], "sound:silence/1")

    def test_gather_dtmf(self):
        out = ari_commands.gather_dtmf({"max_digits": 4, "timeout_seconds": 5}, {})
        self.assertEqual(out["op"], "gather_dtmf")
        self.assertEqual(out["max_digits"], 4)

    def test_transfer(self):
        out = ari_commands.transfer({"to_uri": "sip:agent@example"}, {})
        self.assertEqual(out, {"op": "transfer", "to_uri": "sip:agent@example"})

    def test_hangup(self):
        self.assertEqual(ari_commands.hangup({}, {}), {"op": "hangup"})

    def test_assemble_preserves_order(self):
        ops = [{"op": "play"}, {"op": "hangup"}]
        self.assertEqual(ari_commands.assemble(ops), ops)

    def test_get_handler_returns_known(self):
        for type_id in (
            "voice.play",
            "voice.gather_dtmf",
            "voice.gather_speech",
            "voice.record",
            "voice.transfer",
            "voice.hangup",
        ):
            self.assertIsNotNone(ari_commands.get_handler(type_id))

    def test_get_handler_unknown_returns_none(self):
        self.assertIsNone(ari_commands.get_handler("voice.does_not_exist"))


# ─────────────────────────────────────────────────────────────────────────────
# SIPVoiceAdapter (mocks AriClient)
# ─────────────────────────────────────────────────────────────────────────────


class SIPVoiceAdapterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from tenants.models import Tenant
        from voice.models import VoiceProviderConfig

        cls.tenant = Tenant.objects.create(name="SIP Adapter Tenant")
        cls.config = VoiceProviderConfig.objects.create(
            tenant=cls.tenant,
            name="SIP cfg",
            provider=VoiceProvider.SIP,
            credentials=json.dumps(
                {
                    "vendor_profile": "dialogic",
                    "sip_username": "u",
                    "sip_password": "p",
                    "sip_realm": "r",
                    "sip_proxy": "p:5060",
                }
            ),
        )

    def setUp(self):
        self.adapter = SIPVoiceAdapter(self.config)

    def test_capabilities_advertise_sip_features(self):
        caps = SIPVoiceAdapter.capabilities
        self.assertTrue(caps.supports_voice_call)
        self.assertTrue(caps.supports_recording)
        self.assertTrue(caps.supports_dtmf_gather)
        self.assertTrue(caps.supports_call_transfer)
        self.assertTrue(caps.supports_sip_refer)
        # SIP doesn't have a per-call cost callback like Twilio.
        self.assertFalse(caps.supports_provider_cost)

    def test_endpoint_id_is_derived_from_config_id(self):
        self.assertEqual(self.adapter.endpoint_id, f"voice-{self.config.id}-endpoint")

    @patch("voice.adapters.sip.AriClient")
    def test_initiate_call_dials_via_pjsip_endpoint(self, mock_client_cls):
        mock_ari = mock_client_cls.return_value
        mock_ari.originate.return_value = {"id": "ari-ch-1", "state": "Down"}

        from voice.adapters.base import CallInstructions

        # Recreate adapter so it picks up the mocked AriClient.
        adapter = SIPVoiceAdapter(self.config)
        handle = adapter.initiate_call(
            from_number="+14155550100",
            to_number="+14155550200",
            callback_url="ignored-for-sip",
            instructions=CallInstructions(),
        )

        self.assertEqual(handle.provider_call_id, "ari-ch-1")
        mock_ari.originate.assert_called_once()
        kw = mock_ari.originate.call_args.kwargs
        self.assertTrue(kw["endpoint"].startswith("PJSIP/+14155550200@voice-"))
        self.assertEqual(kw["callerid"], "+14155550100")

    @patch("voice.adapters.sip.AriClient")
    def test_hangup_calls_ari(self, mock_client_cls):
        mock_ari = mock_client_cls.return_value
        adapter = SIPVoiceAdapter(self.config)
        adapter.hangup("ari-ch-1")
        mock_ari.hangup_channel.assert_called_once_with("ari-ch-1")

    @patch("voice.adapters.sip.AriClient")
    def test_play_with_audio_url(self, mock_client_cls):
        mock_ari = mock_client_cls.return_value
        adapter = SIPVoiceAdapter(self.config)
        adapter.play("ch", audio_url="https://example/file.wav")
        mock_ari.play_media.assert_called_once_with("ch", media="sound:https://example/file.wav")

    @patch("voice.adapters.sip.AriClient")
    def test_start_recording_returns_name(self, mock_client_cls):
        mock_ari = mock_client_cls.return_value
        adapter = SIPVoiceAdapter(self.config)
        name = adapter.start_recording("ch-99")
        self.assertEqual(name, "call-ch-99")
        mock_ari.record_channel.assert_called_once()

    @patch("voice.adapters.sip.AriClient")
    def test_transfer_uses_refer(self, mock_client_cls):
        mock_ari = mock_client_cls.return_value
        adapter = SIPVoiceAdapter(self.config)
        adapter.transfer("ch-7", to_uri="sip:agent@example")
        mock_ari.refer_channel.assert_called_once_with("ch-7", to_uri="sip:agent@example")

    def test_parse_webhook_raises(self):
        """SIP doesn't receive HTTP webhooks — ARI events instead."""
        with self.assertRaises(NotImplementedError):
            self.adapter.parse_webhook(None)


# ─────────────────────────────────────────────────────────────────────────────
# Q.850 mapping snapshot
# ─────────────────────────────────────────────────────────────────────────────


class Q850MappingTests(SimpleTestCase):
    def test_known_mappings(self):
        self.assertEqual(_Q850[16], "NORMAL_CLEARING")
        self.assertEqual(_Q850[17], "USER_BUSY")
        self.assertEqual(_Q850[19], "NO_ANSWER")
