"""Registry tests for #188."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from chat_flow.triggers.base import BaseTrigger
from chat_flow.triggers.registry import (
    _REGISTRY,
    get_trigger_cls,
    list_trigger_types,
    register_trigger,
    trigger_introspection,
)


class _NoopConfig(BaseModel):
    flag: bool = False


class _NoopTrigger(BaseTrigger):
    config_model = _NoopConfig

    def matches(self, event, config):  # pragma: no cover — registry tests only
        return False


class TestRegistry:
    def test_register_and_retrieve(self):
        original = _REGISTRY.copy()
        try:
            register_trigger("noop_test_a")(_NoopTrigger)
            assert get_trigger_cls("noop_test_a") is _NoopTrigger
            assert _NoopTrigger.type_name == "noop_test_a"
            assert "noop_test_a" in list_trigger_types()
        finally:
            _REGISTRY.clear()
            _REGISTRY.update(original)

    def test_reregistering_same_class_is_noop(self):
        original = _REGISTRY.copy()
        try:
            register_trigger("noop_test_b")(_NoopTrigger)
            # Decorate the same class a second time under the same name —
            # idempotent for module-reload paths.
            register_trigger("noop_test_b")(_NoopTrigger)
            assert get_trigger_cls("noop_test_b") is _NoopTrigger
        finally:
            _REGISTRY.clear()
            _REGISTRY.update(original)

    def test_reregistering_different_class_raises(self):
        class _OtherTrigger(BaseTrigger):
            config_model = _NoopConfig

            def matches(self, event, config):  # pragma: no cover
                return False

        original = _REGISTRY.copy()
        try:
            register_trigger("noop_test_c")(_NoopTrigger)
            with pytest.raises(RuntimeError, match="already registered"):
                register_trigger("noop_test_c")(_OtherTrigger)
        finally:
            _REGISTRY.clear()
            _REGISTRY.update(original)

    def test_get_unknown_raises_lookup_error(self):
        with pytest.raises(LookupError):
            get_trigger_cls("does_not_exist")

    def test_introspection_shape(self):
        # Ships-with-the-substrate triggers should always be discoverable.
        names = {entry["type_name"] for entry in trigger_introspection()}
        assert "inbound_keyword_match" in names
        assert "ctwa_referral_received" in names
        # Each entry has a JSON-Schema-like dict for config.
        for entry in trigger_introspection():
            assert "type_name" in entry
            assert "config_schema" in entry
            assert isinstance(entry["config_schema"], dict)
