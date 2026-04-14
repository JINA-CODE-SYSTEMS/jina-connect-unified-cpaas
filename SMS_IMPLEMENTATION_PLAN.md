# SMS Channel — Complete Implementation Plan

> **Roadmap item:** SMS | 🚧 Coming soon | Twilio, MSG91, Fast2SMS adapters
>
> **Branch:** `sms-channel`
>
> **Goal:** First-class SMS channel adapter — multi-BSP support (Twilio, MSG91, Fast2SMS), outbound messaging, inbound webhooks, delivery reports, broadcast, team inbox, chat-flow routing, and MCP multi-channel support.

---

## Issue Tracker Map

| Issue | Title | Priority | Phase |
|---|---|---|---|
| NEW | SMS Channel: Core Plumbing & BSP Adapters | P1 | Phase 1 |
| NEW | SMS Channel: Product Integration (Broadcast, Inbox, Chatflow) | P1 | Phase 2 |
| NEW | SMS Channel: MCP Multi-Channel & Hardening | P1 | Phase 3 |
| [#15](../../issues/15) | Multi-channel routing in MCP tools | P1 | Phase 3 |
| [#16](../../issues/16) | Unified inbox across all channels | P1 | Phase 2 |
| [#76](../../issues/76) | Chat flow executor: platform-agnostic routing | P1 | Phase 2 |

---

## Table of Contents

1. [Current State Audit](#1-current-state-audit)
2. [Architecture Overview](#2-architecture-overview)
3. [Data Model Changes](#3-data-model-changes)
4. [Step-by-Step Implementation](#4-step-by-step-implementation)
   - [Phase 1 — Core Channel Plumbing](#phase-1--core-channel-plumbing)
   - [Phase 2 — Product Integration](#phase-2--product-integration)
   - [Phase 3 — MCP & Hardening](#phase-3--mcp--hardening)
5. [Security Checklist](#5-security-checklist)
6. [Testing Plan](#6-testing-plan)
7. [Configuration & Environment Variables](#7-configuration--environment-variables)
8. [Migration & Rollout Checklist](#8-migration--rollout-checklist)
9. [Out of Scope (Later)](#9-out-of-scope-later)
10. [Definition of Done](#10-definition-of-done)

---

## 1. Current State Audit

### What already exists (reuse as-is)

| Area | File(s) | Status |
|---|---|---|
| Platform enum — canonical | `jina_connect/platform_choices.py` → `PlatformChoices.SMS` | ✅ Exists |
| Platform enum — Broadcast | `broadcast/models.py` → `BroadcastPlatformChoices.SMS` | ✅ Exists |
| Platform enum — Team Inbox | `team_inbox/models.py` → `MessagePlatformChoices.SMS` | ✅ Exists |
| Platform enum — Contacts | `contacts/models.py` → `PreferredChannelChoices.SMS` | ✅ Exists |
| Rate limit setting | `jina_connect/settings.py` → `PLATFORM_RATE_LIMITS["sms"]` = 100/min | ✅ Exists |
| Broadcast router | `broadcast/tasks.py` → `_PLATFORM_HANDLERS["SMS"]` | ✅ Routes to `handle_sms_message()` |
| Broadcast sender stub | `broadcast/tasks.py` → `handle_sms_message()` | ⚠️ Stub — simulated, no real API call |
| Broadcast pricing stub | `broadcast/models.py` → `_get_sms_message_price()` | ⚠️ Returns `Decimal("0")` |
| Dashboard filter | `tenants/viewsets/host_wallet_balance.py` | ✅ Accepts SMS counts |
| Tenant product filter | `tenants/filters.py` | ⚠️ Returns empty queryset ("TODO: implement") |
| Channel adapter ABC | `wa/adapters/channel_base.py` → `BaseChannelAdapter` | ✅ Exists (send_text, send_media, send_keyboard) |
| Channel registry | `jina_connect/channel_registry.py` → `register_channel()` / `get_channel_adapter()` | ✅ Exists |
| Inbox message factory | `team_inbox/utils/inbox_message_factory.py` → `create_inbox_message()` | ✅ Platform-agnostic |
| BSP enum — Twilio | `tenants/models.py` → `BSPChoices.TWILIO` | ✅ Exists |
| Credit manager | `broadcast/services/credit_manager.py` → `BroadcastCreditManager` | ✅ Platform-agnostic |
| Placeholder renderer | `broadcast/utils/placeholder_renderer.py` | ✅ Platform-agnostic |

### What does NOT exist yet

- No `sms/` Django app
- No SMS BSP HTTP client (Twilio / MSG91 / Fast2SMS)
- No `SMSApp` model (provider credentials, sender ID, DLT info)
- No inbound SMS webhook receiver
- No delivery report (DLR) webhook receiver
- No outbound SMS message tracking model
- No SMS pricing logic (per-message, per-country, DLT surcharges)
- No MCP `_send_sms_message()` helper
- No chat-flow SMS routing
- No team inbox inbound path for SMS

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                   SMS BSP Providers                               │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────────────┐ │
│  │ Twilio   │  │  MSG91   │  │ Fast2SMS  │  │  AWS SNS (future)│ │
│  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └────────┬─────────┘ │
└───────┼──────────────┼──────────────┼─────────────────┼──────────┘
        │ Inbound       │              │                 │
        │ + DLR         ▲ Send API     ▲                 ▲
        ▼               │              │                 │
┌──────────────────────────────────────────────────────────────────┐
│  sms/views.py                     sms/services/                  │
│  ┌─────────────────────────┐      ┌────────────────────────────┐ │
│  │ SMSInboundWebhookView   │      │ sms_client.py (BSP router) │ │
│  │ • Verify auth (Twilio   │      │ twilio_client.py           │ │
│  │   sig / MSG91 IP / etc) │      │ msg91_client.py            │ │
│  │ • Persist event row     │      │ fast2sms_client.py         │ │
│  │ • Return 200 fast       │      │ message_sender.py          │ │
│  ├─────────────────────────┤      └──────────────▲─────────────┘ │
│  │ SMSDLRWebhookView       │                     │               │
│  │ • Update message status │                     │               │
│  └──────────┬──────────────┘                     │               │
│             │ post_save signal                   │               │
│             ▼                                    │               │
│  ┌─────────────────────────┐      ┌──────────────┴─────────────┐ │
│  │ sms/tasks.py            │      │ broadcast/tasks.py         │ │
│  │ process_sms_event_task  │      │ handle_sms_message()       │ │
│  │ • Parse inbound SMS     │      │ (replace stub with real)   │ │
│  │ • Upsert contact        │      │                            │ │
│  │ • Write team inbox msg  │      │ mcp_server/tools/          │ │
│  │ • Route to chat flow    │      │ send_message(channel=SMS)  │ │
│  └─────────────────────────┘      └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Mirror the Telegram pattern** — `telegram/` app is the closest reference (native API like SMS; no template system).
2. **Multi-BSP from day one** — Twilio, MSG91, Fast2SMS behind a single `BaseSMSProvider` interface.
3. **Reuse all existing abstractions** — `BaseChannelAdapter`, `channel_registry`, `create_inbox_message()`, `BroadcastCreditManager`.
4. **SMS = text-only channel** — No media messages, no keyboards. `send_media()` falls back to text with URL. `send_keyboard()` falls back to numbered text menu.
5. **Tenant isolation** — Every query scoped to tenant; API keys stored in encrypted JSONField.
6. **India DLT compliance** — Support DLT Entity ID, Sender ID (header), Template ID for Indian routes.

---

## 3. Data Model Changes

### 3.1 Extend `TenantContact` (contacts app)

```python
# contacts/models.py — add to ContactSource:
class ContactSource(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    TELEGRAM = "TELEGRAM", "Telegram"
    VOICE = "VOICE", "Voice"
    SMS = "SMS", "SMS"           # ← NEW
```

No new fields needed on `TenantContact` — SMS uses the existing `phone` field (PhoneNumberField) which is already present on every contact.

**Migration:** `contacts/migrations/XXXX_add_sms_source.py` (choices-only — no schema change, Django handles via TextChoices)

### 3.2 New Django App — `sms/`

```
sms/
├── __init__.py
├── admin.py
├── apps.py                        # Register in channel_registry
├── constants.py                   # Error maps, provider constants
├── models.py                      # SMSApp, SMSWebhookEvent, SMSOutboundMessage
├── serializers.py                 # API serializers
├── signals.py                     # post_save → Celery task
├── tasks.py                       # process_sms_event_task
├── urls.py                        # Webhook + API routes
├── views.py                       # Inbound + DLR webhook receivers
├── migrations/
│   └── 0001_initial.py
├── providers/                     # Multi-BSP provider layer
│   ├── __init__.py                # get_sms_provider() factory
│   ├── base.py                    # BaseSMSProvider ABC
│   ├── twilio_provider.py         # Twilio REST API
│   ├── msg91_provider.py          # MSG91 API
│   └── fast2sms_provider.py       # Fast2SMS API
├── services/
│   ├── __init__.py
│   ├── message_sender.py          # SMSMessageSender (implements BaseChannelAdapter)
│   └── rate_limiter.py            # Per-tenant SMS rate limiting
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_twilio_provider.py
│   ├── test_msg91_provider.py
│   ├── test_webhook.py
│   ├── test_message_sender.py
│   ├── test_contact_upsert.py
│   ├── test_inbox_integration.py
│   └── test_broadcast_integration.py
└── viewsets/
    ├── __init__.py
    ├── sms_app.py                 # CRUD for SMSApp configuration
    └── sms_message.py             # List/filter outbound messages
```

### 3.3 New Models

#### `SMSApp` — Provider Configuration (per-tenant)

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE, `related_name="sms_apps"` |
| `provider` | CharField(20) | Choices: `TWILIO`, `MSG91`, `FAST2SMS` |
| `provider_credentials` | JSONField | **Encrypted at rest** — structure varies per BSP (see below) |
| `sender_id` | CharField(20) | Alphanumeric sender ID / short code / phone number |
| `is_active` | BooleanField | Default `True` |
| `daily_limit` | IntegerField | Default 10000 |
| `messages_sent_today` | IntegerField | Default 0, reset via cron |
| `webhook_url` | URLField | Auto-generated: `{BASE}/sms/v1/webhooks/{app.id}/inbound/` |
| `dlr_webhook_url` | URLField | Auto-generated: `{BASE}/sms/v1/webhooks/{app.id}/dlr/` |
| `webhook_secret` | CharField(64) | Random secret for webhook validation |
| **India DLT fields** | | |
| `dlt_entity_id` | CharField(30) | DLT registered entity ID (India compliance) |
| `dlt_template_id` | CharField(30) | Default DLT template ID (optional) |
| **Pricing** | | |
| `price_per_sms` | DecimalField(10,6) | Default rate per message |
| `price_per_sms_international` | DecimalField(10,6) | International rate |
| `created_at` / `updated_at` | DateTimeField | Auto |

**Constraints:** `unique_together = ("tenant", "provider", "sender_id")`

**Provider Credentials Schema:**

```jsonc
// Twilio
{
  "account_sid": "ACxxxxxxx",
  "auth_token": "xxxxxxx",
  "messaging_service_sid": "MGxxxxxxx"  // optional, for Messaging Service
}

// MSG91
{
  "auth_key": "xxxxxxx",
  "sender_id": "JINA",   // 6-char sender
  "route": "4",           // 4=transactional, 1=promotional
  "dlt_te_id": "xxxxx"   // DLT Template Entity ID
}

// Fast2SMS
{
  "api_key": "xxxxxxx",
  "sender_id": "JINA",
  "route": "dlt"          // "dlt" or "quick"
}
```

#### `SMSWebhookEvent` — Raw Inbound & DLR Dumps

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE |
| `sms_app` | FK → `SMSApp` | CASCADE, `related_name="webhook_events"` |
| `event_type` | CharField(20) | `INBOUND`, `DLR`, `UNKNOWN` |
| `provider` | CharField(20) | `TWILIO`, `MSG91`, `FAST2SMS` |
| `payload` | JSONField | Raw provider webhook payload |
| `from_number` | CharField(20) | Sender phone |
| `to_number` | CharField(20) | Receiver phone (our number) |
| `provider_message_id` | CharField(100) | BSP's unique message ID |
| `is_processed` | BooleanField | Default `False` |
| `processed_at` | DateTimeField | Nullable |
| `error_message` | TextField | Nullable |
| `retry_count` | IntegerField | Default 0 |
| `created_at` | DateTimeField | Auto |

**Constraints:** `unique_together = ("sms_app", "provider_message_id", "event_type")` — idempotency guard

#### `SMSOutboundMessage` — Outbound Message Tracking

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE |
| `sms_app` | FK → `SMSApp` | CASCADE |
| `contact` | FK → `TenantContact` | CASCADE, nullable |
| `to_number` | CharField(20) | Destination phone |
| `from_number` | CharField(20) | Sender ID / short code |
| `message_text` | TextField | SMS body (max ~1600 chars / 10 segments) |
| `segment_count` | IntegerField | Default 1, SMS segments used |
| `provider_message_id` | CharField(100) | BSP's message ID from send response |
| `status` | CharField(20) | `PENDING`, `QUEUED`, `SENT`, `DELIVERED`, `FAILED`, `UNDELIVERED` |
| `cost` | DecimalField(10,6) | Actual cost charged |
| `provider_cost` | DecimalField(10,6) | Cost reported by provider (nullable) |
| `request_payload` | JSONField | What was sent to BSP |
| `response_payload` | JSONField | BSP response |
| `error_code` | CharField(20) | Provider error code |
| `error_message` | TextField | Nullable |
| `sent_at` | DateTimeField | Nullable |
| `delivered_at` | DateTimeField | Nullable |
| `failed_at` | DateTimeField | Nullable |
| `inbox_message` | FK → `team_inbox.Messages` | Nullable, links to inbox timeline |
| `broadcast_message` | FK → `broadcast.BroadcastMessage` | Nullable, links to broadcast |
| `created_at` / `updated_at` | DateTimeField | Auto |

---

## 4. Step-by-Step Implementation

---

### Phase 1 — Core Channel Plumbing

**Goal:** Django app, multi-BSP provider, send API, webhook receivers, channel registration.

---

#### Step 1: Create `sms/` Django App Skeleton

**Files:**
- `sms/__init__.py`
- `sms/apps.py` — `SMSConfig(AppConfig)` with `ready()` registering `"SMS"` in channel registry
- `sms/admin.py` — Register `SMSApp`, `SMSOutboundMessage`, `SMSWebhookEvent`
- `sms/constants.py` — Provider constants, error maps, status maps
- `sms/models.py` — `SMSApp`, `SMSWebhookEvent`, `SMSOutboundMessage`
- `sms/signals.py` — `post_save` on `SMSWebhookEvent` → triggers `process_sms_event_task`
- `sms/migrations/0001_initial.py` — Auto-generated

**Pattern — `sms/apps.py`:**
```python
from django.apps import AppConfig


class SMSConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sms"

    def ready(self):
        import sms.signals  # noqa: F401

        from jina_connect.channel_registry import register_channel

        def _sms_adapter_factory(tenant):
            from sms.models import SMSApp
            sms_app = SMSApp.objects.filter(
                tenant=tenant, is_active=True,
            ).first()
            if not sms_app:
                raise ValueError(f"Tenant {tenant.pk} has no active SMS app configured.")
            from sms.services.message_sender import SMSMessageSender
            return SMSMessageSender(sms_app)

        register_channel("SMS", _sms_adapter_factory)
```

**Wiring:**
- Add `"sms"` to `INSTALLED_APPS` in `jina_connect/settings.py`
- Add `path("sms/", include("sms.urls"))` to `jina_connect/urls.py`

---

#### Step 2: Multi-BSP Provider Layer

**Files:**
- `sms/providers/__init__.py` — `get_sms_provider(sms_app)` factory
- `sms/providers/base.py` — `BaseSMSProvider` ABC
- `sms/providers/twilio_provider.py` — Twilio REST API implementation
- `sms/providers/msg91_provider.py` — MSG91 API implementation
- `sms/providers/fast2sms_provider.py` — Fast2SMS API implementation

**Pattern — `sms/providers/base.py`:**
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SMSSendResult:
    """Uniform result from all SMS send operations."""
    success: bool
    provider: str                          # "twilio", "msg91", "fast2sms"
    message_id: Optional[str] = None       # provider's message SID/ID
    segment_count: int = 1
    cost: Optional[float] = None           # provider-reported cost
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class SMSInboundMessage:
    """Normalized inbound SMS from any provider."""
    from_number: str
    to_number: str
    body: str
    provider_message_id: str
    timestamp: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass
class SMSDeliveryReport:
    """Normalized delivery status from any provider."""
    provider_message_id: str
    status: str                            # SENT, DELIVERED, FAILED, UNDELIVERED
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    timestamp: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None


class BaseSMSProvider(ABC):
    """Abstract base for all SMS providers."""

    PROVIDER_NAME: str = "base"

    def __init__(self, sms_app: "SMSApp"):
        self.sms_app = sms_app
        self.credentials = sms_app.provider_credentials or {}

    @abstractmethod
    def send_sms(
        self,
        to: str,
        body: str,
        *,
        sender_id: Optional[str] = None,
        dlt_template_id: Optional[str] = None,
        **kwargs,
    ) -> SMSSendResult:
        """Send a single SMS message."""

    @abstractmethod
    def parse_inbound_webhook(self, payload: dict) -> SMSInboundMessage:
        """Parse provider-specific inbound webhook into normalized form."""

    @abstractmethod
    def parse_dlr_webhook(self, payload: dict) -> SMSDeliveryReport:
        """Parse provider-specific delivery report into normalized form."""

    @abstractmethod
    def validate_webhook_signature(self, request) -> bool:
        """Verify that the webhook came from the SMS provider."""

    def get_balance(self) -> Optional[float]:
        """Optional: fetch account balance from provider."""
        return None
```

**Pattern — `sms/providers/__init__.py`:**
```python
from sms.providers.base import BaseSMSProvider

_PROVIDER_REGISTRY = {}


def _lazy_load():
    if _PROVIDER_REGISTRY:
        return
    from sms.providers.twilio_provider import TwilioSMSProvider
    from sms.providers.msg91_provider import MSG91SMSProvider
    from sms.providers.fast2sms_provider import Fast2SMSProvider

    _PROVIDER_REGISTRY.update({
        "TWILIO": TwilioSMSProvider,
        "MSG91": MSG91SMSProvider,
        "FAST2SMS": Fast2SMSProvider,
    })


def get_sms_provider(sms_app) -> BaseSMSProvider:
    """Factory resolution: provider field → provider instance."""
    _lazy_load()
    provider_cls = _PROVIDER_REGISTRY.get(sms_app.provider)
    if provider_cls is None:
        raise NotImplementedError(f"No SMS provider for '{sms_app.provider}'")
    return provider_cls(sms_app)
```

---

#### Step 3: Twilio SMS Provider (Primary)

**File:** `sms/providers/twilio_provider.py`

**Implementation details:**
- Use `requests` (not Twilio SDK) to keep deps minimal — same pattern as `telegram/services/bot_client.py`
- Endpoint: `POST https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json`
- Auth: HTTP Basic (`account_sid:auth_token`)
- Send params: `To`, `From` (or `MessagingServiceSid`), `Body`, `StatusCallback`
- Inbound webhook: Twilio POSTs form-encoded data with `From`, `To`, `Body`, `MessageSid`
- DLR webhook: Twilio POSTs form-encoded `MessageSid`, `MessageStatus` (queued → sent → delivered → failed)
- Signature validation: `X-Twilio-Signature` using HMAC-SHA1 of request URL + sorted POST params + auth_token

```python
class TwilioSMSProvider(BaseSMSProvider):
    PROVIDER_NAME = "twilio"
    API_BASE = "https://api.twilio.com/2010-04-01/Accounts"

    def send_sms(self, to, body, *, sender_id=None, **kwargs):
        sid = self.credentials["account_sid"]
        token = self.credentials["auth_token"]
        url = f"{self.API_BASE}/{sid}/Messages.json"

        data = {
            "To": to,
            "Body": body,
            "StatusCallback": self.sms_app.dlr_webhook_url,
        }
        # Use MessagingServiceSid if available, else From number
        if self.credentials.get("messaging_service_sid"):
            data["MessagingServiceSid"] = self.credentials["messaging_service_sid"]
        else:
            data["From"] = sender_id or self.sms_app.sender_id

        resp = requests.post(url, data=data, auth=(sid, token), timeout=30)
        # ... parse response → SMSSendResult

    def validate_webhook_signature(self, request):
        """Validate X-Twilio-Signature header."""
        # HMAC-SHA1(url + sorted_post_params, auth_token)
        ...

    def parse_inbound_webhook(self, payload):
        return SMSInboundMessage(
            from_number=payload.get("From", ""),
            to_number=payload.get("To", ""),
            body=payload.get("Body", ""),
            provider_message_id=payload.get("MessageSid", ""),
        )

    def parse_dlr_webhook(self, payload):
        status_map = {
            "queued": "QUEUED", "sent": "SENT",
            "delivered": "DELIVERED", "failed": "FAILED",
            "undelivered": "UNDELIVERED",
        }
        return SMSDeliveryReport(
            provider_message_id=payload.get("MessageSid", ""),
            status=status_map.get(payload.get("MessageStatus", ""), "UNKNOWN"),
            error_code=payload.get("ErrorCode"),
            error_message=payload.get("ErrorMessage"),
        )
```

---

#### Step 4: MSG91 SMS Provider

**File:** `sms/providers/msg91_provider.py`

**Implementation details:**
- Send API: `POST https://control.msg91.com/api/v5/flow/` (for DLT) or `POST https://api.msg91.com/api/v2/sendsms` (legacy)
- Auth: Header `authkey: <auth_key>`
- India DLT compliance: Requires `DLT_TE_ID` (template entity ID), `sender_id` (6 chars)
- Inbound: Webhook configured in MSG91 dashboard — POSTs JSON with `mobile`, `message`, `msg_id`
- DLR: Webhook with `request_id`, `status` (1=Delivered, 2=Failed, 9=NDNC)
- Signature: IP whitelist validation (MSG91 publishes webhook IPs)

---

#### Step 5: Fast2SMS Provider

**File:** `sms/providers/fast2sms_provider.py`

**Implementation details:**
- Send API: `POST https://www.fast2sms.com/dev/bulkV2`
- Auth: Header `authorization: <api_key>`
- Params: `route` (dlt/quick), `sender_id`, `message`, `numbers` (comma-separated)
- DLT route: Requires `variables_values` instead of `message`
- Inbound: Not supported by Fast2SMS (outbound-only provider)
- DLR: Callback URL with `request_id`, `status`

---

#### Step 6: SMS Message Sender Service (Channel Adapter)

**File:** `sms/services/message_sender.py`

**Pattern — implements `BaseChannelAdapter`:**
```python
import logging
from typing import Any, Dict, Optional

from wa.adapters.channel_base import BaseChannelAdapter
from sms.providers import get_sms_provider

logger = logging.getLogger(__name__)


class SMSMessageSender(BaseChannelAdapter):
    """Implements BaseChannelAdapter for SMS channel."""

    def __init__(self, sms_app):
        self.sms_app = sms_app
        self.provider = get_sms_provider(sms_app)

    def send_text(self, phone: str, text: str, **kwargs) -> Dict[str, Any]:
        """Send plain text SMS."""
        result = self.provider.send_sms(
            to=phone,
            body=text,
            dlt_template_id=kwargs.get("dlt_template_id"),
        )
        self._persist_outbound(phone, text, result, **kwargs)
        return {
            "success": result.success,
            "message_id": result.message_id,
            "segments": result.segment_count,
            "error": result.error_message,
        }

    def send_media(self, phone: str, media_type: str, media_url: str,
                   caption: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """SMS doesn't support media — send caption + URL as text."""
        fallback_text = caption or ""
        if media_url:
            fallback_text = f"{fallback_text}\n{media_url}".strip()
        return self.send_text(phone, fallback_text, **kwargs)

    def send_keyboard(self, phone: str, text: str, keyboard: list,
                      **kwargs) -> Dict[str, Any]:
        """SMS doesn't support interactive keyboards — send numbered menu."""
        lines = [text, ""]
        for i, btn in enumerate(keyboard, 1):
            label = btn.get("text") or btn.get("label") or btn.get("title", "")
            lines.append(f"{i}. {label}")
        return self.send_text(phone, "\n".join(lines), **kwargs)

    def get_channel_name(self) -> str:
        return "SMS"

    def _persist_outbound(self, phone, text, result, **kwargs):
        """Create SMSOutboundMessage + optional inbox timeline entry."""
        from sms.models import SMSOutboundMessage
        outbound = SMSOutboundMessage.objects.create(
            tenant=self.sms_app.tenant,
            sms_app=self.sms_app,
            contact=kwargs.get("contact"),
            to_number=phone,
            from_number=self.sms_app.sender_id,
            message_text=text,
            segment_count=result.segment_count,
            provider_message_id=result.message_id or "",
            status="SENT" if result.success else "FAILED",
            cost=result.cost or self.sms_app.price_per_sms,
            request_payload={"to": phone, "body": text},
            response_payload=result.raw_response or {},
            error_code=result.error_code or "",
            error_message=result.error_message or "",
            broadcast_message=kwargs.get("broadcast_message"),
        )
        # Create inbox timeline entry if contact provided
        if kwargs.get("contact") and kwargs.get("create_inbox_entry", True):
            from team_inbox.utils.inbox_message_factory import create_inbox_message
            inbox_msg = create_inbox_message(
                tenant=self.sms_app.tenant,
                contact=kwargs["contact"],
                platform="SMS",
                direction="OUTGOING",
                author=kwargs.get("author", "USER"),
                content={"type": "text", "body": {"text": text}},
                external_message_id=str(outbound.id),
                tenant_user=kwargs.get("tenant_user"),
                is_read=True,
            )
            outbound.inbox_message = inbox_msg
            outbound.save(update_fields=["inbox_message"])
        return outbound
```

---

#### Step 7: Rate Limiter

**File:** `sms/services/rate_limiter.py`

**Reuse pattern from `telegram/services/rate_limiter.py`:**
- Per-tenant window-based rate limiting using Django cache (Redis)
- Consult `settings.PLATFORM_RATE_LIMITS["sms"]` (default: 100/min)
- `check_rate_limit(sms_app)` → `(bool, retry_after_seconds)`
- Integrated into `SMSMessageSender.send_text()` with walrus operator check

---

#### Step 8: Webhook Receivers

**File:** `sms/views.py`

**Two endpoints:**

1. **`SMSInboundWebhookView`** — receives inbound SMS from provider
   - `POST /sms/v1/webhooks/<uuid:sms_app_id>/inbound/`
   - Validates provider signature (Twilio HMAC / MSG91 IP whitelist)
   - Persists `SMSWebhookEvent(event_type="INBOUND")`
   - Returns 200 immediately (async processing via signal → Celery)

2. **`SMSDLRWebhookView`** — receives delivery reports
   - `POST /sms/v1/webhooks/<uuid:sms_app_id>/dlr/`
   - Validates provider signature
   - Persists `SMSWebhookEvent(event_type="DLR")`
   - Returns 200 immediately

**Pattern:**
```python
@method_decorator(csrf_exempt, name="dispatch")
class SMSInboundWebhookView(View):
    def post(self, request, sms_app_id):
        try:
            sms_app = SMSApp.objects.select_related("tenant").get(
                id=sms_app_id, is_active=True,
            )
        except SMSApp.DoesNotExist:
            return JsonResponse({"error": "not found"}, status=404)

        provider = get_sms_provider(sms_app)
        if not provider.validate_webhook_signature(request):
            logger.warning("SMS webhook signature validation failed: %s", sms_app_id)
            return JsonResponse({"error": "forbidden"}, status=403)

        payload = _parse_request_body(request)
        SMSWebhookEvent.objects.create(
            tenant=sms_app.tenant,
            sms_app=sms_app,
            event_type="INBOUND",
            provider=sms_app.provider,
            payload=payload,
            from_number=_extract_from(payload, sms_app.provider),
            to_number=_extract_to(payload, sms_app.provider),
            provider_message_id=_extract_msg_id(payload, sms_app.provider),
        )
        return JsonResponse({"status": "ok"})
```

---

#### Step 9: Inbound Event Processing (Celery Task)

**File:** `sms/tasks.py`

**Pattern — mirrors `telegram/tasks.py`:**
```python
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def process_sms_event_task(self, event_id: str):
    """Process a single SMS webhook event."""
    event = SMSWebhookEvent.objects.select_related("sms_app", "tenant").get(id=event_id)

    if event.is_processed:
        return

    try:
        if event.event_type == "INBOUND":
            _handle_inbound_sms(event)
        elif event.event_type == "DLR":
            _handle_delivery_report(event)

        event.is_processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["is_processed", "processed_at"])
    except Exception as exc:
        event.retry_count += 1
        event.error_message = str(exc)
        event.save(update_fields=["retry_count", "error_message"])
        raise self.retry(exc=exc)


def _handle_inbound_sms(event):
    """Process inbound SMS: upsert contact → inbox message → chatflow routing."""
    provider = get_sms_provider(event.sms_app)
    inbound = provider.parse_inbound_webhook(event.payload)

    # 1. Upsert contact by phone number
    contact, created = TenantContact.objects.get_or_create(
        tenant=event.tenant,
        phone=inbound.from_number,
        defaults={
            "source": "SMS",
            "first_name": "",
        },
    )

    # 2. Create team inbox message
    create_inbox_message(
        tenant=event.tenant,
        contact=contact,
        platform="SMS",
        direction="INCOMING",
        author="CONTACT",
        content={"type": "text", "body": {"text": inbound.body}},
        external_message_id=inbound.provider_message_id,
    )

    # 3. Route to chat flow if assigned
    _handle_chatflow_routing_sms(event.sms_app, contact, inbound.body)


def _handle_delivery_report(event):
    """Update outbound message status from DLR."""
    provider = get_sms_provider(event.sms_app)
    dlr = provider.parse_dlr_webhook(event.payload)

    try:
        outbound = SMSOutboundMessage.objects.get(
            sms_app=event.sms_app,
            provider_message_id=dlr.provider_message_id,
        )
    except SMSOutboundMessage.DoesNotExist:
        logger.warning("DLR for unknown message: %s", dlr.provider_message_id)
        return

    outbound.status = dlr.status
    update_fields = ["status"]

    if dlr.status == "DELIVERED":
        outbound.delivered_at = timezone.now()
        update_fields.append("delivered_at")
    elif dlr.status in ("FAILED", "UNDELIVERED"):
        outbound.failed_at = timezone.now()
        outbound.error_code = dlr.error_code or ""
        outbound.error_message = dlr.error_message or ""
        update_fields.extend(["failed_at", "error_code", "error_message"])

    outbound.save(update_fields=update_fields)

    # Update linked BroadcastMessage status if exists
    if outbound.broadcast_message:
        bm = outbound.broadcast_message
        status_map = {
            "DELIVERED": "DELIVERED", "FAILED": "FAILED",
            "UNDELIVERED": "FAILED", "SENT": "SENT",
        }
        bm.status = status_map.get(dlr.status, bm.status)
        bm.save(update_fields=["status"])
```

---

#### Step 10: URL Configuration

**File:** `sms/urls.py`

```python
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from sms.views import SMSInboundWebhookView, SMSDLRWebhookView
from sms.viewsets.sms_app import SMSAppViewSet
from sms.viewsets.sms_message import SMSOutboundMessageViewSet

router = DefaultRouter()
router.register(r"v1/apps", SMSAppViewSet, basename="sms-apps")
router.register(r"v1/messages", SMSOutboundMessageViewSet, basename="sms-messages")

urlpatterns = [
    path("v1/webhooks/<uuid:sms_app_id>/inbound/", SMSInboundWebhookView.as_view(), name="sms-inbound-webhook"),
    path("v1/webhooks/<uuid:sms_app_id>/dlr/", SMSDLRWebhookView.as_view(), name="sms-dlr-webhook"),
    path("", include(router.urls)),
]
```

---

### Phase 2 — Product Integration

**Goal:** Wire SMS into broadcast, team inbox, chat flow, and pricing.

---

#### Step 11: Replace Broadcast `handle_sms_message()` Stub

**File:** `broadcast/tasks.py`

**Replace the stub** with real SMS sending:
```python
def handle_sms_message(message):
    """Send SMS for a BroadcastMessage."""
    try:
        from sms.models import SMSApp
        from sms.services.message_sender import SMSMessageSender

        broadcast = message.broadcast
        sms_app = SMSApp.objects.filter(
            tenant=broadcast.tenant, is_active=True,
        ).first()
        if not sms_app:
            return {"success": False, "error": "No active SMS app configured"}

        sender = SMSMessageSender(sms_app)
        # Render placeholder content
        rendered_text = message.rendered_content or broadcast.name
        phone = str(message.contact.phone)

        result = sender.send_text(
            phone=phone,
            text=rendered_text,
            contact=message.contact,
            broadcast_message=message,
            tenant_user=broadcast.created_by,
            create_inbox_entry=False,  # Broadcast task handles inbox creation
        )

        return {
            "success": result["success"],
            "message_id": result.get("message_id", ""),
            "error": result.get("error", ""),
            "response": result,
        }
    except Exception as e:
        logger.exception("SMS broadcast message failed: %s", e)
        return {"success": False, "error": str(e)}
```

---

#### Step 12: SMS Pricing

**File:** `broadcast/models.py`

**Replace the `_get_sms_message_price()` stub:**
```python
def _get_sms_message_price(self):
    """Get SMS message price from the tenant's active SMS app."""
    from sms.models import SMSApp

    sms_app = SMSApp.objects.filter(
        tenant=self.tenant, is_active=True,
    ).first()
    if not sms_app:
        return Decimal("0")

    # Check if recipient is international (simple heuristic)
    contact = self.broadcastmessage_set.first()
    if contact and contact.contact and contact.contact.phone:
        phone_str = str(contact.contact.phone)
        # If phone doesn't start with tenant's country code → international
        if sms_app.price_per_sms_international and not phone_str.startswith("+91"):
            return sms_app.price_per_sms_international

    return sms_app.price_per_sms
```

---

#### Step 13: Team Inbox — Inbound SMS to Inbox

**Already handled in Step 9** (`_handle_inbound_sms` calls `create_inbox_message()`).

The existing `team_inbox/utils/inbox_message_factory.py` is platform-agnostic — passing `platform="SMS"` works out of the box.

**Inbox `expires_at` for SMS:**
- SMS has no session window (unlike WhatsApp's 24h)
- `Messages.expires_at` should return `None` for SMS — same as Telegram

**File:** `team_inbox/models.py` — verify `expires_at` property handles SMS:
```python
@property
def expires_at(self):
    if self.platform == MessagePlatformChoices.WHATSAPP:
        return self.timestamp + timedelta(hours=24)
    # Telegram, SMS, VOICE — no session window
    return None
```

---

#### Step 14: Chat Flow SMS Routing

**File:** `sms/tasks.py` — add `_handle_chatflow_routing_sms()`

**Pattern — mirrors `telegram/tasks.py` `_handle_chatflow_routing_telegram()`:**
```python
def _handle_chatflow_routing_sms(sms_app, contact, text):
    """Route inbound SMS to active chat flow session if contact is assigned."""
    from contacts.models import AssigneeTypeChoices
    from chat_flow.models import UserChatFlowSession

    # Check if contact is assigned to chatflow
    if contact.assigned_to_type != AssigneeTypeChoices.CHATFLOW:
        # Check for active session fallback
        session = UserChatFlowSession.objects.filter(
            contact=contact, is_active=True,
        ).first()
        if not session:
            return

    # Route text input through chatflow executor
    from chat_flow.services.graph_executor import ChatFlowExecutor

    session = UserChatFlowSession.objects.filter(
        contact=contact, is_active=True,
    ).select_related("chat_flow").first()
    if not session:
        return

    executor = ChatFlowExecutor(
        chat_flow=session.chat_flow,
        contact=contact,
        tenant=sms_app.tenant,
        platform="SMS",
    )
    result = executor.process_input(text, session=session)

    # If flow completed, unassign contact
    if result and result.get("completed"):
        contact.assigned_to_type = AssigneeTypeChoices.UNASSIGNED
        contact.assigned_to_id = None
        contact.save(update_fields=["assigned_to_type", "assigned_to_id"])
```

**Chat Flow Executor updates** — `chat_flow/services/graph_executor.py`:
- The `send_session_message()` method needs SMS awareness
- For SMS: Skip template rendering, send plain text via `SMSMessageSender.send_text()`
- The executor already receives `platform` param — add SMS branch in the send logic

---

#### Step 15: Serializers & ViewSets

**Files:**
- `sms/serializers.py` — `SMSAppSerializer`, `SMSOutboundMessageSerializer`, `SMSWebhookEventSerializer`
- `sms/viewsets/sms_app.py` — CRUD for `SMSApp` (create, update, list, retrieve, delete)
- `sms/viewsets/sms_message.py` — Read-only list/filter for outbound messages

**SMSAppViewSet actions:**
| Endpoint | Method | Description |
|---|---|---|
| `/sms/v1/apps/` | GET | List tenant's SMS apps |
| `/sms/v1/apps/` | POST | Create SMS app (validate credentials) |
| `/sms/v1/apps/{id}/` | GET | Retrieve SMS app config |
| `/sms/v1/apps/{id}/` | PATCH | Update config |
| `/sms/v1/apps/{id}/` | DELETE | Deactivate SMS app |
| `/sms/v1/apps/{id}/test/` | POST | Send test SMS to verify setup |
| `/sms/v1/apps/{id}/balance/` | GET | Check provider balance |

---

### Phase 3 — MCP & Hardening

**Goal:** MCP multi-channel, comprehensive tests, admin, documentation.

---

#### Step 16: MCP `send_message()` — Add SMS Channel

**File:** `mcp_server/tools/messaging.py`

```python
_ALLOWED_CHANNELS = {"WHATSAPP", "TELEGRAM", "SMS"}  # ← Add SMS

# In send_message():
if normalized == "SMS":
    return _send_sms_message(api_key, phone, text)

def _send_sms_message(api_key, phone, text):
    """Send SMS via tenant's configured SMS provider."""
    tenant = _resolve_tenant(api_key)
    adapter = get_channel_adapter("SMS", tenant)
    result = adapter.send_text(phone, text)
    return {
        "success": result["success"],
        "message_id": result.get("message_id"),
        "channel": "SMS",
    }
```

---

#### Step 17: MCP `create_broadcast()` — Add SMS Channel

**File:** `mcp_server/tools/campaigns.py`

```python
_ALLOWED_CHANNELS = {"WHATSAPP", "TELEGRAM", "SMS"}  # ← Add SMS

# In create_broadcast():
if platform == "SMS":
    # SMS broadcasts don't use templates — just recipient list + message text
    broadcast = Broadcast.objects.create(
        tenant=tenant,
        name=name,
        platform="SMS",
        status="SCHEDULED",
        scheduled_time=scheduled_time,
        # SMS-specific: store message text in placeholder_data
        placeholder_data={"message_text": message_text},
    )
```

---

#### Step 18: MCP Server Description Update

**File:** `mcp_server/server.py`

Update server description to mention SMS support alongside WhatsApp and Telegram.

---

#### Step 19: Rate Limiter Integration

Integrate `sms/services/rate_limiter.py` into `SMSMessageSender.send_text()`:
```python
def send_text(self, phone, text, **kwargs):
    if err := self.rate_limiter.check_rate_limit(self.sms_app):
        return {"success": False, "error": f"Rate limited. Retry after {err}s"}
    # ... proceed with send
```

---

#### Step 20: Admin Configuration

**File:** `sms/admin.py`

```python
@admin.register(SMSApp)
class SMSAppAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "provider", "sender_id", "is_active", "messages_sent_today", "daily_limit")
    list_filter = ("provider", "is_active")
    search_fields = ("sender_id", "tenant__name")
    readonly_fields = ("webhook_url", "dlr_webhook_url", "webhook_secret", "messages_sent_today")

@admin.register(SMSOutboundMessage)
class SMSOutboundMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "to_number", "status", "segment_count", "cost", "created_at")
    list_filter = ("status", "sms_app__provider")
    search_fields = ("to_number", "provider_message_id")
    date_hierarchy = "created_at"

@admin.register(SMSWebhookEvent)
class SMSWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "provider", "from_number", "is_processed", "created_at")
    list_filter = ("event_type", "provider", "is_processed")
    date_hierarchy = "created_at"
```

---

#### Step 21: Daily Counter Reset Cron

**File:** `sms/cron.py`

```python
def reset_daily_sms_counters():
    """Reset messages_sent_today for all active SMS apps. Run via django-crontab at midnight."""
    from sms.models import SMSApp
    SMSApp.objects.filter(is_active=True).update(messages_sent_today=0)
```

**Wire into** `jina_connect/settings.py` → `CRONJOBS`:
```python
CRONJOBS += [
    ("0 0 * * *", "sms.cron.reset_daily_sms_counters"),
]
```

---

#### Step 22: Tenant Filter — SMS Product

**File:** `tenants/filters.py`

Replace the TODO stub:
```python
elif value_lower == "sms":
    return queryset.filter(sms_apps__is_active=True).distinct()
```

---

#### Step 23: Comprehensive Test Suite

**Target: 40+ tests across 8 files**

| Test File | Tests | Description |
|---|---|---|
| `test_twilio_provider.py` | 8 | send_sms, parse_inbound, parse_dlr, signature validation, error handling |
| `test_msg91_provider.py` | 6 | send_sms, DLT params, parse_inbound, parse_dlr |
| `test_webhook.py` | 8 | Inbound endpoint (valid/invalid sig, missing app, idempotency), DLR endpoint |
| `test_message_sender.py` | 8 | send_text, send_media fallback, send_keyboard fallback, outbound persistence, inbox entry, rate limit |
| `test_contact_upsert.py` | 4 | New contact from SMS, existing contact match by phone, source update |
| `test_inbox_integration.py` | 4 | Inbound → inbox message, outbound → inbox timeline, DLR status propagation |
| `test_broadcast_integration.py` | 6 | handle_sms_message real send, pricing, credit deduction, batch processing, refund on failure |
| `conftest.py` | — | Shared fixtures: `sms_app_factory`, `twilio_inbound_payload`, `dlr_payload`, `mock_provider` |

**Test pattern — reuse from `telegram/tests/`:**
```python
def _make_sender():
    """Create SMSMessageSender with mocked provider."""
    sms_app = SMSAppFactory(provider="TWILIO")
    with patch("sms.providers.twilio_provider.requests.post") as mock_post:
        mock_post.return_value = MockResponse({"sid": "SM123", "status": "queued"})
        sender = SMSMessageSender(sms_app)
        yield sender, mock_post
```

---

#### Step 24: README Roadmap Update

**File:** `README.md`

Update SMS status from `🚧 Coming soon` to `✅ Multi-BSP (Twilio, MSG91, Fast2SMS)`.

---

## 5. Security Checklist

| # | Check | Implementation |
|---|---|---|
| 1 | **Credential encryption** | `provider_credentials` JSONField stored encrypted via Fernet (`FIELD_ENCRYPTION_KEY`) — same pattern as Telegram `bot_token` |
| 2 | **Webhook signature validation** | Twilio: HMAC-SHA1 of URL+params; MSG91: IP whitelist; Fast2SMS: shared secret header |
| 3 | **CSRF exemption** | Only on webhook views (external POST), all API views use DRF auth |
| 4 | **Tenant isolation** | Every queryset scoped to `tenant=request.user.tenant` |
| 5 | **Rate limiting** | Per-tenant + per-provider via Redis cache + `daily_limit` field |
| 6 | **No credential logging** | `provider_credentials` excluded from `__str__`, admin display, and serializer output |
| 7 | **Idempotent webhooks** | `unique_together = ("sms_app", "provider_message_id", "event_type")` prevents duplicate processing |
| 8 | **India DLT compliance** | `dlt_entity_id` and `dlt_template_id` fields for regulatory compliance |
| 9 | **Input validation** | Phone numbers validated via `phonenumbers` library (already used for contacts) |
| 10 | **Message size limits** | SMS body capped at 1600 chars (10 segments max) — validated in provider |

---

## 6. Testing Plan

### Unit Tests (40+ tests)
See Step 23 above.

### Integration Tests
```bash
# Run all SMS tests
python manage.py test sms/ -v2

# Run broadcast SMS regression
python manage.py test broadcast/tests/ -v2

# Run team inbox regression
python manage.py test team_inbox/tests/ -v2

# Django system check
python manage.py check --deploy
```

### Manual E2E Test Script
Create `sms_e2e_test.py` (mirrors `telegram_e2e_test.py`):
1. Create `SMSApp` with Twilio test credentials
2. Send test SMS to Twilio Magic Number
3. Verify `SMSOutboundMessage` record created
4. Simulate inbound webhook → verify contact + inbox message
5. Simulate DLR webhook → verify status update

---

## 7. Configuration & Environment Variables

```env
# SMS Provider Credentials (stored in SMSApp.provider_credentials per tenant)
# These are NOT global settings — they're per-tenant in DB

# Global SMS Settings
SMS_RATE_LIMIT=100                   # Messages per minute per tenant (already in settings.py)
SMS_MAX_RETRIES=3                    # Max retry attempts for failed sends
SMS_REQUEST_TIMEOUT=30               # HTTP timeout for provider API calls

# Twilio Test Credentials (for E2E testing only)
TWILIO_TEST_ACCOUNT_SID=ACtest...
TWILIO_TEST_AUTH_TOKEN=test...
TWILIO_TEST_FROM_NUMBER=+15005550006

# Field encryption (already exists)
FIELD_ENCRYPTION_KEY=...             # Same key used for Telegram bot_token
```

Add to `jina_connect/settings.py`:
```python
# SMS settings
SMS_MAX_RETRIES = config("SMS_MAX_RETRIES", 3, cast=int)
SMS_REQUEST_TIMEOUT = config("SMS_REQUEST_TIMEOUT", 30, cast=int)
```

---

## 8. Migration & Rollout Checklist

| # | Step | Command |
|---|---|---|
| 1 | Create branch | `git checkout -b sms-channel` |
| 2 | Create `sms/` app skeleton | Step 1 |
| 3 | Generate initial migration | `python manage.py makemigrations sms` |
| 4 | Run migration | `python manage.py migrate` |
| 5 | Add to INSTALLED_APPS | `"sms"` in settings.py |
| 6 | Register URLs | `path("sms/", include("sms.urls"))` in main urls.py |
| 7 | Implement providers | Steps 3–5 |
| 8 | Implement sender service | Step 6 |
| 9 | Implement webhooks | Steps 8–9 |
| 10 | Wire broadcast handler | Step 11 |
| 11 | Wire MCP tools | Steps 16–18 |
| 12 | Run full test suite | `python manage.py test` |
| 13 | Django system check | `python manage.py check --deploy` |
| 14 | Update README | Step 24 |
| 15 | PR review + merge | `sms-channel` → `main` |

---

## 9. Out of Scope (Later)

| Feature | Why Deferred |
|---|---|
| **SMS Templates (DLT)** | Indian DLT template management is complex; Phase 1 uses raw text with optional `dlt_template_id` passthrough |
| **MMS (multimedia SMS)** | Different API endpoints, not all providers support it; add as separate feature |
| **Two-way SMS conversations** | Inbound SMS routing is Phase 1; long-running conversation context (like WhatsApp sessions) is future |
| **SMS short codes** | Requires provider setup and approval; use long codes / alphanumeric sender IDs first |
| **Number pooling** | Twilio Messaging Services handle this; defer until multi-number needed |
| **A2P 10DLC registration** | US-specific compliance; defer until US market is priority |
| **SMS analytics dashboard** | Reuse broadcast dashboard with SMS filter; custom SMS analytics later |
| **Opt-out management** | `STOP` keyword handling; add as separate compliance feature |
| **Country-specific rate cards** | Like WhatsApp's per-country pricing from Meta; add when SMS volume justifies |

---

## 10. Definition of Done

### Phase 1 (Core)
- [ ] `sms/` Django app created with all models, migrations, admin
- [ ] Multi-BSP provider layer (`BaseSMSProvider`, Twilio, MSG91, Fast2SMS)
- [ ] `SMSMessageSender` implements `BaseChannelAdapter` (send_text, send_media fallback, send_keyboard fallback)
- [ ] `"SMS"` registered in `channel_registry` via `SMSConfig.ready()`
- [ ] Inbound + DLR webhook endpoints receiving and persisting events
- [ ] Celery task processing inbound SMS → contact upsert + inbox message
- [ ] DLR processing updates `SMSOutboundMessage` + `BroadcastMessage` status
- [ ] Rate limiter integrated
- [ ] All unit tests passing (20+)

### Phase 2 (Integration)
- [ ] `broadcast/tasks.py` `handle_sms_message()` stub replaced with real sending
- [ ] SMS pricing logic implemented in `_get_sms_message_price()`
- [ ] Team inbox shows SMS messages (inbound + outbound)
- [ ] Chat flow executor routes SMS replies correctly
- [ ] ViewSets + serializers for SMS app management
- [ ] `tenants/filters.py` SMS product filter works
- [ ] Broadcast + inbox regression tests passing

### Phase 3 (MCP + Hardening)
- [ ] MCP `send_message()` supports `channel=SMS`
- [ ] MCP `create_broadcast()` supports `channel=SMS`
- [ ] MCP server description updated
- [ ] 40+ tests passing across 8 test files
- [ ] Admin UI complete for all 3 SMS models
- [ ] Daily counter reset cron configured
- [ ] README roadmap updated
- [ ] E2E test script created and validated
- [ ] Django system check: 0 issues
- [ ] PR reviewed and merged

---

## Appendix: File Change Summary

### New Files (Phase 1–3)

| File | Phase | Description |
|---|---|---|
| `sms/__init__.py` | 1 | Package init |
| `sms/apps.py` | 1 | AppConfig + channel_registry registration |
| `sms/admin.py` | 3 | Admin for SMSApp, SMSOutboundMessage, SMSWebhookEvent |
| `sms/constants.py` | 1 | Provider constants, error maps |
| `sms/models.py` | 1 | SMSApp, SMSWebhookEvent, SMSOutboundMessage |
| `sms/serializers.py` | 2 | API serializers |
| `sms/signals.py` | 1 | post_save → Celery task dispatch |
| `sms/tasks.py` | 1 | process_sms_event_task, inbound/DLR handling |
| `sms/urls.py` | 1 | Webhook + API routes |
| `sms/views.py` | 1 | SMSInboundWebhookView, SMSDLRWebhookView |
| `sms/cron.py` | 3 | Daily counter reset |
| `sms/providers/__init__.py` | 1 | get_sms_provider() factory |
| `sms/providers/base.py` | 1 | BaseSMSProvider ABC + data classes |
| `sms/providers/twilio_provider.py` | 1 | Twilio REST API implementation |
| `sms/providers/msg91_provider.py` | 1 | MSG91 API implementation |
| `sms/providers/fast2sms_provider.py` | 1 | Fast2SMS API implementation |
| `sms/services/__init__.py` | 1 | Package init |
| `sms/services/message_sender.py` | 1 | SMSMessageSender (BaseChannelAdapter) |
| `sms/services/rate_limiter.py` | 1 | Per-tenant SMS rate limiting |
| `sms/migrations/0001_initial.py` | 1 | Auto-generated |
| `sms/tests/__init__.py` | 3 | Package init |
| `sms/tests/conftest.py` | 3 | Shared fixtures |
| `sms/tests/test_twilio_provider.py` | 3 | Twilio provider tests |
| `sms/tests/test_msg91_provider.py` | 3 | MSG91 provider tests |
| `sms/tests/test_webhook.py` | 3 | Webhook endpoint tests |
| `sms/tests/test_message_sender.py` | 3 | Message sender tests |
| `sms/tests/test_contact_upsert.py` | 3 | Contact upsert tests |
| `sms/tests/test_inbox_integration.py` | 3 | Inbox integration tests |
| `sms/tests/test_broadcast_integration.py`| 3 | Broadcast integration tests |
| `sms/viewsets/__init__.py` | 2 | Package init |
| `sms/viewsets/sms_app.py` | 2 | SMSApp CRUD viewset |
| `sms/viewsets/sms_message.py` | 2 | SMS message list/filter viewset |
| `sms_e2e_test.py` | 3 | End-to-end test script |

### Modified Files

| File | Phase | Change |
|---|---|---|
| `jina_connect/settings.py` | 1 | Add `"sms"` to `INSTALLED_APPS`, add `SMS_MAX_RETRIES`, `SMS_REQUEST_TIMEOUT` |
| `jina_connect/urls.py` | 1 | Add `path("sms/", include("sms.urls"))` |
| `broadcast/tasks.py` | 2 | Replace `handle_sms_message()` stub with real implementation |
| `broadcast/models.py` | 2 | Replace `_get_sms_message_price()` stub with real pricing |
| `mcp_server/tools/messaging.py` | 3 | Add `"SMS"` to `_ALLOWED_CHANNELS`, add `_send_sms_message()` |
| `mcp_server/tools/campaigns.py` | 3 | Add `"SMS"` to `_ALLOWED_CHANNELS`, add SMS broadcast branch |
| `mcp_server/server.py` | 3 | Update description for SMS support |
| `tenants/filters.py` | 3 | Replace SMS TODO with `sms_apps__is_active` filter |
| `contacts/models.py` | 1 | Add `SMS` to `ContactSource` choices |
| `README.md` | 3 | Update SMS roadmap status |
| `chat_flow/services/graph_executor.py`| 2 | Add SMS branch in `send_session_message()` |

**Total: ~33 new files, ~11 modified files**
