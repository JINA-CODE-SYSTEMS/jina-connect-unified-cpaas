# Voice (Calls, IVR) — Implementation Plan

> **Status:** 🚧 Ready for implementation  
> **Depends on:** WhatsApp ✅, SMS ✅, RCS ✅, Telegram ✅  
> **Completes:** Four-channel foundation (WhatsApp + SMS + RCS + Voice)

---

## Executive Summary

Voice channel with SIP trunking and basic IVR flow support. Two provider adapters (SIP trunk via Twilio + generic SIP) covering outbound calling, inbound routing, DTMF menus, call recording, voicemail, and real-time status tracking. Plugs into broadcast (bulk calling campaigns), team inbox (call logs + recordings in timeline), and chat flow builder (IVR flow design).

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    Channel Registry                           │
│  register_channel("VOICE", _voice_adapter_factory)           │
│  get_channel_adapter("VOICE", tenant) → VoiceCallManager     │
└──────────────┬───────────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────────┐
│              VoiceCallManager (BaseChannelAdapter)            │
│  send_text() → TTS outbound call                             │
│  send_media() → play audio file                              │
│  send_keyboard() → IVR menu with DTMF                       │
│  make_call() / transfer_call() / end_call()                  │
└──────────┬──────────────────────────────┬────────────────────┘
           │                              │
┌──────────▼──────────┐    ┌──────────────▼────────────────────┐
│  TwilioVoiceProvider │    │  SIPTrunkProvider                 │
│  (Twilio Voice API)  │    │  (Generic SIP / Plivo / Exotel)  │
└──────────────────────┘    └──────────────────────────────────┘
```

---

## Phase 1 — Core Plumbing & Provider Adapters

**Goal:** Django app `voice/`, models, migrations, two providers, webhook ingestion, call status tracking.

### 1.1 Django App Scaffolding

Create `voice/` app mirroring the RCS/SMS pattern:

```
voice/
├── __init__.py
├── admin.py
├── apps.py                   # register_channel("VOICE", factory)
├── constants.py              # Provider/status/event choices
├── cron.py                   # Reset daily call counters
├── models.py                 # VoiceApp, VoiceCall, VoiceWebhookEvent, IVRMenu
├── serializers.py
├── signals.py                # post_save → process_voice_event_task
├── tasks.py                  # Celery tasks for event processing
├── urls.py
├── views.py                  # Webhook endpoints
├── migrations/
│   └── 0001_initial.py
├── providers/
│   ├── __init__.py           # get_voice_provider() registry
│   ├── base.py               # BaseVoiceProvider ABC + data classes
│   ├── twilio_voice.py       # Twilio Voice implementation
│   └── sip_trunk.py          # Generic SIP trunk (Plivo/Exotel/custom)
├── services/
│   ├── call_manager.py       # VoiceCallManager (BaseChannelAdapter)
│   ├── ivr_builder.py        # IVR menu/flow builder utilities
│   ├── recording_manager.py  # Call recording storage & retrieval
│   ├── dtmf_handler.py       # DTMF input processing
│   └── rate_limiter.py       # Per-app call rate limiting
├── viewsets/
│   ├── voice_app.py          # CRUD for VoiceApp config
│   ├── voice_call.py         # Call log listing & detail
│   └── ivr_menu.py           # IVR menu CRUD
└── tests/
    ├── __init__.py
    ├── test_models.py
    ├── test_call_manager.py
    ├── test_twilio_provider.py
    ├── test_sip_provider.py
    ├── test_webhook_views.py
    ├── test_ivr_builder.py
    ├── test_dtmf_handler.py
    └── test_broadcast_handler.py
```

### 1.2 Models

#### `VoiceApp` — Per-tenant voice configuration

```python
class VoiceApp(BaseTenantModelForFilterUser):
    """Per-tenant voice channel configuration."""
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="voice_apps")
    name = models.CharField(max_length=255)
    provider = models.CharField(max_length=30, choices=VoiceProviderChoices.choices)
    is_active = models.BooleanField(default=True)

    # SIP / Twilio credentials (encrypted at rest)
    provider_credentials = EncryptedTextField(blank=True, default="")

    # Caller ID / trunk config
    caller_id = models.CharField(max_length=20, help_text="E.164 caller ID or SIP trunk number")
    sip_domain = models.CharField(max_length=255, blank=True, default="", help_text="SIP trunk domain")
    sip_username = EncryptedTextField(blank=True, default="")
    sip_password = EncryptedTextField(blank=True, default="")

    # Webhook
    webhook_url = models.URLField(blank=True, default="")
    webhook_secret = EncryptedTextField(blank=True, default="")

    # Recording settings
    recording_enabled = models.BooleanField(default=False)
    recording_storage = models.CharField(
        max_length=20, choices=[("LOCAL", "Local"), ("S3", "S3"), ("GCS", "GCS")],
        default="S3",
    )
    voicemail_enabled = models.BooleanField(default=False)
    voicemail_greeting_url = models.URLField(blank=True, default="")

    # Limits & pricing
    daily_limit = models.PositiveIntegerField(default=1000)
    calls_made_today = models.PositiveIntegerField(default=0)
    concurrent_call_limit = models.PositiveSmallIntegerField(default=10)
    price_per_call = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    price_per_minute = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    # TTS defaults
    default_tts_voice = models.CharField(max_length=50, default="en-US-Standard-C")
    default_tts_language = models.CharField(max_length=10, default="en-US")

    class Meta:
        unique_together = ("tenant", "name")

    def increment_daily_counter(self) -> bool:
        """Atomic daily limit check. Returns False if limit reached."""
        from django.db.models import F
        updated = VoiceApp.objects.filter(
            pk=self.pk,
            calls_made_today__lt=F("daily_limit"),
        ).update(calls_made_today=F("calls_made_today") + 1)
        return updated > 0
```

#### `VoiceCall` — Individual call tracking

```python
class VoiceCall(BaseTenantModelForFilterUser):
    """Tracks every inbound and outbound voice call."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voice_app = models.ForeignKey(VoiceApp, on_delete=models.CASCADE, related_name="calls")
    contact = models.ForeignKey("contacts.TenantContact", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="voice_calls")

    # Call identifiers
    provider_call_id = models.CharField(max_length=255, db_index=True, help_text="Provider-side SID/ID")
    direction = models.CharField(max_length=10, choices=CallDirectionChoices.choices)
    from_number = models.CharField(max_length=20)
    to_number = models.CharField(max_length=20)

    # Status tracking
    status = models.CharField(max_length=20, choices=CallStatusChoices.choices, default="QUEUED")

    # Timestamps
    initiated_at = models.DateTimeField(auto_now_add=True)
    ringing_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0, help_text="Total call duration in seconds")
    ring_duration_seconds = models.PositiveIntegerField(default=0)

    # Recording
    recording_url = models.URLField(blank=True, default="")
    recording_duration_seconds = models.PositiveIntegerField(default=0)
    recording_storage_path = models.CharField(max_length=512, blank=True, default="")

    # Voicemail
    voicemail_url = models.URLField(blank=True, default="")
    voicemail_duration_seconds = models.PositiveIntegerField(default=0)
    voicemail_transcription = models.TextField(blank=True, default="")

    # DTMF
    dtmf_digits = models.CharField(max_length=50, blank=True, default="",
                                    help_text="Accumulated DTMF input during call")

    # IVR tracking
    ivr_menu = models.ForeignKey("IVRMenu", on_delete=models.SET_NULL, null=True, blank=True)
    ivr_path = models.JSONField(default=list, help_text="List of IVR nodes traversed")

    # Cost
    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    # Error
    error_code = models.CharField(max_length=50, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    # Broadcast link
    broadcast_message = models.ForeignKey(
        "broadcast.BroadcastMessage", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="voice_calls",
    )

    # Team Inbox link
    inbox_message = models.ForeignKey(
        "team_inbox.Messages", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="voice_call_entries",
    )

    # Raw provider payload
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-initiated_at"]
        indexes = [
            models.Index(fields=["voice_app", "status"]),
            models.Index(fields=["provider_call_id"]),
            models.Index(fields=["contact", "-initiated_at"]),
        ]
```

#### `VoiceWebhookEvent` — Raw webhook storage + idempotency

```python
class VoiceWebhookEvent(models.Model):
    """Raw webhook events from voice providers for idempotent processing."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voice_app = models.ForeignKey(VoiceApp, on_delete=models.CASCADE, related_name="webhook_events")
    provider_event_id = models.CharField(max_length=255, db_index=True)
    event_type = models.CharField(max_length=30, choices=VoiceEventTypeChoices.choices)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("voice_app", "provider_event_id", "event_type")
```

#### `IVRMenu` — IVR flow definition

```python
class IVRMenu(BaseTenantModelForFilterUser):
    """Defines an IVR menu tree with DTMF options."""
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="ivr_menus")
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    # Root greeting
    greeting_text = models.TextField(help_text="TTS text or SSML for the greeting prompt")
    greeting_audio_url = models.URLField(blank=True, default="", help_text="Pre-recorded greeting")

    # IVR tree structure (JSON)
    # Format: { "options": { "1": { "action": "transfer", "target": "+91...", "label": "Sales" },
    #                        "2": { "action": "submenu", "menu_id": "<uuid>", "label": "Support" },
    #                        "0": { "action": "repeat" },
    #                        "*": { "action": "voicemail" } } }
    menu_tree = models.JSONField(default=dict, help_text="DTMF option → action mapping")

    # Timeout / invalid input handling
    no_input_timeout_seconds = models.PositiveSmallIntegerField(default=5)
    max_retries = models.PositiveSmallIntegerField(default=3)
    invalid_input_text = models.TextField(default="Invalid selection. Please try again.")
    timeout_text = models.TextField(default="We didn't receive your input. Please try again.")

    # Fallback
    fallback_action = models.CharField(
        max_length=20,
        choices=[("VOICEMAIL", "Voicemail"), ("TRANSFER", "Transfer"), ("HANGUP", "Hang up")],
        default="VOICEMAIL",
    )
    fallback_target = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        unique_together = ("tenant", "name")
```

### 1.3 Constants

```python
# voice/constants.py

from django.db import models


class VoiceProviderChoices(models.TextChoices):
    TWILIO = "TWILIO", "Twilio Voice"
    SIP_TRUNK = "SIP_TRUNK", "SIP Trunk (Generic)"
    PLIVO = "PLIVO", "Plivo"
    EXOTEL = "EXOTEL", "Exotel"
    VONAGE = "VONAGE", "Vonage"
    TELNYX = "TELNYX", "Telnyx"


class CallDirectionChoices(models.TextChoices):
    INBOUND = "INBOUND", "Inbound"
    OUTBOUND = "OUTBOUND", "Outbound"


class CallStatusChoices(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    INITIATED = "INITIATED", "Initiated"
    RINGING = "RINGING", "Ringing"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"  # answered
    COMPLETED = "COMPLETED", "Completed"
    BUSY = "BUSY", "Busy"
    NO_ANSWER = "NO_ANSWER", "No Answer"
    FAILED = "FAILED", "Failed"
    CANCELED = "CANCELED", "Canceled"
    VOICEMAIL = "VOICEMAIL", "Voicemail"


class VoiceEventTypeChoices(models.TextChoices):
    CALL_INITIATED = "CALL_INITIATED", "Call Initiated"
    CALL_RINGING = "CALL_RINGING", "Call Ringing"
    CALL_ANSWERED = "CALL_ANSWERED", "Call Answered"
    CALL_COMPLETED = "CALL_COMPLETED", "Call Completed"
    CALL_FAILED = "CALL_FAILED", "Call Failed"
    DTMF_RECEIVED = "DTMF_RECEIVED", "DTMF Received"
    RECORDING_READY = "RECORDING_READY", "Recording Ready"
    VOICEMAIL_RECEIVED = "VOICEMAIL_RECEIVED", "Voicemail Received"
    TRANSFER_INITIATED = "TRANSFER_INITIATED", "Transfer Initiated"
    TRANSFER_COMPLETED = "TRANSFER_COMPLETED", "Transfer Completed"


class IVRActionChoices(models.TextChoices):
    PLAY = "PLAY", "Play Audio"
    SAY = "SAY", "Text-to-Speech"
    GATHER = "GATHER", "Gather DTMF"
    TRANSFER = "TRANSFER", "Transfer Call"
    SUBMENU = "SUBMENU", "Go to Submenu"
    VOICEMAIL = "VOICEMAIL", "Send to Voicemail"
    HANGUP = "HANGUP", "Hang Up"
    REPEAT = "REPEAT", "Repeat Menu"
    RECORD = "RECORD", "Record Message"
    WEBHOOK = "WEBHOOK", "Call External Webhook"


# Twilio status → internal status mapping
TWILIO_STATUS_MAP = {
    "queued": CallStatusChoices.QUEUED,
    "initiated": CallStatusChoices.INITIATED,
    "ringing": CallStatusChoices.RINGING,
    "in-progress": CallStatusChoices.IN_PROGRESS,
    "completed": CallStatusChoices.COMPLETED,
    "busy": CallStatusChoices.BUSY,
    "no-answer": CallStatusChoices.NO_ANSWER,
    "failed": CallStatusChoices.FAILED,
    "canceled": CallStatusChoices.CANCELED,
}
```

### 1.4 Provider Base (ABC + Data Classes)

```python
# voice/providers/base.py

from __future__ import annotations
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CallResult:
    """Uniform result from all voice call operations."""
    success: bool
    provider: str
    call_id: Optional[str] = None
    status: str = "QUEUED"
    cost: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class CallStatusReport:
    """Normalized call status update from any provider."""
    call_id: str
    status: str
    duration_seconds: int = 0
    direction: str = "OUTBOUND"
    from_number: str = ""
    to_number: str = ""
    answered_by: Optional[str] = None  # "human", "machine", None
    timestamp: Optional[str] = None
    raw_payload: Optional[Dict] = None


@dataclass
class DTMFInput:
    """Normalized DTMF input from any provider."""
    call_id: str
    digits: str
    finished_on_key: Optional[str] = None  # "#", "*", "timeout"
    raw_payload: Optional[Dict] = None


@dataclass
class RecordingResult:
    """Normalized recording result."""
    call_id: str
    recording_id: str
    recording_url: str
    duration_seconds: int = 0
    raw_payload: Optional[Dict] = None


@dataclass
class TwiMLResponse:
    """Represents a TwiML/SIP response for call control."""
    xml: str  # Raw TwiML/SIP XML
    content_type: str = "application/xml"


class BaseVoiceProvider(ABC):
    """Abstract base for all voice providers."""

    PROVIDER_NAME: str = "base"

    def __init__(self, voice_app):
        self.voice_app = voice_app
        raw = voice_app.provider_credentials or "{}"
        if isinstance(raw, str):
            try:
                self.credentials = json.loads(raw)
            except (ValueError, TypeError):
                self.credentials = {}
        else:
            self.credentials = raw or {}

    # ── Outbound ──────────────────────────────────────────────
    @abstractmethod
    def make_call(
        self, to: str, from_: str, *,
        tts_text: Optional[str] = None,
        audio_url: Optional[str] = None,
        ivr_menu: Optional[Dict] = None,
        record: bool = False,
        machine_detection: bool = False,
        timeout: int = 30,
        **kwargs,
    ) -> CallResult:
        """Initiate an outbound call."""

    @abstractmethod
    def end_call(self, call_id: str) -> CallResult:
        """Hang up an active call."""

    @abstractmethod
    def transfer_call(self, call_id: str, to: str, *, announce_text: Optional[str] = None) -> CallResult:
        """Transfer/forward an active call to another number."""

    # ── IVR / DTMF ───────────────────────────────────────────
    @abstractmethod
    def build_ivr_response(self, menu: Dict, *, gather_timeout: int = 5) -> TwiMLResponse:
        """Build provider-specific IVR response (TwiML, etc.)."""

    @abstractmethod
    def build_say_response(self, text: str, *, voice: str = "alice", language: str = "en-US") -> TwiMLResponse:
        """Build a TTS response."""

    @abstractmethod
    def build_play_response(self, audio_url: str) -> TwiMLResponse:
        """Build an audio playback response."""

    # ── Recording ─────────────────────────────────────────────
    @abstractmethod
    def get_recording(self, recording_id: str) -> RecordingResult:
        """Fetch recording metadata and URL."""

    @abstractmethod
    def delete_recording(self, recording_id: str) -> bool:
        """Delete a recording from the provider."""

    # ── Webhook parsing ───────────────────────────────────────
    @abstractmethod
    def parse_status_webhook(self, payload: Dict) -> CallStatusReport:
        """Parse a call status webhook into normalized form."""

    @abstractmethod
    def parse_dtmf_webhook(self, payload: Dict) -> DTMFInput:
        """Parse DTMF input from webhook."""

    @abstractmethod
    def parse_recording_webhook(self, payload: Dict) -> RecordingResult:
        """Parse recording-ready webhook."""

    @abstractmethod
    def validate_webhook_signature(self, request) -> bool:
        """Verify that the webhook came from the voice provider."""
```

### 1.5 Provider: Twilio Voice

```python
# voice/providers/twilio_voice.py
# Uses: twilio Python SDK (twilio>=9.0.0)
#
# Key operations:
#   make_call()  → client.calls.create(to, from_, twiml=..., status_callback=...)
#   end_call()   → client.calls(sid).update(status="completed")
#   transfer()   → TwiML <Dial><Number>...</Number></Dial>
#   IVR          → TwiML <Gather numDigits="1" action="/dtmf-handler"><Say>...</Say></Gather>
#   Recording    → twiml <Record> or call.create(record=True)
#   Webhook      → Twilio sends POST to status_callback URL on each status change
#   Signature    → twilio.request_validator.validate(url, params, signature)
#
# Answering machine detection:
#   client.calls.create(..., machine_detection="DetectMessageEnd")
#   → async webhook with AnsweredBy: "human" | "machine_start" | "machine_end_beep"
```

### 1.6 Provider: SIP Trunk (Generic)

```python
# voice/providers/sip_trunk.py
# Supports: Plivo, Exotel, Vonage, Telnyx, or raw SIP trunk
#
# Strategy: Provider sub-selection via voice_app.provider field
#   "PLIVO"   → Plivo REST API (plivo-python)
#   "EXOTEL"  → Exotel API (requests-based)
#   "VONAGE"  → Vonage Voice API (vonage>=3.0)
#   "TELNYX"  → Telnyx Voice API (telnyx>=2.0)
#   "SIP_TRUNK" → Raw SIP INVITE via pjsua2 or Obit SIP (future)
#
# All share the same BaseVoiceProvider interface.
# Phase 1 implements Twilio + Plivo as the two concrete providers.
# Exotel/Vonage/Telnyx/raw-SIP can be added incrementally.
```

### 1.7 Webhook Views

```python
# voice/views.py — Two webhook endpoints per provider pattern

class TwilioVoiceWebhookView(APIView):
    """Handles Twilio voice status callbacks and IVR gather results."""
    # POST /voice/v1/webhooks/twilio/<uuid:voice_app_id>/
    # 1. Validate X-Twilio-Signature
    # 2. Store VoiceWebhookEvent (idempotent via unique_together)
    # 3. post_save signal → process_voice_event_task.delay()

class TwilioDTMFWebhookView(APIView):
    """Handles Twilio <Gather> DTMF results — returns TwiML for next step."""
    # POST /voice/v1/webhooks/twilio/<uuid:voice_app_id>/dtmf/
    # 1. Parse Digits from POST body
    # 2. Look up IVR menu → resolve next action
    # 3. Return TwiML (Say/Play/Gather/Dial/Hangup)

class SIPTrunkWebhookView(APIView):
    """Handles generic SIP / Plivo / Exotel status webhooks."""
    # POST /voice/v1/webhooks/sip/<uuid:voice_app_id>/
```

### 1.8 URL Configuration

```python
# voice/urls.py
urlpatterns = [
    # Webhooks
    path("v1/webhooks/twilio/<uuid:voice_app_id>/", TwilioVoiceWebhookView.as_view()),
    path("v1/webhooks/twilio/<uuid:voice_app_id>/dtmf/", TwilioDTMFWebhookView.as_view()),
    path("v1/webhooks/sip/<uuid:voice_app_id>/", SIPTrunkWebhookView.as_view()),
    # REST API
    path("", include(router.urls)),
]

# jina_connect/urls.py — add:
path("voice/", include(("voice.urls", "voice"), namespace="voice")),
```

### 1.9 Signals & Tasks

```python
# voice/signals.py — same pattern as RCS
@receiver(post_save, sender=VoiceWebhookEvent)
def queue_voice_event_processing(sender, instance, created, **kwargs):
    if created:
        from voice.tasks import process_voice_event_task
        process_voice_event_task.delay(str(instance.pk))

# voice/tasks.py
@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def process_voice_event_task(self, event_id: str):
    """Route voice webhook events to handlers."""
    # CALL_INITIATED / CALL_RINGING / CALL_ANSWERED → _handle_status_update
    # CALL_COMPLETED → _handle_call_completed (update duration, cost, contact.total_calls)
    # DTMF_RECEIVED → _handle_dtmf (IVR routing)
    # RECORDING_READY → _handle_recording (download & store)
    # VOICEMAIL_RECEIVED → _handle_voicemail

@shared_task(bind=True, max_retries=3)
def initiate_outbound_call_task(self, voice_call_id: str):
    """Async call initiation — used by broadcast and direct API."""

@shared_task
def download_recording_task(recording_url: str, voice_call_id: str):
    """Download recording from provider and store in S3/GCS."""
```

### 1.10 Cron Jobs

```python
# voice/cron.py
def reset_daily_voice_counters():
    """Reset calls_made_today for all active VoiceApps. Run at midnight."""
    from voice.models import VoiceApp
    VoiceApp.objects.filter(is_active=True).update(calls_made_today=0)

# settings.py CRONJOBS addition:
# ("0 0 * * *", "voice.cron.reset_daily_voice_counters"),
```

### 1.11 Dependencies

Add to `requirements.txt`:
```
twilio>=9.0.0
plivo>=4.50.0
```

### 1.12 Settings

Add to `INSTALLED_APPS`:
```python
"voice",
```

---

## Phase 2 — Product Integration

**Goal:** Wire voice into broadcast, team inbox, chat flow, tenants, contacts, and pricing.

### 2.1 Broadcast Integration

#### `broadcast/models.py`
```python
# Add VOICE to BroadcastPlatformChoices
class BroadcastPlatformChoices(models.TextChoices):
    WHATSAPP = "WHATSAPP", "WhatsApp"
    TELEGRAM = "TELEGRAM", "Telegram"
    SMS = "SMS", "SMS"
    RCS = "RCS", "RCS"
    VOICE = "VOICE", "Voice"  # ← NEW

# Add pricing method
def _get_voice_message_price(self) -> Decimal:
    from voice.models import VoiceApp
    voice_app = VoiceApp.objects.filter(tenant=self.tenant, is_active=True).first()
    if not voice_app:
        return Decimal("0")
    return Decimal(str(voice_app.price_per_call or 0))

# Update get_message_price() routing:
elif self.platform == BroadcastPlatformChoices.VOICE:
    return self._get_voice_message_price()
```

#### `broadcast/tasks.py`
```python
def handle_voice_message(message):
    """Handle voice broadcast — initiate outbound call per recipient."""
    from voice.models import VoiceApp
    from voice.services.call_manager import VoiceCallManager

    tenant = message.broadcast.tenant
    contact = message.contact

    voice_app = VoiceApp.objects.filter(tenant=tenant, is_active=True).first()
    if not voice_app:
        return {"success": False, "error": f"No active VoiceApp for tenant {tenant.pk}"}

    if not contact.phone:
        return {"success": False, "error": f"Contact {contact.pk} has no phone number"}

    # DNC check
    if contact.dnc:
        return {"success": False, "error": f"Contact {contact.pk} is on Do Not Call list"}

    manager = VoiceCallManager(voice_app)
    data = message.broadcast.placeholder_data or {}
    tts_text = message.rendered_content or data.get("message") or data.get("tts_text", "")
    audio_url = data.get("audio_url")
    ivr_menu_id = data.get("ivr_menu_id")

    result = manager.make_call(
        to=str(contact.phone),
        tts_text=tts_text or None,
        audio_url=audio_url or None,
        ivr_menu_id=ivr_menu_id,
        contact=contact,
        broadcast_message=message,
    )
    return result

# Add to _PLATFORM_HANDLERS:
_PLATFORM_HANDLERS["VOICE"] = handle_voice_message
```

### 2.2 Team Inbox Integration

`MessagePlatformChoices.VOICE` already exists. The task event handlers will create inbox entries:

```python
# In voice/tasks.py → _handle_call_completed()
def _handle_call_completed(voice_call):
    """Create team inbox entry for completed call."""
    from team_inbox.models import Messages, MessageEventIds

    content = {
        "type": "voice_call",
        "direction": voice_call.direction,
        "status": voice_call.status,
        "duration_seconds": voice_call.duration_seconds,
        "from": voice_call.from_number,
        "to": voice_call.to_number,
        "recording_url": voice_call.recording_url or None,
        "voicemail_url": voice_call.voicemail_url or None,
        "dtmf_digits": voice_call.dtmf_digits or None,
        "cost": str(voice_call.cost),
    }

    event_id = MessageEventIds.objects.create(
        tenant=voice_call.voice_app.tenant,
        wa_message=None,
    )
    inbox_msg = Messages.objects.create(
        tenant=voice_call.voice_app.tenant,
        message_id=event_id,
        content=content,
        direction="INBOUND" if voice_call.direction == "INBOUND" else "OUTGOING",
        platform="VOICE",
        author="SYSTEM",
        contact=voice_call.contact,
    )
    voice_call.inbox_message = inbox_msg
    voice_call.save(update_fields=["inbox_message"])
```

### 2.3 Chat Flow Integration — IVR Flow Design

#### `chat_flow/constants.py` — Add voice node types
```python
VALID_NODE_TYPES += ("ivr_menu", "dtmf_gather", "play_audio", "call_transfer", "voicemail")
```

#### `chat_flow/services/graph_executor.py` — Add VOICE branch
```python
# ── VOICE branch ──────────────────────────────────────────────
if platform == "VOICE":
    from voice.models import VoiceApp
    from voice.services.call_manager import VoiceCallManager

    voice_app = VoiceApp.objects.filter(tenant=contact.tenant, is_active=True).first()
    if not voice_app:
        result["error"] = "No active VoiceApp found for tenant"
        return result

    manager = VoiceCallManager(voice_app)
    # For IVR flows triggered by chatflow, initiate outbound call with IVR
    send_result = manager.make_call(
        to=str(contact.phone),
        tts_text=user_input,
        contact=contact,
    )

    if send_result.get("success"):
        result["success"] = True
        result["message_id"] = send_result.get("call_id")
    else:
        result["error"] = send_result.get("error") or "Failed to initiate voice call"
```

### 2.4 Tenant Filter Integration

#### `tenants/filters.py`
```python
# Add voice filter branch:
elif value_lower == "voice":
    return queryset.filter(voice_apps__is_active=True).distinct()

# Update "all" filter:
elif value_lower == "all":
    return queryset.filter(
        Q(wa_apps__is_active=True) | Q(sms_apps__is_active=True) |
        Q(telegram_bots__is_active=True) | Q(rcs_apps__is_active=True) |
        Q(voice_apps__is_active=True)
    ).distinct()
```

### 2.5 Tenants BSPChoices — Add Voice Providers

```python
# tenants/models.py
class BSPChoices(models.TextChoices):
    # ... existing ...
    TWILIO_VOICE = "TWILIO_VOICE", "Twilio Voice"
    PLIVO = "PLIVO", "Plivo"
    EXOTEL = "EXOTEL", "Exotel"
```

### 2.6 Contact Integration

Contacts model already has the voice fields (`last_called_at`, `total_calls`, `dnc`, `lead_status`, `lead_score`, `preferred_channel=VOICE`, `source=VOICE`). The call completion handler updates them:

```python
# In voice/tasks.py → _handle_call_completed()
if voice_call.contact:
    contact = voice_call.contact
    contact.total_calls = (contact.total_calls or 0) + 1
    contact.last_called_at = timezone.now()
    contact.save(update_fields=["total_calls", "last_called_at"])
```

---

## Phase 3 — MCP Multi-Channel, Tests & Hardening

### 3.1 MCP Tools

#### `mcp_server/tools/messaging.py` — Add voice tools

```python
# Add "VOICE" to _ALLOWED_CHANNELS
_ALLOWED_CHANNELS = {"WHATSAPP", "TELEGRAM", "SMS", "RCS", "VOICE"}

@mcp.tool()
def make_voice_call(
    api_key: str,
    phone: str,
    tts_text: Optional[str] = None,
    audio_url: Optional[str] = None,
    ivr_menu_id: Optional[str] = None,
    record: bool = False,
) -> dict:
    """Initiate an outbound voice call with TTS, audio playback, or IVR menu.

    Args:
        api_key: Your Jina Connect API key.
        phone: Destination phone number in E.164 format.
        tts_text: Text to speak via TTS when call is answered.
        audio_url: URL of audio file to play when call is answered.
        ivr_menu_id: UUID of an IVR menu to play.
        record: Whether to record the call.
    """

@mcp.tool()
def get_voice_call_status(api_key: str, call_id: str) -> dict:
    """Get the status and details of a voice call.

    Args:
        api_key: Your Jina Connect API key.
        call_id: UUID of the VoiceCall.
    """

@mcp.tool()
def end_voice_call(api_key: str, call_id: str) -> dict:
    """End an active voice call.

    Args:
        api_key: Your Jina Connect API key.
        call_id: UUID of the VoiceCall to end.
    """

@mcp.tool()
def transfer_voice_call(api_key: str, call_id: str, to_phone: str) -> dict:
    """Transfer an active voice call to another phone number.

    Args:
        api_key: Your Jina Connect API key.
        call_id: UUID of the active VoiceCall.
        to_phone: Phone number to transfer to in E.164 format.
    """

@mcp.tool()
def get_call_recording(api_key: str, call_id: str) -> dict:
    """Get the recording URL for a completed voice call.

    Args:
        api_key: Your Jina Connect API key.
        call_id: UUID of the VoiceCall.
    """
```

### 3.2 Channel Adapter (send_message unified dispatch)

```python
# In mcp_server/tools/messaging.py → send_message():
elif channel == "VOICE":
    return _send_voice_message(api_key, phone, text)

def _send_voice_message(api_key: str, phone: str, text: str) -> dict:
    """Initiate a TTS outbound call (used by send_message channel=VOICE)."""
    from voice.models import VoiceApp
    from voice.services.call_manager import VoiceCallManager

    tenant, _ = resolve_tenant(api_key)
    voice_app = VoiceApp.objects.filter(tenant=tenant, is_active=True).first()
    if not voice_app:
        return {"error": "No active VoiceApp configured for this tenant."}

    manager = VoiceCallManager(voice_app)
    result = manager.make_call(to=str(phone), tts_text=text)
    if result.get("success"):
        return {"call_id": result.get("call_id", ""), "status": "QUEUED", "phone": phone, "channel": "VOICE"}
    return {"error": result.get("error", "Failed to initiate voice call.")}
```

### 3.3 Test Suite

| Test File | Coverage |
|---|---|
| `test_models.py` | VoiceApp, VoiceCall, VoiceWebhookEvent, IVRMenu CRUD, daily counter atomicity, DNC field |
| `test_call_manager.py` | `make_call()`, `send_text()` (TTS), `send_keyboard()` (IVR), `end_call()`, `transfer_call()`, daily limit, rate limit, DNC block |
| `test_twilio_provider.py` | `make_call()` → Twilio API mock, `end_call()`, `transfer_call()`, `build_ivr_response()`, webhook signature validation, TwiML generation |
| `test_sip_provider.py` | Plivo API mock, `make_call()`, `end_call()`, webhook parsing |
| `test_webhook_views.py` | Status webhooks (all statuses), DTMF webhooks, recording-ready webhooks, signature validation, idempotency (duplicate event_id) |
| `test_ivr_builder.py` | Menu tree validation, DTMF routing, submenu resolution, timeout handling, max retries |
| `test_dtmf_handler.py` | Digit parsing, multi-digit gather, timeout, invalid input, action dispatch |
| `test_broadcast_handler.py` | `handle_voice_message()`, DNC skip, missing phone skip, daily limit block, IVR + TTS + audio dispatch |

**Target:** 60+ test cases, matching RCS test density.

### 3.4 Admin

```python
# voice/admin.py
@admin.register(VoiceApp)
class VoiceAppAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "provider", "caller_id", "is_active", "calls_made_today", "daily_limit")
    list_filter = ("provider", "is_active", "recording_enabled")
    search_fields = ("name", "tenant__name", "caller_id")

@admin.register(VoiceCall)
class VoiceCallAdmin(admin.ModelAdmin):
    list_display = ("id", "voice_app", "direction", "from_number", "to_number", "status",
                    "duration_seconds", "initiated_at")
    list_filter = ("status", "direction")
    search_fields = ("provider_call_id", "from_number", "to_number")
    readonly_fields = ("id", "initiated_at", "raw_payload")

@admin.register(VoiceWebhookEvent)
class VoiceWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "voice_app", "event_type", "processed", "created_at")
    list_filter = ("event_type", "processed")

@admin.register(IVRMenu)
class IVRMenuAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "is_active", "fallback_action")
    list_filter = ("is_active", "fallback_action")
```

### 3.5 Serializers

```python
# voice/serializers.py
class VoiceAppSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceApp
        fields = "__all__"
        read_only_fields = ("id", "calls_made_today", "created_at", "updated_at")
        extra_kwargs = {
            "provider_credentials": {"write_only": True},
            "sip_password": {"write_only": True},
            "webhook_secret": {"write_only": True},
        }

class VoiceCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceCall
        fields = "__all__"
        read_only_fields = ("id", "initiated_at", "duration_seconds", "cost", "raw_payload")

class IVRMenuSerializer(serializers.ModelSerializer):
    class Meta:
        model = IVRMenu
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")

class MakeCallSerializer(serializers.Serializer):
    """Validates outbound call request."""
    to = serializers.CharField(max_length=20, help_text="E.164 phone number")
    tts_text = serializers.CharField(required=False, allow_blank=True)
    audio_url = serializers.URLField(required=False, allow_blank=True)
    ivr_menu_id = serializers.UUIDField(required=False)
    record = serializers.BooleanField(default=False)
```

---

## Migration Plan

### New Migrations Required

| App | Migration | Description |
|---|---|---|
| `voice` | `0001_initial` | VoiceApp, VoiceCall, VoiceWebhookEvent, IVRMenu |
| `broadcast` | `0006_*` | Add VOICE to BroadcastPlatformChoices |
| `tenants` | `0017_*` | Add TWILIO_VOICE, PLIVO, EXOTEL to BSPChoices |

### Existing Migrations — No Changes Needed

| App | Reason |
|---|---|
| `contacts` | `VOICE` already in ContactSource + PreferredChannelChoices |
| `team_inbox` | `VOICE` already in MessagePlatformChoices |
| `jina_connect/platform_choices.py` | `VOICE` already defined |

---

## Files Changed Summary

### New Files (voice/ app) — ~34 files

| Path | Purpose |
|---|---|
| `voice/__init__.py` | Package init |
| `voice/admin.py` | Django admin |
| `voice/apps.py` | AppConfig + channel registration |
| `voice/constants.py` | All choice enums |
| `voice/cron.py` | Daily counter reset |
| `voice/models.py` | VoiceApp, VoiceCall, VoiceWebhookEvent, IVRMenu |
| `voice/serializers.py` | DRF serializers |
| `voice/signals.py` | post_save → task dispatch |
| `voice/tasks.py` | Celery event processing + call initiation |
| `voice/urls.py` | URL configuration |
| `voice/views.py` | Webhook views (Twilio + SIP) |
| `voice/providers/__init__.py` | Provider registry |
| `voice/providers/base.py` | BaseVoiceProvider ABC + data classes |
| `voice/providers/twilio_voice.py` | Twilio Voice implementation |
| `voice/providers/sip_trunk.py` | Plivo / generic SIP implementation |
| `voice/services/call_manager.py` | VoiceCallManager (BaseChannelAdapter) |
| `voice/services/ivr_builder.py` | IVR menu/flow builder |
| `voice/services/recording_manager.py` | Recording storage |
| `voice/services/dtmf_handler.py` | DTMF input processing |
| `voice/services/rate_limiter.py` | Per-app rate limiting |
| `voice/viewsets/voice_app.py` | VoiceApp CRUD viewset |
| `voice/viewsets/voice_call.py` | VoiceCall list/detail viewset |
| `voice/viewsets/ivr_menu.py` | IVRMenu CRUD viewset |
| `voice/migrations/0001_initial.py` | Initial migration |
| `voice/tests/test_models.py` | Model tests |
| `voice/tests/test_call_manager.py` | Call manager tests |
| `voice/tests/test_twilio_provider.py` | Twilio provider tests |
| `voice/tests/test_sip_provider.py` | SIP/Plivo provider tests |
| `voice/tests/test_webhook_views.py` | Webhook view tests |
| `voice/tests/test_ivr_builder.py` | IVR builder tests |
| `voice/tests/test_dtmf_handler.py` | DTMF handler tests |
| `voice/tests/test_broadcast_handler.py` | Broadcast integration tests |

### Modified Files — ~12 files

| Path | Change |
|---|---|
| `jina_connect/settings.py` | Add `"voice"` to INSTALLED_APPS, add cron job |
| `jina_connect/urls.py` | Add `path("voice/", include(...))` |
| `broadcast/models.py` | Add VOICE to BroadcastPlatformChoices, `_get_voice_message_price()`, routing |
| `broadcast/tasks.py` | Add `handle_voice_message()`, update `_PLATFORM_HANDLERS` |
| `chat_flow/services/graph_executor.py` | Add VOICE branch |
| `chat_flow/constants.py` | Add IVR node types |
| `tenants/models.py` | Add BSPChoices for voice providers |
| `tenants/filters.py` | Add `"voice"` filter + update `"all"` |
| `mcp_server/tools/messaging.py` | Add voice MCP tools, update `_ALLOWED_CHANNELS` |
| `requirements.txt` | Add `twilio>=9.0.0`, `plivo>=4.50.0` |
| `README.md` | Update channel support table |
| `broadcast/migrations/0006_*.py` | VOICE platform choice |
| `tenants/migrations/0017_*.py` | Voice BSP choices |

---

## VoiceCallManager — BaseChannelAdapter Implementation

```python
# voice/services/call_manager.py
class VoiceCallManager(BaseChannelAdapter):
    """Implements BaseChannelAdapter for Voice channel."""

    def __init__(self, voice_app):
        self.voice_app = voice_app
        self.provider = get_voice_provider(voice_app)

    def get_channel_name(self) -> str:
        return "VOICE"

    def send_text(self, chat_id: str, text: str, **kwargs) -> Dict[str, Any]:
        """Send a TTS outbound call. Maps BaseChannelAdapter.send_text to voice."""
        return self.make_call(to=chat_id, tts_text=text, **kwargs)

    def send_media(self, chat_id: str, media_type: str, media_url: str,
                   caption: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Play an audio file in a call. Only 'audio' media_type is native."""
        if media_type == "audio":
            return self.make_call(to=chat_id, audio_url=media_url, tts_text=caption, **kwargs)
        # For non-audio media, fall back to TTS of the caption
        return self.send_text(chat_id=chat_id, text=caption or media_url, **kwargs)

    def send_keyboard(self, chat_id: str, text: str, keyboard: list,
                      **kwargs) -> Dict[str, Any]:
        """Initiate call with IVR menu built from keyboard options."""
        ivr_menu = self._keyboard_to_ivr(text, keyboard)
        return self.make_call(to=chat_id, ivr_menu=ivr_menu, tts_text=text, **kwargs)

    def make_call(self, to: str, *, tts_text=None, audio_url=None,
                  ivr_menu=None, ivr_menu_id=None, record=None,
                  contact=None, broadcast_message=None, **kwargs) -> Dict[str, Any]:
        """Core call initiation method."""
        # 1. DNC check
        if contact and contact.dnc:
            return {"success": False, "call_id": "", "error": "Contact is on Do Not Call list"}

        # 2. Rate limit
        if not check_rate_limit(str(self.voice_app.pk)):
            return {"success": False, "call_id": "", "error": "Rate limit exceeded"}

        # 3. Daily limit
        if not self.voice_app.increment_daily_counter():
            return {"success": False, "call_id": "", "error": "Daily call limit reached"}

        # 4. Resolve IVR menu
        ivr_data = None
        ivr_obj = None
        if ivr_menu_id:
            from voice.models import IVRMenu
            ivr_obj = IVRMenu.objects.filter(pk=ivr_menu_id, tenant=self.voice_app.tenant).first()
            if ivr_obj:
                ivr_data = ivr_obj.menu_tree
        elif ivr_menu:
            ivr_data = ivr_menu

        # 5. Determine recording
        should_record = record if record is not None else self.voice_app.recording_enabled

        # 6. Initiate call via provider
        result = self.provider.make_call(
            to=to,
            from_=self.voice_app.caller_id,
            tts_text=tts_text,
            audio_url=audio_url,
            ivr_menu=ivr_data,
            record=should_record,
            **kwargs,
        )

        # 7. Persist VoiceCall record
        voice_call = self._persist_call(
            to=to, result=result, contact=contact,
            broadcast_message=broadcast_message, ivr_menu=ivr_obj,
        )

        return {
            "success": result.success,
            "call_id": str(voice_call.pk) if voice_call else "",
            "provider_call_id": result.call_id or "",
            "status": result.status,
            "error": result.error_message,
        }
```

---

## IVR Flow Example

### Simple IVR Menu Tree (JSON stored in `IVRMenu.menu_tree`)

```json
{
  "greeting": "Welcome to Acme Corp. Press 1 for Sales, 2 for Support, 0 to repeat.",
  "options": {
    "1": {
      "action": "transfer",
      "target": "+919876543210",
      "label": "Sales",
      "announce": "Connecting you to our sales team."
    },
    "2": {
      "action": "submenu",
      "label": "Support",
      "greeting": "For billing, press 1. For technical support, press 2.",
      "options": {
        "1": {
          "action": "transfer",
          "target": "+919876543211",
          "label": "Billing"
        },
        "2": {
          "action": "transfer",
          "target": "+919876543212",
          "label": "Technical Support"
        }
      }
    },
    "0": {
      "action": "repeat"
    },
    "*": {
      "action": "voicemail"
    }
  }
}
```

### Twilio TwiML Generated for Above

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather numDigits="1" action="/voice/v1/webhooks/twilio/{app_id}/dtmf/" method="POST" timeout="5">
    <Say voice="alice">Welcome to Acme Corp. Press 1 for Sales, 2 for Support, 0 to repeat.</Say>
  </Gather>
  <Say voice="alice">We didn't receive your input. Goodbye.</Say>
  <Hangup/>
</Response>
```

---

## Later Phase (Not in Scope)

These are tracked but NOT implemented in this PR:

| Feature | Notes |
|---|---|
| Advanced call routing | Skills-based routing, queue management, ring groups |
| Real-time transcription | Twilio Media Streams → WebSocket → STT engine |
| Sentiment analysis | Post-call analysis via LLM on transcription |
| Agent handoff (warm transfer) | Conference bridge → agent whisper → hand off |
| Speech-to-text for chat flow | Live STT as alternative to DTMF for IVR input |
| Conferencing | Multi-party calls |
| Call queuing | Hold music, position announcements, estimated wait time |
| SIP registration (self-hosted) | pjsua2 / Obit for raw SIP REGISTER/INVITE |
| Voicemail-to-email | Transcribe voicemail + email to agent |

---

## Implementation Order (Recommended)

```
Week 1:  Phase 1.1–1.4  → App scaffold, models, constants, provider base
Week 1:  Phase 1.5      → Twilio Voice provider (primary)
Week 2:  Phase 1.6      → SIP Trunk / Plivo provider
Week 2:  Phase 1.7–1.10 → Webhooks, URLs, signals, tasks, cron
Week 2:  Phase 1.11–1.12→ Dependencies, settings
Week 3:  Phase 2.1–2.2  → Broadcast + team inbox integration
Week 3:  Phase 2.3–2.6  → Chat flow IVR, tenant filters, contacts
Week 4:  Phase 3.1–3.2  → MCP tools + unified dispatch
Week 4:  Phase 3.3–3.5  → Tests (60+), admin, serializers
Week 4:  CI green, PR    → Lint, format, migrations check, push
```

---

## GitHub Issues

| Issue | Title | Phase |
|---|---|---|
| #89 | Voice: Core Plumbing & Provider Adapters (Twilio + SIP) | Phase 1 |
| #90 | Voice: Product Integration (Broadcast, Inbox, ChatFlow, Tenants) | Phase 2 |
| #91 | Voice: MCP Tools, Tests & Hardening | Phase 3 |
