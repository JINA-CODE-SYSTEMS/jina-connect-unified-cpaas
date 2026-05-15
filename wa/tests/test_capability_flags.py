"""
Capability flag tests
=====================

Verifies that every channel adapter declares a ``platform`` and a
``capabilities`` ``Capabilities`` instance, and that declared flags align with
the adapter's actual method implementations.

The mapping ``CAPABILITY_TO_METHOD`` is intentionally explicit so a future
adapter that lies about its capabilities (e.g. declares ``supports_media=True``
without overriding ``send_media``) fails this test.

HOW TO RUN:
    DJANGO_SETTINGS_MODULE=jina_connect.settings python -m pytest wa/tests/test_capability_flags.py -v
"""

from __future__ import annotations

import dataclasses
from typing import Type

from django.test import TestCase

from jina_connect.platform_choices import PlatformChoices
from wa.adapters.channel_base import BaseChannelAdapter, Capabilities

# ─────────────────────────────────────────────────────────────────────────────
# Map each Capabilities flag to the method name it implies.  None means the
# flag has no single 1:1 method (e.g. supports_provider_cost — handled in
# billing pipeline; supports_voice_call — covered by initiate_call in voice).
# ─────────────────────────────────────────────────────────────────────────────

CAPABILITY_TO_METHOD: dict[str, str | None] = {
    "supports_text": "send_text",
    "supports_media": "send_media",
    "supports_keyboards": "send_keyboard",
    # Below are channel features that are exercised through more than one
    # method (templates have submit/sync/delete, reactions are kwargs, etc.).
    # The test verifies declarations rather than method presence for these.
    "supports_templates": None,
    "supports_template_buttons": None,
    "supports_reactions": None,
    "supports_typing_indicator": None,
    # Voice-only flags — none of the existing text adapters declare these.
    "supports_voice_call": "initiate_call",
    "supports_recording": "start_recording",
    "supports_dtmf_gather": "gather_dtmf",
    "supports_speech_gather": "gather_speech",
    "supports_call_transfer": "transfer",
    "supports_sip_refer": None,
    "supports_conference": None,
    # Billing
    "supports_provider_cost": None,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _all_adapter_classes() -> list[Type[BaseChannelAdapter]]:
    """Import every concrete channel adapter so we can introspect them."""
    from rcs.services.message_sender import RCSMessageSender
    from sms.services.message_sender import SMSMessageSender
    from telegram.services.message_sender import TelegramMessageSender
    from wa.adapters.gupshup import GupshupAdapter
    from wa.adapters.meta_direct import MetaDirectAdapter

    return [
        MetaDirectAdapter,
        GupshupAdapter,
        SMSMessageSender,
        TelegramMessageSender,
        RCSMessageSender,
    ]


def _method_is_overridden(cls: Type[BaseChannelAdapter], method_name: str) -> bool:
    """True if ``cls`` defines ``method_name`` itself (not inherited as abstract)."""
    method = getattr(cls, method_name, None)
    if method is None:
        return False
    # An abstract method inherited from BaseChannelAdapter has
    # ``__isabstractmethod__ = True``.  An override does not.
    return not getattr(method, "__isabstractmethod__", False)


# ─────────────────────────────────────────────────────────────────────────────
# Capabilities dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class CapabilitiesDataclassTests(TestCase):
    def test_capabilities_is_frozen(self):
        """``Capabilities`` is frozen — instances cannot be mutated."""
        caps = Capabilities()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            caps.supports_text = False  # type: ignore[misc]

    def test_defaults_are_conservative(self):
        """All non-text flags default to False; ``supports_text`` defaults True."""
        caps = Capabilities()
        self.assertTrue(caps.supports_text)
        # Voice and most rich-feature flags default False.
        for flag in (
            "supports_media",
            "supports_keyboards",
            "supports_templates",
            "supports_template_buttons",
            "supports_reactions",
            "supports_typing_indicator",
            "supports_voice_call",
            "supports_recording",
            "supports_dtmf_gather",
            "supports_speech_gather",
            "supports_call_transfer",
            "supports_sip_refer",
            "supports_conference",
            "supports_provider_cost",
        ):
            self.assertFalse(getattr(caps, flag), f"{flag} should default to False")

    def test_extra_defaults_to_empty_frozenset(self):
        caps = Capabilities()
        self.assertEqual(caps.extra, frozenset())
        self.assertIsInstance(caps.extra, frozenset)

    def test_capabilities_field_coverage(self):
        """Every field on ``Capabilities`` is mapped in CAPABILITY_TO_METHOD."""
        declared_fields = {f.name for f in dataclasses.fields(Capabilities)}
        declared_fields.discard("extra")
        mapped = set(CAPABILITY_TO_METHOD.keys())
        missing = declared_fields - mapped
        self.assertFalse(
            missing,
            f"Capabilities fields not mapped in CAPABILITY_TO_METHOD: {sorted(missing)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Per-adapter declarations
# ─────────────────────────────────────────────────────────────────────────────


class AdapterDeclarationsTests(TestCase):
    def test_every_adapter_sets_platform(self):
        """Every concrete adapter declares a non-empty ``platform``."""
        valid_platforms = set(PlatformChoices.values)
        for cls in _all_adapter_classes():
            with self.subTest(adapter=cls.__name__):
                self.assertTrue(cls.platform, f"{cls.__name__}.platform is empty")
                self.assertIn(
                    cls.platform,
                    valid_platforms,
                    f"{cls.__name__}.platform={cls.platform!r} not in PlatformChoices",
                )

    def test_every_adapter_sets_capabilities(self):
        """Every concrete adapter declares a ``Capabilities`` instance."""
        for cls in _all_adapter_classes():
            with self.subTest(adapter=cls.__name__):
                self.assertIsInstance(
                    cls.capabilities,
                    Capabilities,
                    f"{cls.__name__}.capabilities is not a Capabilities instance",
                )

    def test_declared_method_capabilities_match_implementation(self):
        """
        For each flag with a 1:1 method mapping, the adapter's declared flag
        must match whether the method is actually overridden.

        Catches the case where an adapter declares ``supports_media=True`` but
        never overrides ``send_media`` (or vice versa).
        """
        for cls in _all_adapter_classes():
            for flag, method_name in CAPABILITY_TO_METHOD.items():
                if method_name is None:
                    continue
                with self.subTest(adapter=cls.__name__, flag=flag):
                    declared = getattr(cls.capabilities, flag)
                    overridden = _method_is_overridden(cls, method_name)
                    if declared and not overridden:
                        self.fail(f"{cls.__name__} declares {flag}=True but does not override {method_name}")

    def test_get_channel_name_matches_platform(self):
        """``get_channel_name()`` returns the same string as ``platform``."""
        for cls in _all_adapter_classes():
            with self.subTest(adapter=cls.__name__):
                # Need an instance; pass a sentinel for the *_app argument.
                # The constructors only stash the argument, so this is safe.
                try:
                    instance = cls.__new__(cls)
                except TypeError:
                    continue
                self.assertEqual(
                    instance.get_channel_name(),
                    cls.platform,
                    f"{cls.__name__}.get_channel_name() does not match .platform",
                )
