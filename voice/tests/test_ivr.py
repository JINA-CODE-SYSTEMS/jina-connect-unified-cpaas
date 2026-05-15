"""IVR compiler + session + node-spec registration tests (#168)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from chat_flow.node_registry import _REGISTRY, get_node_type, validate_flow_for_platform
from jina_connect.platform_choices import PlatformChoices
from voice.ivr import session as ivr_session
from voice.ivr.compiler import IvrCompilationError, compile_for_adapter
from voice.ivr.node_specs import register_voice_node_types

# ─────────────────────────────────────────────────────────────────────────────
# Voice node-type registration
# ─────────────────────────────────────────────────────────────────────────────


class VoiceNodeRegistrationTests(SimpleTestCase):
    def setUp(self):
        self._snapshot = dict(_REGISTRY)
        _REGISTRY.clear()
        register_voice_node_types()

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._snapshot)

    def test_all_six_voice_types_registered(self):
        for type_id in (
            "voice.play",
            "voice.gather_dtmf",
            "voice.gather_speech",
            "voice.record",
            "voice.transfer",
            "voice.hangup",
        ):
            self.assertIsNotNone(get_node_type(type_id), type_id)

    def test_voice_play_requires_audio_or_tts(self):
        spec = get_node_type("voice.play")
        assert spec is not None
        errors = spec.validator({})
        self.assertEqual(len(errors), 1)
        self.assertIn("audio_url or tts_text", errors[0])

    def test_voice_play_with_audio_url_passes(self):
        spec = get_node_type("voice.play")
        assert spec is not None
        self.assertEqual(spec.validator({"audio_url": "https://x"}), [])

    def test_voice_node_rejected_on_whatsapp(self):
        """Saving a flow with voice nodes on platform=WHATSAPP raises."""
        flow = {"nodes": [{"id": "n1", "type": "voice.play", "data": {}}]}
        errors = validate_flow_for_platform(flow, PlatformChoices.WHATSAPP)
        # Errors mention the unsupported platform.
        self.assertTrue(any("not supported" in e for e in errors))

    def test_gather_dtmf_requires_max_digits_and_timeout(self):
        spec = get_node_type("voice.gather_dtmf")
        assert spec is not None
        self.assertEqual(spec.required_data_fields, frozenset(["max_digits", "timeout_seconds"]))

    def test_re_registration_is_idempotent(self):
        # Registry is no-op on identical re-registration (per #156 review fix).
        register_voice_node_types()
        register_voice_node_types()


# ─────────────────────────────────────────────────────────────────────────────
# Compiler
# ─────────────────────────────────────────────────────────────────────────────


class _FakeDialect:
    """In-test dialect — captures what the compiler emitted so tests
    can assert on the order / payload."""

    def __init__(self):
        self.calls = []

    def get_handler(self, type_id):
        return {
            "voice.play": self._play,
            "voice.gather_dtmf": self._gather,
            "voice.transfer": self._transfer,
            "voice.hangup": self._hangup,
        }.get(type_id)

    def _play(self, data, context):
        self.calls.append(("play", data, context))
        return ("play", data)

    def _gather(self, data, context):
        self.calls.append(("gather", data, context))
        return ("gather", data)

    def _transfer(self, data, context):
        self.calls.append(("transfer", data, context))
        return ("transfer", data)

    def _hangup(self, data, context):
        self.calls.append(("hangup", data, context))
        return ("hangup", data)

    def assemble(self, chunks):
        return chunks


class _FakeAdapter:
    def __init__(self, dialect):
        self._dialect = dialect

    def get_dialect(self):
        return self._dialect


class CompilerTests(SimpleTestCase):
    def setUp(self):
        self.dialect = _FakeDialect()
        self.adapter = _FakeAdapter(self.dialect)

    def test_declaration_order_when_no_entry(self):
        flow = {
            "nodes": [
                {"id": "n1", "type": "voice.play", "data": {"tts_text": "Hi"}},
                {"id": "n2", "type": "voice.hangup", "data": {}},
            ]
        }
        out = compile_for_adapter(flow, self.adapter)
        self.assertEqual(out, [("play", {"tts_text": "Hi"}), ("hangup", {})])

    def test_follows_edges_from_entry_node(self):
        flow = {
            "entry_node_id": "welcome",
            "nodes": [
                {"id": "welcome", "type": "voice.play", "data": {"tts_text": "Hi"}},
                {"id": "menu", "type": "voice.gather_dtmf", "data": {"max_digits": 1, "timeout_seconds": 3}},
                {"id": "bye", "type": "voice.hangup", "data": {}},
                {"id": "unused", "type": "voice.play", "data": {"tts_text": "unused"}},
            ],
            "edges": [
                {"id": "e1", "source": "welcome", "target": "menu"},
                {"id": "e2", "source": "menu", "target": "bye"},
            ],
        }
        out = compile_for_adapter(flow, self.adapter)
        self.assertEqual(len(out), 3)
        verbs = [c[0] for c in out]
        self.assertEqual(verbs, ["play", "gather", "hangup"])
        # ``unused`` is not in the walked path.
        self.assertNotIn("unused", [c.get("tts_text") for _, c in out])

    def test_skips_unknown_node_types_with_warning(self):
        flow = {
            "nodes": [
                {"id": "n1", "type": "voice.play", "data": {"tts_text": "Hi"}},
                {"id": "n2", "type": "totally.unknown", "data": {}},
                {"id": "n3", "type": "voice.hangup", "data": {}},
            ]
        }
        out = compile_for_adapter(flow, self.adapter)
        self.assertEqual([c[0] for c in out], ["play", "hangup"])

    def test_channel_agnostic_nodes_skipped_silently(self):
        flow = {
            "nodes": [
                {"id": "n1", "type": "voice.play", "data": {"tts_text": "Hi"}},
                {"id": "n2", "type": "conditional", "data": {}},
                {"id": "n3", "type": "set_variable", "data": {}},
                {"id": "n4", "type": "voice.hangup", "data": {}},
            ]
        }
        out = compile_for_adapter(flow, self.adapter)
        self.assertEqual([c[0] for c in out], ["play", "hangup"])

    def test_empty_flow_raises(self):
        with self.assertRaises(IvrCompilationError):
            compile_for_adapter({}, self.adapter)

    def test_unknown_entry_node_id_raises(self):
        flow = {
            "entry_node_id": "ghost",
            "nodes": [{"id": "n1", "type": "voice.hangup", "data": {}}],
        }
        with self.assertRaises(IvrCompilationError):
            compile_for_adapter(flow, self.adapter)

    def test_all_unhandled_nodes_raises(self):
        flow = {
            "nodes": [
                {"id": "n1", "type": "totally.unknown", "data": {}},
                {"id": "n2", "type": "conditional", "data": {}},
            ]
        }
        with self.assertRaises(IvrCompilationError):
            compile_for_adapter(flow, self.adapter)

    def test_passes_context_to_handlers(self):
        flow = {"nodes": [{"id": "n1", "type": "voice.gather_dtmf", "data": {"max_digits": 1, "timeout_seconds": 3}}]}
        context = {"gather_action_url": "https://example/gather"}
        compile_for_adapter(flow, self.adapter, context=context)
        # Dialect captured the context we passed.
        _, _, ctx_received = self.dialect.calls[0]
        self.assertEqual(ctx_received["gather_action_url"], "https://example/gather")


# ─────────────────────────────────────────────────────────────────────────────
# Dialect dispatch — every real adapter wires the right dialect module
# ─────────────────────────────────────────────────────────────────────────────


class AdapterDialectDispatchTests(SimpleTestCase):
    """Each concrete adapter ``get_dialect()`` points at the right
    dialect module."""

    def _adapter_cls_returns(self, adapter_cls_path: str, expected_module: str):
        import importlib

        module_path, _, cls_name = adapter_cls_path.rpartition(".")
        cls = getattr(importlib.import_module(module_path), cls_name)
        # Build a minimal-ish instance — skip __init__ for HTTP adapters
        # that touch DB / decoding.
        adapter = cls.__new__(cls)
        # ``get_dialect`` is a method, but doesn't read ``self``.
        dialect = adapter.get_dialect()
        self.assertTrue(
            dialect.__name__.endswith(expected_module),
            f"expected ...{expected_module}, got {dialect.__name__}",
        )

    def test_twilio_uses_twiml(self):
        self._adapter_cls_returns("voice.adapters.http_voice.twilio.TwilioVoiceAdapter", "twiml")

    def test_plivo_uses_plivo_xml(self):
        self._adapter_cls_returns("voice.adapters.http_voice.plivo.PlivoVoiceAdapter", "plivo_xml")

    def test_vonage_uses_ncco(self):
        self._adapter_cls_returns("voice.adapters.http_voice.vonage.VonageVoiceAdapter", "ncco")

    def test_telnyx_uses_telnyx_cc(self):
        self._adapter_cls_returns("voice.adapters.http_voice.telnyx.TelnyxVoiceAdapter", "telnyx_cc")

    def test_exotel_uses_exotel_xml(self):
        self._adapter_cls_returns("voice.adapters.http_voice.exotel.ExotelVoiceAdapter", "exotel_xml")

    def test_sip_uses_ari_commands(self):
        self._adapter_cls_returns("voice.adapters.sip.SIPVoiceAdapter", "ari_commands")


# ─────────────────────────────────────────────────────────────────────────────
# Compile against every real dialect
# ─────────────────────────────────────────────────────────────────────────────


class CompileAgainstEachRealDialectTests(SimpleTestCase):
    """End-to-end: same flow_data compiled through every dialect
    produces a non-empty output of the expected shape."""

    FLOW = {
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "voice.play", "data": {"tts_text": "Hello"}},
            {"id": "n2", "type": "voice.hangup", "data": {}},
        ],
        "edges": [{"source": "n1", "target": "n2"}],
    }

    def _compile_with_dialect_module(self, module_path: str):
        import importlib

        dialect = importlib.import_module(module_path)
        adapter = _FakeAdapter(dialect)
        return compile_for_adapter(self.FLOW, adapter)

    def test_twiml_emits_xml_string(self):
        out = self._compile_with_dialect_module("voice.ivr.dialects.twiml")
        self.assertIsInstance(out, str)
        self.assertIn("<Response>", out)
        self.assertIn("<Say>Hello</Say>", out)
        self.assertIn("<Hangup/>", out)

    def test_plivo_xml_emits_xml_string(self):
        out = self._compile_with_dialect_module("voice.ivr.dialects.plivo_xml")
        self.assertIsInstance(out, str)
        self.assertIn("<Speak>Hello</Speak>", out)
        self.assertIn("<Hangup/>", out)

    def test_ncco_emits_list_of_dicts(self):
        out = self._compile_with_dialect_module("voice.ivr.dialects.ncco")
        self.assertIsInstance(out, list)
        self.assertEqual(out[0]["action"], "talk")
        self.assertEqual(out[0]["text"], "Hello")

    def test_telnyx_cc_emits_command_list(self):
        out = self._compile_with_dialect_module("voice.ivr.dialects.telnyx_cc")
        self.assertIsInstance(out, list)
        self.assertEqual(out[0]["verb"], "speak")
        self.assertEqual(out[-1]["verb"], "hangup")

    def test_exotel_xml_emits_xml_string(self):
        out = self._compile_with_dialect_module("voice.ivr.dialects.exotel_xml")
        self.assertIsInstance(out, str)
        self.assertIn("<Say>Hello</Say>", out)

    def test_ari_commands_emits_command_list(self):
        out = self._compile_with_dialect_module("voice.ivr.dialects.ari_commands")
        self.assertIsInstance(out, list)
        self.assertEqual(out[0]["op"], "play")
        self.assertEqual(out[-1]["op"], "hangup")


# ─────────────────────────────────────────────────────────────────────────────
# IVR session
# ─────────────────────────────────────────────────────────────────────────────


class IvrSessionTests(SimpleTestCase):
    """Session reads/writes via a mocked Redis client."""

    def setUp(self):
        self._redis_patcher = patch("voice.ivr.session._get_redis_client")
        self.mock_get_client = self._redis_patcher.start()
        self.mock_client = MagicMock()
        self.mock_get_client.return_value = self.mock_client
        # Internal store so set/get round-trip works in tests.
        self.store: dict[str, str] = {}
        self.mock_client.set.side_effect = self._store_set
        self.mock_client.get.side_effect = self._store_get
        self.mock_client.exists.side_effect = self._store_exists
        self.mock_client.delete.side_effect = self._store_delete

    def tearDown(self):
        self._redis_patcher.stop()

    def _store_set(self, key, value, ex=None, nx=None):
        self.store[key] = value
        return True

    def _store_get(self, key):
        return self.store.get(key)

    def _store_exists(self, key):
        return 1 if key in self.store else 0

    def _store_delete(self, key):
        return self.store.pop(key, None) is not None

    def test_create_persists_initial_state(self):
        s = ivr_session.IVRSession.create(
            "call-1",
            flow_id="flow-x",
            entry_node_id="n1",
            variables={"name": "Alice"},
        )
        state = s.get()
        self.assertEqual(state["current_node_id"], "n1")
        self.assertEqual(state["flow_id"], "flow-x")
        self.assertEqual(state["variables"]["name"], "Alice")
        # TTL applied — set was called with ex= kwarg.
        ex = self.mock_client.set.call_args.kwargs.get("ex")
        self.assertIsNotNone(ex)
        self.assertGreater(ex, 0)

    def test_exists_returns_false_when_missing(self):
        self.assertFalse(ivr_session.IVRSession.exists("never-existed"))

    def test_exists_returns_true_after_create(self):
        ivr_session.IVRSession.create("call-2", flow_id="f")
        self.assertTrue(ivr_session.IVRSession.exists("call-2"))

    def test_get_returns_none_when_missing(self):
        s = ivr_session.IVRSession("never-existed")
        self.assertIsNone(s.get())

    def test_update_merges_state(self):
        s = ivr_session.IVRSession.create("call-3", flow_id="f", entry_node_id="n1")
        s.update(current_node_id="n2")
        self.assertEqual(s.get()["current_node_id"], "n2")
        # flow_id preserved.
        self.assertEqual(s.get()["flow_id"], "f")

    def test_update_merges_variables_dict(self):
        s = ivr_session.IVRSession.create("call-4", flow_id="f", variables={"a": 1})
        s.update(variables={"b": 2})
        vars_now = s.get()["variables"]
        self.assertEqual(vars_now, {"a": 1, "b": 2})

    def test_update_raises_when_session_missing(self):
        s = ivr_session.IVRSession("never-existed")
        with self.assertRaises(RuntimeError):
            s.update(current_node_id="x")

    def test_advance_to_clears_dtmf_buffer(self):
        s = ivr_session.IVRSession.create("call-5", flow_id="f", entry_node_id="n1")
        s.record_dtmf("1")
        s.record_dtmf("2")
        self.assertEqual(s.get()["dtmf_buffer"], "12")
        s.advance_to("n2")
        self.assertEqual(s.get()["dtmf_buffer"], "")
        self.assertEqual(s.get()["current_node_id"], "n2")

    def test_record_dtmf_appends(self):
        s = ivr_session.IVRSession.create("call-6", flow_id="f")
        s.record_dtmf("9")
        s.record_dtmf("1")
        s.record_dtmf("1")
        self.assertEqual(s.get()["dtmf_buffer"], "911")

    def test_set_variable(self):
        s = ivr_session.IVRSession.create("call-7", flow_id="f")
        s.set_variable("zip", "94110")
        self.assertEqual(s.get()["variables"]["zip"], "94110")

    def test_increment_retry_counts_per_node(self):
        s = ivr_session.IVRSession.create("call-8", flow_id="f")
        self.assertEqual(s.increment_retry("menu"), 1)
        self.assertEqual(s.increment_retry("menu"), 2)
        self.assertEqual(s.increment_retry("other"), 1)

    def test_delete_removes_session(self):
        s = ivr_session.IVRSession.create("call-9", flow_id="f")
        self.assertTrue(ivr_session.IVRSession.exists("call-9"))
        s.delete()
        self.assertFalse(ivr_session.IVRSession.exists("call-9"))

    def test_invalid_json_in_redis_returns_none(self):
        # Pre-seed a bad value at the expected key.
        self.store["voice:ivr:bad"] = "{not json"
        s = ivr_session.IVRSession("bad")
        self.assertIsNone(s.get())
