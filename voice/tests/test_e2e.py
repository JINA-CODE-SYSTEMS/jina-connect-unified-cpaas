"""End-to-end voice tests against real provider sandboxes (#176).

These tests are gated by the ``VOICE_E2E_TESTS=1`` env var so a regular
PR run never burns sandbox credits / talks to external services. The
GitHub Actions ``voice-e2e.yml`` workflow flips the flag (and supplies
the sandbox credentials via secrets) on its schedule + manual trigger.

Per-provider, each test:

  1. Skips fast if the sandbox credentials for that provider are missing
     (so a partial credentials set still produces a green run).
  2. Stands up its own tenant + provider config so tests don't share
     state and can run in parallel.
  3. Polls the ``VoiceCall`` row for status transitions with a hard
     90-second cap — anything longer means the sandbox is slow / down
     and we'd rather fail than hang CI.
  4. Cleans up its own rows on success and failure (``addCleanup``).

If a new provider's sandbox creds are added later, register them in the
``PROVIDER_CREDS`` table below and write the matching test by following
the Twilio pattern — the helpers do most of the work.

Inbound + recording + transcription cases are written as best-effort:
they assert the lifecycle that the provider sandbox actually emits.
Where the sandbox can't drive an inbound call (e.g. test creds don't
allow it), the inbound test skips with a clear message instead of
faking the webhook.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest
from django.test import TransactionTestCase

from contacts.models import TenantContact
from tenants.models import Tenant, TenantVoiceApp
from voice.constants import TERMINAL_STATUSES, CallStatus, VoiceProvider
from voice.models import (
    VoiceCall,
    VoiceCallEvent,
    VoiceProviderConfig,
    VoiceRecording,
)

# ─────────────────────────────────────────────────────────────────────────────
# Gating
# ─────────────────────────────────────────────────────────────────────────────


E2E_ENABLED = os.environ.get("VOICE_E2E_TESTS") == "1"
SIP_E2E_ENABLED = os.environ.get("VOICE_E2E_SIP_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not E2E_ENABLED,
    reason="VOICE_E2E_TESTS not set; this suite hits real provider sandboxes.",
)


# Per-test budget. Real provider sandboxes are slow — 90s lets the call
# go from INITIATING → RINGING → IN_PROGRESS → COMPLETED without giving
# us a hung CI job. The poll cadence is 2s so transitions are caught
# without spamming the DB.
POLL_INTERVAL_SECONDS = 2.0
TERMINAL_TIMEOUT_SECONDS = 90.0
RECORDING_TIMEOUT_SECONDS = 60.0
TRANSCRIPTION_TIMEOUT_SECONDS = 90.0


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox credential lookup
# ─────────────────────────────────────────────────────────────────────────────


# Each entry resolves the env vars for *this* provider's sandbox. The
# test skips if any required var is missing, so a half-configured CI
# environment doesn't crash — it just runs the providers whose creds
# are present.
PROVIDER_CREDS = {
    VoiceProvider.TWILIO: {
        "required": ("VOICE_E2E_TWILIO_SID", "VOICE_E2E_TWILIO_TOKEN", "VOICE_E2E_TWILIO_FROM", "VOICE_E2E_TWILIO_TO"),
        "build": lambda env: {
            "credentials": {
                "account_sid": env["VOICE_E2E_TWILIO_SID"],
                "auth_token": env["VOICE_E2E_TWILIO_TOKEN"],
            },
            "from_numbers": [env["VOICE_E2E_TWILIO_FROM"]],
            "to_number": env["VOICE_E2E_TWILIO_TO"],
        },
    },
    VoiceProvider.PLIVO: {
        "required": ("VOICE_E2E_PLIVO_ID", "VOICE_E2E_PLIVO_TOKEN", "VOICE_E2E_PLIVO_FROM", "VOICE_E2E_PLIVO_TO"),
        "build": lambda env: {
            "credentials": {
                "auth_id": env["VOICE_E2E_PLIVO_ID"],
                "auth_token": env["VOICE_E2E_PLIVO_TOKEN"],
            },
            "from_numbers": [env["VOICE_E2E_PLIVO_FROM"]],
            "to_number": env["VOICE_E2E_PLIVO_TO"],
        },
    },
    VoiceProvider.VONAGE: {
        "required": (
            "VOICE_E2E_VONAGE_KEY",
            "VOICE_E2E_VONAGE_SECRET",
            "VOICE_E2E_VONAGE_APP_ID",
            "VOICE_E2E_VONAGE_PRIVATE_KEY",
            "VOICE_E2E_VONAGE_FROM",
            "VOICE_E2E_VONAGE_TO",
        ),
        "build": lambda env: {
            "credentials": {
                "api_key": env["VOICE_E2E_VONAGE_KEY"],
                "api_secret": env["VOICE_E2E_VONAGE_SECRET"],
                "application_id": env["VOICE_E2E_VONAGE_APP_ID"],
                "private_key_pem": env["VOICE_E2E_VONAGE_PRIVATE_KEY"],
                "signature_secret": env.get("VOICE_E2E_VONAGE_SIG_SECRET", ""),
            },
            "from_numbers": [env["VOICE_E2E_VONAGE_FROM"]],
            "to_number": env["VOICE_E2E_VONAGE_TO"],
        },
    },
    VoiceProvider.TELNYX: {
        "required": (
            "VOICE_E2E_TELNYX_API_KEY",
            "VOICE_E2E_TELNYX_CONNECTION_ID",
            "VOICE_E2E_TELNYX_FROM",
            "VOICE_E2E_TELNYX_TO",
        ),
        "build": lambda env: {
            "credentials": {
                "api_key": env["VOICE_E2E_TELNYX_API_KEY"],
                "connection_id": env["VOICE_E2E_TELNYX_CONNECTION_ID"],
                "outbound_voice_profile_id": env.get("VOICE_E2E_TELNYX_PROFILE_ID", ""),
                "public_key": env.get("VOICE_E2E_TELNYX_PUBLIC_KEY", ""),
            },
            "from_numbers": [env["VOICE_E2E_TELNYX_FROM"]],
            "to_number": env["VOICE_E2E_TELNYX_TO"],
        },
    },
    VoiceProvider.EXOTEL: {
        "required": (
            "VOICE_E2E_EXOTEL_SID",
            "VOICE_E2E_EXOTEL_KEY",
            "VOICE_E2E_EXOTEL_TOKEN",
            "VOICE_E2E_EXOTEL_FROM",
            "VOICE_E2E_EXOTEL_TO",
        ),
        "build": lambda env: {
            "credentials": {
                "sid": env["VOICE_E2E_EXOTEL_SID"],
                "api_key": env["VOICE_E2E_EXOTEL_KEY"],
                "api_token": env["VOICE_E2E_EXOTEL_TOKEN"],
                "subdomain": env.get("VOICE_E2E_EXOTEL_SUBDOMAIN", "api.exotel.com"),
            },
            "from_numbers": [env["VOICE_E2E_EXOTEL_FROM"]],
            "to_number": env["VOICE_E2E_EXOTEL_TO"],
            "inbound_webhook_token": env.get("VOICE_E2E_EXOTEL_WEBHOOK_TOKEN", "e2e-token"),
        },
    },
}


def _resolve_creds(provider: VoiceProvider):
    """Return ``(creds_dict, to_number, inbound_token)`` or ``None`` if
    any required env var is missing for ``provider``.
    """
    spec = PROVIDER_CREDS[provider]
    env = os.environ
    missing = [k for k in spec["required"] if not env.get(k)]
    if missing:
        return None
    built = spec["build"](env)
    return built


# ─────────────────────────────────────────────────────────────────────────────
# Polling helpers
# ─────────────────────────────────────────────────────────────────────────────


def _poll_until_terminal(call_id: uuid.UUID, *, timeout: float = TERMINAL_TIMEOUT_SECONDS) -> VoiceCall:
    """Re-read ``call_id`` until ``status in TERMINAL_STATUSES`` or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        call = VoiceCall.objects.get(pk=call_id)
        if call.status in TERMINAL_STATUSES:
            return call
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"Call {call_id} did not reach terminal status within {timeout}s "
        f"(last seen: {VoiceCall.objects.get(pk=call_id).status})"
    )


def _poll_until_recording(call_id: uuid.UUID, *, timeout: float = RECORDING_TIMEOUT_SECONDS) -> VoiceRecording:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = VoiceRecording.objects.filter(call_id=call_id).exclude(storage_url="").first()
        if rec is not None:
            return rec
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"No recording observed for call {call_id} within {timeout}s")


def _poll_until_transcript(recording_id: uuid.UUID, *, timeout: float = TRANSCRIPTION_TIMEOUT_SECONDS) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = VoiceRecording.objects.get(pk=recording_id)
        if rec.transcription:
            return rec.transcription
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"Recording {recording_id} not transcribed within {timeout}s")


# ─────────────────────────────────────────────────────────────────────────────
# Base test case
# ─────────────────────────────────────────────────────────────────────────────


class _ProviderE2ECase(TransactionTestCase):
    """Per-provider scaffold.

    Subclasses set ``provider`` and inherit the lifecycle / recording
    / transcription tests. Using ``TransactionTestCase`` so the
    Celery-driven status writes from real webhooks land in the DB the
    poller can see; the standard ``TestCase`` rolls the txn back at
    the end of the test, hiding the worker writes.
    """

    provider: VoiceProvider = None  # type: ignore[assignment]
    recording_supported: bool = True
    transcription_supported: bool = True

    def setUp(self):
        super().setUp()
        if self.provider is None:
            self.skipTest("base class — set ``provider`` in the subclass")

        creds = _resolve_creds(self.provider)
        if creds is None:
            self.skipTest(f"{self.provider} sandbox creds not configured; skipping E2E")
        self.creds_payload = creds

        self.tenant = Tenant.objects.create(name=f"E2E {self.provider} Tenant {uuid.uuid4().hex[:6]}")
        self.addCleanup(self.tenant.delete)

        TenantVoiceApp.objects.create(tenant=self.tenant, is_enabled=True)

        cfg_kwargs = {
            "tenant": self.tenant,
            "name": f"E2E {self.provider}",
            "provider": self.provider.value,
            "credentials": json.dumps(creds["credentials"]),
            "from_numbers": creds["from_numbers"],
            "recording_enabled": self.recording_supported,
        }
        if "inbound_webhook_token" in creds:
            cfg_kwargs["inbound_webhook_token"] = creds["inbound_webhook_token"]

        self.config = VoiceProviderConfig.objects.create(**cfg_kwargs)
        TenantVoiceApp.objects.filter(tenant=self.tenant).update(default_outbound_config=self.config)

        self.contact = TenantContact.objects.create(
            tenant=self.tenant,
            phone=creds["to_number"],
            first_name="E2E",
        )

    # ── lifecycle ───────────────────────────────────────────────────────

    def _initiate_call(self, *, tts_text="Hello from Jina Connect E2E test."):
        from voice.constants import CallDirection
        from voice.tasks import initiate_call

        call = VoiceCall.objects.create(
            tenant=self.tenant,
            name=f"e2e-{self.provider}",
            provider_config=self.config,
            provider_call_id=f"pending-{uuid.uuid4().hex[:8]}",
            direction=CallDirection.OUTBOUND,
            from_number=str(self.config.from_numbers[0]),
            to_number=self.creds_payload["to_number"],
            contact=self.contact,
            status=CallStatus.QUEUED,
            metadata={"static_play": {"tts_text": tts_text}},
        )
        self.addCleanup(lambda: VoiceCall.objects.filter(pk=call.pk).delete())
        # Run synchronously so test thread observes provider_call_id
        # immediately. ``initiate_call`` is a Celery task; calling the
        # underlying function directly bypasses the broker.
        initiate_call(str(call.id))
        call.refresh_from_db()
        return call

    # ── tests ───────────────────────────────────────────────────────────

    def test_outbound_call_lifecycle(self):
        call = self._initiate_call()
        self.assertNotEqual(
            call.provider_call_id.startswith("pending-"),
            True,
            f"adapter never replaced placeholder provider_call_id ({call.provider_call_id})",
        )

        call = _poll_until_terminal(call.id)
        self.assertIn(call.status, TERMINAL_STATUSES)

        events = list(VoiceCallEvent.objects.filter(call=call).order_by("sequence"))
        self.assertGreater(len(events), 0, "no VoiceCallEvent rows landed for the call")
        # Sequence numbers must be monotonic per call.
        for prev, cur in zip(events, events[1:]):
            self.assertLess(prev.sequence, cur.sequence)

    def test_recording(self):
        if not self.recording_supported:
            self.skipTest(f"{self.provider} test config has recording disabled")

        call = self._initiate_call(tts_text="Please record this for the E2E test.")
        _poll_until_terminal(call.id)

        recording = _poll_until_recording(call.id)
        self.assertTrue(recording.storage_url, "recording.storage_url empty after webhook")

        # Signed-URL fetch — the bucket key isn't useful on its own.
        from voice.recordings import storage

        try:
            signed = storage.signed_url(recording.storage_url, expires_seconds=300)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"signed_url raised: {exc}")
        self.assertTrue(signed.startswith(("http://", "https://")))

    def test_transcription(self):
        if not self.transcription_supported:
            self.skipTest(f"{self.provider} transcription test disabled")
        if not os.environ.get("VOICE_TRANSCRIPTION_PROVIDER"):
            self.skipTest("VOICE_TRANSCRIPTION_PROVIDER not configured; skipping transcript poll")

        call = self._initiate_call(tts_text="Transcribe this E2E utterance.")
        _poll_until_terminal(call.id)
        recording = _poll_until_recording(call.id)
        text = _poll_until_transcript(recording.id)
        self.assertGreater(len(text.strip()), 0)


# ─────────────────────────────────────────────────────────────────────────────
# Per-provider subclasses
# ─────────────────────────────────────────────────────────────────────────────


class TestTwilioE2E(_ProviderE2ECase):
    provider = VoiceProvider.TWILIO


class TestPlivoE2E(_ProviderE2ECase):
    provider = VoiceProvider.PLIVO


class TestVonageE2E(_ProviderE2ECase):
    provider = VoiceProvider.VONAGE


class TestTelnyxE2E(_ProviderE2ECase):
    provider = VoiceProvider.TELNYX


class TestExotelE2E(_ProviderE2ECase):
    provider = VoiceProvider.EXOTEL
    # Exotel doesn't expose mid-call control through the same API
    # shape, and the test number setup needs the customer's portal.
    # Recording often requires a separate flow id, so leave it off by
    # default and let the suite pass when the env doesn't include the
    # recording-enabled flow.
    recording_supported = False
    transcription_supported = False


# ─────────────────────────────────────────────────────────────────────────────
# Inbound — only when the sandbox can drive an inbound webhook
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (E2E_ENABLED and os.environ.get("VOICE_E2E_INBOUND_PROVIDER")),
    reason="Inbound E2E requires VOICE_E2E_INBOUND_PROVIDER set to a configured provider name.",
)
class TestInboundE2E(TransactionTestCase):
    """Inbound is opt-in per run because the sandbox typically needs a
    human to dial in or to trigger a "send test" button. The provider
    name comes from ``VOICE_E2E_INBOUND_PROVIDER`` so CI can target a
    single sandbox at a time."""

    def test_inbound_call_creates_voice_call_and_inbox_row(self):
        from team_inbox.models import MessagePlatformChoices, Messages

        provider_name = os.environ["VOICE_E2E_INBOUND_PROVIDER"].lower()
        # Wait up to 90s for *any* inbound VoiceCall row to land for
        # the provider — the test runner is responsible for triggering
        # the call out-of-band.
        deadline = time.monotonic() + TERMINAL_TIMEOUT_SECONDS
        call = None
        while time.monotonic() < deadline:
            call = (
                VoiceCall.objects.filter(
                    direction="inbound",
                    provider_config__provider=provider_name,
                )
                .order_by("-created_at")
                .first()
            )
            if call and call.status in TERMINAL_STATUSES:
                break
            time.sleep(POLL_INTERVAL_SECONDS)

        self.assertIsNotNone(call, "no inbound VoiceCall observed within the timeout")
        self.assertEqual(call.direction, "inbound")

        # An inbox row should follow via the call_completed signal.
        msg = (
            Messages.objects.filter(tenant=call.tenant, platform=MessagePlatformChoices.VOICE)
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(msg, "no team_inbox.Messages row for the inbound call")


# ─────────────────────────────────────────────────────────────────────────────
# SIP outbound — separate flag because it requires Asterisk reachable
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (E2E_ENABLED and SIP_E2E_ENABLED),
    reason="SIP E2E requires VOICE_E2E_SIP_TESTS=1 alongside an Asterisk ARI endpoint.",
)
class TestSIPE2E(TransactionTestCase):
    def test_sip_outbound_call_reaches_terminal(self):
        sip_creds = {
            "sip_username": os.environ.get("VOICE_E2E_SIP_USERNAME", ""),
            "sip_password": os.environ.get("VOICE_E2E_SIP_PASSWORD", ""),
            "sip_realm": os.environ.get("VOICE_E2E_SIP_REALM", ""),
            "sip_proxy": os.environ.get("VOICE_E2E_SIP_PROXY", ""),
            "sip_transport": "udp",
            "vendor_profile": os.environ.get("VOICE_E2E_SIP_PROFILE", "generic"),
        }
        if not all(sip_creds[k] for k in ("sip_username", "sip_password", "sip_realm", "sip_proxy")):
            self.skipTest("SIP sandbox creds missing.")

        from voice.constants import CallDirection
        from voice.tasks import initiate_call

        tenant = Tenant.objects.create(name=f"E2E SIP Tenant {uuid.uuid4().hex[:6]}")
        self.addCleanup(tenant.delete)
        TenantVoiceApp.objects.create(tenant=tenant, is_enabled=True)
        cfg = VoiceProviderConfig.objects.create(
            tenant=tenant,
            name="E2E SIP",
            provider=VoiceProvider.SIP.value,
            credentials=json.dumps(sip_creds),
            from_numbers=[os.environ["VOICE_E2E_SIP_FROM"]],
        )
        contact = TenantContact.objects.create(
            tenant=tenant, phone=os.environ["VOICE_E2E_SIP_TO"], first_name="E2E SIP"
        )
        call = VoiceCall.objects.create(
            tenant=tenant,
            name="e2e-sip",
            provider_config=cfg,
            provider_call_id=f"pending-{uuid.uuid4().hex[:8]}",
            direction=CallDirection.OUTBOUND,
            from_number=os.environ["VOICE_E2E_SIP_FROM"],
            to_number=os.environ["VOICE_E2E_SIP_TO"],
            contact=contact,
            status=CallStatus.QUEUED,
            metadata={"static_play": {"tts_text": "SIP E2E"}},
        )
        self.addCleanup(lambda: VoiceCall.objects.filter(pk=call.pk).delete())
        initiate_call(str(call.id))
        call = _poll_until_terminal(call.id)
        self.assertIn(call.status, TERMINAL_STATUSES)
