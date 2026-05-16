"""TwiML dialect emitter tests (#160)."""

from __future__ import annotations

from django.test import SimpleTestCase

from voice.ivr.dialects import twiml


class PlayTests(SimpleTestCase):
    def test_tts_only(self):
        out = twiml.play({"tts_text": "Hello world"}, {})
        self.assertIn("<Say>Hello world</Say>", out)

    def test_tts_with_voice_and_language(self):
        out = twiml.play(
            {
                "tts_text": "Hello",
                "tts_voice": "Polly.Aditi",
                "tts_language": "en-IN",
            },
            {},
        )
        self.assertIn('voice="Polly.Aditi"', out)
        self.assertIn('language="en-IN"', out)

    def test_audio_only(self):
        out = twiml.play({"audio_url": "https://example.com/audio.mp3"}, {})
        self.assertEqual(out, "<Play>https://example.com/audio.mp3</Play>")

    def test_tts_wins_when_both_set(self):
        out = twiml.play({"tts_text": "tts wins", "audio_url": "https://x/x.mp3"}, {})
        self.assertIn("<Say>tts wins</Say>", out)
        self.assertNotIn("<Play>", out)

    def test_neither_emits_silent_pause(self):
        out = twiml.play({}, {})
        self.assertEqual(out, '<Pause length="1"/>')

    def test_tts_text_escapes_xml(self):
        out = twiml.play({"tts_text": "<script>alert('x')</script>"}, {})
        # The angle brackets must be escaped so the response is well-formed.
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)


class GatherDtmfTests(SimpleTestCase):
    def test_basic(self):
        out = twiml.gather_dtmf({"max_digits": 4, "timeout_seconds": 5}, {})
        self.assertIn('numDigits="4"', out)
        self.assertIn('timeout="5"', out)
        self.assertIn('input="dtmf"', out)

    def test_with_prompt_tts(self):
        out = twiml.gather_dtmf(
            {"max_digits": 1, "timeout_seconds": 3, "prompt_tts": "Pick one"},
            {},
        )
        self.assertIn("<Say>Pick one</Say>", out)

    def test_with_action_url(self):
        out = twiml.gather_dtmf(
            {"max_digits": 1, "timeout_seconds": 3},
            {"gather_action_url": "https://example.com/gather"},
        )
        self.assertIn('action="https://example.com/gather"', out)

    def test_finish_on_key(self):
        out = twiml.gather_dtmf(
            {"max_digits": 10, "timeout_seconds": 30, "finish_on_key": "#"},
            {},
        )
        self.assertIn('finishOnKey="#"', out)


class GatherSpeechTests(SimpleTestCase):
    def test_basic(self):
        out = twiml.gather_speech({"language": "en-US", "timeout_seconds": 5}, {})
        self.assertIn('input="speech"', out)
        self.assertIn('language="en-US"', out)
        self.assertIn('speechTimeout="5"', out)


class RecordTests(SimpleTestCase):
    def test_basic(self):
        out = twiml.record({"max_duration_seconds": 60}, {})
        self.assertIn('maxLength="60"', out)
        self.assertIn('playBeep="true"', out)


class TransferTests(SimpleTestCase):
    def test_basic(self):
        out = twiml.transfer({"to_uri": "+14155550100"}, {})
        self.assertEqual(out, "<Dial>+14155550100</Dial>")


class HangupTests(SimpleTestCase):
    def test_basic(self):
        self.assertEqual(twiml.hangup({}, {}), "<Hangup/>")


class AssembleTests(SimpleTestCase):
    def test_wraps_in_response_with_prolog(self):
        out = twiml.assemble(["<Say>hi</Say>", "<Hangup/>"])
        self.assertTrue(out.startswith('<?xml version="1.0"'))
        self.assertIn("<Response>", out)
        self.assertIn("<Say>hi</Say>", out)
        self.assertIn("<Hangup/></Response>", out)


class HandlerLookupTests(SimpleTestCase):
    def test_known_types(self):
        for type_id in (
            "voice.play",
            "voice.gather_dtmf",
            "voice.gather_speech",
            "voice.record",
            "voice.transfer",
            "voice.hangup",
        ):
            self.assertIsNotNone(twiml.get_handler(type_id), type_id)

    def test_unknown_returns_none(self):
        self.assertIsNone(twiml.get_handler("voice.does_not_exist"))
