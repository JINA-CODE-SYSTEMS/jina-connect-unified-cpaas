# RCS Business Messaging — Complete Implementation Plan

> **Roadmap item:** RCS Business Messaging | 🚧 Coming soon | Google RBM, Meta RCS, iOS RCS
>
> **Branch:** `rcs-channel`
>
> **Goal:** First-class RCS Business Messaging channel with **three provider implementations** — Google RBM (primary, Android + iOS via GSMA Universal Profile), Meta RCS (ecosystem bridge, shared assets with WhatsApp), and iOS-optimised delivery (media ratios, button rendering). Supports rich cards, carousels, suggested replies/actions, capability checks, SMS fallback, outbound messaging, inbound webhooks, delivery/read reports, broadcast, team inbox, chat-flow routing, and MCP multi-channel support.
>
> **Provider Strategy:**
> - **Google RBM** — The universal hub. Reaches Android natively and iOS (18+) via GSMA Universal Profile through carrier RCS hubs. Phase 1.
> - **Meta RCS** — The ecosystem bridge. Leverages Meta Business Suite for unified management with WhatsApp. Shared media assets and verified profiles. Phase 2.
> - **iOS RCS** — Not a separate API but a rendering target. Apple adopted GSMA RCS Universal Profile in iOS 18. Messages sent via Google RBM / Meta RCS land in the native iOS Messages app. The adapter auto-adjusts media to 3:2 aspect ratio and adapts button rendering for iPhone. Phase 1 (rendering) + Phase 2 (device-aware routing).

---

## Issue Tracker Map

| Issue | Title | Priority | Phase |
|---|---|---|---|
| NEW | RCS Channel: Core Plumbing & Provider Adapters | P1 | Phase 1 |
| NEW | RCS Channel: Product Integration (Broadcast, Inbox, Chatflow) | P1 | Phase 2 |
| NEW | RCS Channel: MCP Multi-Channel & Hardening | P1 | Phase 3 |
| [#15](../../issues/15) | Multi-channel routing in MCP tools | P1 | Phase 3 |
| [#16](../../issues/16) | Unified inbox across all channels | P1 | Phase 2 |
| [#76](../../issues/76) | Chat flow executor: platform-agnostic routing | P1 | Phase 2 |

---

## Table of Contents

1. [Current State Audit](#1-current-state-audit)
2. [RCS Protocol Overview](#2-rcs-protocol-overview)
3. [Architecture Overview](#3-architecture-overview)
4. [Data Model Changes](#4-data-model-changes)
5. [Step-by-Step Implementation](#5-step-by-step-implementation)
   - [Phase 1 — Core Channel Plumbing](#phase-1--core-channel-plumbing)
   - [Phase 2 — Product Integration](#phase-2--product-integration)
   - [Phase 3 — MCP & Hardening](#phase-3--mcp--hardening)
6. [Security Checklist](#6-security-checklist)
7. [Testing Plan](#7-testing-plan)
8. [Configuration & Environment Variables](#8-configuration--environment-variables)
9. [Migration & Rollout Checklist](#9-migration--rollout-checklist)
10. [Out of Scope (Later)](#10-out-of-scope-later)
11. [Definition of Done](#11-definition-of-done)

---

## 1. Current State Audit

### What already exists (reuse as-is)

| Area | File(s) | Status |
|---|---|---|
| Platform enum — canonical | `jina_connect/platform_choices.py` | ⚠️ Needs `RCS` added to `PlatformChoices` |
| Platform enum — Broadcast | `broadcast/models.py` → `BroadcastPlatformChoices` | ⚠️ Needs `RCS` choice |
| Platform enum — Team Inbox | `team_inbox/models.py` → `MessagePlatformChoices` | ⚠️ Needs `RCS` choice |
| Platform enum — Contacts | `contacts/models.py` → `PreferredChannelChoices` / `ContactSource` | ⚠️ Needs `RCS` choice |
| Rate limit setting | `jina_connect/settings.py` → `PLATFORM_RATE_LIMITS` | ⚠️ Needs `"rcs"` key (Google limit: 300 msg/s per agent) |
| Broadcast router | `broadcast/tasks.py` → `_PLATFORM_HANDLERS` | ⚠️ Needs `"RCS": handle_rcs_message` entry |
| Broadcast pricing stub | `broadcast/models.py` → needs `_get_rcs_message_price()` | ⚠️ Does not exist yet |
| Channel adapter ABC | `wa/adapters/channel_base.py` → `BaseChannelAdapter` | ✅ Exists (send_text, send_media, send_keyboard) |
| Channel registry | `jina_connect/channel_registry.py` → `register_channel()` / `get_channel_adapter()` | ✅ Exists |
| Inbox message factory | `team_inbox/utils/inbox_message_factory.py` → `create_inbox_message()` | ✅ Platform-agnostic |
| Credit manager | `broadcast/services/credit_manager.py` → `BroadcastCreditManager` | ✅ Platform-agnostic |
| Placeholder renderer | `broadcast/utils/placeholder_renderer.py` | ✅ Platform-agnostic |
| Encrypted field | `encrypted_model_fields.EncryptedTextField` | ✅ Used by SMS + Telegram |
| Abstract webhook base | `abstract/models.py` → `BaseWebhookDumps` | ✅ Exists |
| BSP enum in tenants | `tenants/models.py` → `BSPChoices` | ⚠️ Needs `GOOGLE_RBM`, `META_RCS` entries |

### What does NOT exist yet

- No `rcs/` Django app
- No Google RBM API HTTP client (OAuth2 service account auth)
- No `RCSApp` model (agent credentials, webhook config)
- No RCS capability check service (verify user supports RCS before sending)
- No rich card / carousel builder service
- No suggested reply / action builder
- No inbound message webhook receiver
- No delivery / read event webhook receiver
- No outbound RCS message tracking model
- No RCS pricing logic
- No SMS fallback logic (RCS → SMS when user is not RCS-capable)
- No MCP `_send_rcs_message()` helper
- No chat-flow RCS routing
- No team inbox inbound path for RCS
- No broadcast RCS handler

---

## 2. RCS Protocol Overview

### What is RCS Business Messaging (RBM)?

RCS (Rich Communication Services) is the next-generation successor to SMS. It runs natively in the phone's default Messages app (no separate app install). RCS Business Messaging (RBM) lets verified brands send rich, interactive messages to users.

### Key Differences from SMS/Telegram/WhatsApp

| Feature | SMS | Telegram | WhatsApp | RCS |
|---|---|---|---|---|
| Rich cards | ❌ | ❌ | ✅ (templates) | ✅ (standalone + carousel) |
| Suggested replies | ❌ | ✅ (inline keyboard) | ✅ (quick replies) | ✅ (up to 11 chips) |
| Suggested actions | ❌ | ❌ | ❌ | ✅ (dial, map, calendar, URL, share location) |
| Media in-message | ❌ (MMS) | ✅ | ✅ | ✅ (image, video, GIF, PDF) |
| Delivery + read receipts | DLR only | ❌ | ✅ | ✅ (DELIVERED, READ) |
| Typing indicators | ❌ | ❌ | ❌ | ✅ (IS_TYPING) |
| Capability check | ❌ | ❌ | ❌ | ✅ (check if user has RCS) |
| User-initiated first | ❌ | ✅ | Via template | ❌ (agent initiates) |
| Message revocation | ❌ | ❌ | ❌ | ✅ (before delivery) |
| Auth model | API key | Bot token | Access token | OAuth2 service account |
| Fallback needed | N/A | N/A | N/A | ✅ (SMS fallback if no RCS) |

### Google RBM API Summary

**Service endpoint:** `https://rcsbusinessmessaging.googleapis.com`
**Auth:** OAuth2 service account with scope `https://www.googleapis.com/auth/rcsbusinessmessaging`

| Resource | Endpoint | Description |
|---|---|---|
| Send message | `POST /v1/phones/{E.164}/agentMessages` | Send text, rich card, carousel, media |
| Send event | `POST /v1/phones/{E.164}/agentEvents` | Send IS_TYPING, READ indicators |
| Revoke message | `DELETE /v1/phones/{E.164}/agentMessages/{messageId}` | Revoke undelivered message |
| Capability check | `GET /v1/phones/{E.164}/capabilities` | Check if user supports RCS |
| Upload file | `POST /v1/files` | Upload media for rich cards |
| Batch capability | `POST /v1/users:batchGet` | Check RCS for multiple users |

**Webhook payloads (inbound to us):**
- `UserMessage` — text, suggestionResponse, location, userFile
- `UserEvent` — DELIVERED, READ, IS_TYPING
- Verification: `X-Goog-Signature` header = HMAC-SHA512 of base64-decoded `message.data` using `clientToken`

### Meta RCS API Summary

Meta has entered the RCS space via their Business Platform, aligning their RCS implementation with the WhatsApp Business API patterns. This makes onboarding easier for tenants already in the Meta ecosystem.

**Auth:** System User Access Tokens via Meta Business Suite
**Verification:** Tied to Meta Business Manager (BMM) "Verified" status — shared across WhatsApp/Instagram/RCS
**Unique Strength:** Shared media assets with WhatsApp/Instagram; unified "Managed Verification" across Meta properties

| Resource | Endpoint | Description |
|---|---|---|
| Send message | `POST /{phone-number-id}/messages` | Send text, rich card, carousel, media |
| Upload media | `POST /{phone-number-id}/media` | Upload media files |
| Capability check | Via carrier routing | Meta handles carrier-level RCS discovery internally |
| Webhooks | Webhook subscription | Inbound messages + delivery/read events |

**Key Differences from Google RBM:**
- Meta often uses **pre-approved templates** for rich cards rather than building JSON from scratch per message
- Media hosting can be Meta-hosted or external URL (Google requires Google-hosted or public URL)
- Fallback to SMS is handled at the API level (Meta triggers the SMS gateway) vs Google's carrier-level optional fallback
- Meta's webhook format mirrors WhatsApp webhook structure — simpler integration if WhatsApp is already set up

**Provider Credentials Schema:**
```jsonc
// Meta RCS
{
  "access_token": "...",         // System User Access Token from Meta Business Suite
  "app_id": "...",               // Meta App ID
  "app_secret": "...",           // For webhook signature validation (HMAC-SHA256)
  "phone_number_id": "...",      // Business phone number ID
  "business_id": "..."           // Meta Business Manager ID
}
```

### iOS RCS — Rendering & Routing Considerations

Apple adopted the GSMA RCS Universal Profile starting with **iOS 18** (2024). RCS Business Messages sent via Google RBM or Meta RCS land in the native **iOS Messages app** — no separate app required.

**How it works:** iPhone users receive RCS via their carrier's RCS hub. Google RBM agents and Meta RCS agents connect to these same carrier hubs. There is no separate "Apple RCS API" — Apple trusts the carrier-verified sender status.

**iOS-Specific Rendering Differences:**

| Feature | Android (Google Messages) | iOS (Apple Messages) |
|---|---|---|
| Bubble color | Dark blue / custom | Green (standard RCS) |
| Rich cards | Dynamic heights, flexible | Fixed **3:2 aspect ratio**, strict cropping |
| Carousels | Smooth horizontal scroll | Supported (iOS 18.2+), max 10 cards |
| Suggested replies | Quick-tap chips above keyboard | Bottom-docked buttons |
| Suggested actions | Custom chips | Bottom-docked, limited action subset |
| Read receipts | Native | Carrier-dependent |
| Typing indicators | Native | Carrier-dependent |
| Payments | Google Pay integration | Limited (Apple prefers Apple Pay via AMB) |
| Verified sender | Full logo + banner | Verified name + icon (carrier-provided) |

**Implementation implications:**
1. **Media ratios** — Always use **3:2 (width:height)** with a **16:9 safe area** for rich card media to avoid awkward cropping on iPhone
2. **Suggestion text** — Keep to ≤20 chars for iOS (25 for Android) since bottom-docked buttons have less space
3. **Carousel cards** — Require iOS 18.2+ — fall back to standalone card + text for older iOS versions
4. **Capability check** — Google's capability API returns device features; if `RICHCARD_CAROUSEL` is missing, fall back to standalone cards
5. **No separate adapter needed** — iOS routing is handled at the carrier level; our adapter adjusts rendering based on device profile when available

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     RCS Provider APIs                            │
│  ┌────────────┐  ┌────────────────┐  ┌────────────────────────┐ │
│  │ Google RBM  │  │  Meta RCS      │  │  Sinch / Infobip      │ │
│  │ (Phase 1)   │  │  (Phase 2)     │  │  (future)             │ │
│  └─────┬───────┘  └──────┬─────────┘  └───────────┬───────────┘ │
└────────┼─────────────────┼────────────────────────┼─────────────┘
         │ Inbound          ▲ Send API               ▲
         │ + Events         │                        │
         ▼                  │                        │
┌──────────────────────────────────────────────────────────────────┐
│ rcs/views.py                     rcs/services/                  │
│ ┌─────────────────────────┐      ┌────────────────────────────┐ │
│ │ RCSWebhookView           │      │ rcs_client.py (HTTP)       │ │
│ │ • Verify X-Goog-Sig     │      │ capability_checker.py      │ │
│ │ • Parse Pub/Sub envelope │      │ rich_card_builder.py       │ │
│ │ • Persist event row      │      │ suggestion_builder.py      │ │
│ │ • Return 200 fast        │      │ message_sender.py          │ │
│ ├─────────────────────────┤      └──────────────▲─────────────┘ │
│ │ RCSEventWebhookView      │                     │               │
│ │ • DELIVERED / READ        │                     │               │
│ └──────────┬──────────────┘                      │               │
│            │ post_save signal                    │               │
│            ▼                                     │               │
│ ┌─────────────────────────┐      ┌──────────────┴─────────────┐ │
│ │ rcs/tasks.py             │      │ broadcast/tasks.py         │ │
│ │ process_rcs_event_task   │      │ handle_rcs_message()       │ │
│ │ • Parse inbound message  │      │ (with SMS fallback)        │ │
│ │ • Upsert contact         │      │                            │ │
│ │ • Write team inbox msg   │      │ mcp_server/tools/          │ │
│ │ • Route to chat flow     │      │ send_message(channel=RCS)  │ │
│ └─────────────────────────┘      └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Mirror the SMS pattern** — `sms/` app is the closest reference architecture (multi-provider, webhook-based).
2. **Rich-first, text-fallback** — RCS natively supports rich cards, carousels, and suggested actions. `send_keyboard()` maps to suggested replies/actions. `send_media()` maps to rich cards with media.
3. **RCS → SMS fallback** — If capability check returns `404` (user not RCS-capable), automatically fall back to SMS via `get_channel_adapter("SMS", tenant)`.
4. **Reuse all existing abstractions** — `BaseChannelAdapter`, `channel_registry`, `create_inbox_message()`, `BroadcastCreditManager`.
5. **Multi-provider from day one** — Google RBM (Phase 1) + Meta RCS (Phase 2) behind `BaseRCSProvider` interface. Tenant chooses provider per `RCSApp`.
6. **iOS-aware rendering** — When device profile is available, auto-adjust media to 3:2 aspect ratio and cap suggestion text at 20 chars for iPhone recipients.
7. **Tenant isolation** — Every query scoped to tenant; service account credentials stored in `EncryptedTextField`.
8. **Google Pub/Sub webhook model** — Google RBM delivers webhooks via Pub/Sub push. The webhook payload contains a base64-encoded `message.data` field that must be decoded.
9. **Smart device routing** — Capability check detects RCS support + device OS where available. Route to best provider, adjust rendering per device, fall back to SMS for non-RCS devices.

---

## 4. Data Model Changes

### 4.1 Extend Platform Enums

**Files to modify:**
- `jina_connect/platform_choices.py` — add `RCS = "RCS", "RCS"`
- `broadcast/models.py` → `BroadcastPlatformChoices` — add `RCS`
- `team_inbox/models.py` → `MessagePlatformChoices` — add `RCS`
- `contacts/models.py` → `ContactSource` — add `RCS`
- `tenants/models.py` → `BSPChoices` — add `GOOGLE_RBM = "GOOGLE_RBM", "Google RBM"` and `META_RCS = "META_RCS", "Meta RCS"`

**Migration:** Choices-only changes — no schema migration needed (Django handles via TextChoices).

### 4.2 New Django App — `rcs/`

```
rcs/
├── __init__.py
├── admin.py
├── apps.py                       # Register in channel_registry
├── constants.py                  # Provider constants, event types, status maps
├── models.py                     # RCSApp, RCSWebhookEvent, RCSOutboundMessage
├── serializers.py                # API serializers
├── signals.py                    # post_save → Celery task
├── tasks.py                      # process_rcs_event_task
├── urls.py                       # Webhook + API routes
├── views.py                      # Inbound + event webhook receivers
├── cron.py                       # Daily counter reset
├── migrations/
│   └── 0001_initial.py
├── providers/                    # Multi-provider layer
│   ├── __init__.py               # get_rcs_provider() factory
│   ├── base.py                   # BaseRCSProvider ABC + data classes
│   ├── google_rbm_provider.py    # Google RBM REST API implementation
│   └── meta_rcs_provider.py      # Meta RCS API (WhatsApp-style) implementation
├── services/
│   ├── __init__.py
│   ├── message_sender.py         # RCSMessageSender (implements BaseChannelAdapter)
│   ├── rate_limiter.py           # Per-tenant RCS rate limiting
│   ├── capability_checker.py     # Check if phone supports RCS (with caching)
│   ├── rich_card_builder.py      # StandaloneCard + CarouselCard construction
│   └── suggestion_builder.py     # SuggestedReply + SuggestedAction construction
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # Shared fixtures
│   ├── test_google_rbm_provider.py
│   ├── test_meta_rcs_provider.py
│   ├── test_webhook.py
│   ├── test_message_sender.py
│   ├── test_capability_checker.py
│   ├── test_rich_card_builder.py
│   ├── test_suggestion_builder.py
│   ├── test_broadcast_rcs_handler.py
│   ├── test_sms_fallback.py
│   └── test_ios_rendering.py
└── viewsets/
    ├── __init__.py
    ├── rcs_app.py                # CRUD for RCSApp configuration
    └── rcs_message.py            # List/filter outbound messages
```

### 4.3 New Models

#### `RCSApp` — Agent Configuration (per-tenant)

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE, `related_name="rcs_apps"` |
| `provider` | CharField(20) | Choices: `GOOGLE_RBM`, `META_RCS` |
| `provider_credentials` | EncryptedTextField | **Encrypted at rest** — JSON string (see schema below) |
| `agent_id` | CharField(100) | Google RBM agent ID (from Business Communications console) |
| `agent_name` | CharField(255) | Display name of the RCS agent |
| `is_active` | BooleanField | Default `True` |
| `daily_limit` | IntegerField | Default 10000 |
| `messages_sent_today` | IntegerField | Default 0, reset via cron |
| `webhook_url` | URLField | Auto-generated: `{BASE}/rcs/v1/webhooks/{app.id}/` |
| `webhook_client_token` | CharField(64) | Random secret for `X-Goog-Signature` validation (auto-generated) |
| `sms_fallback_enabled` | BooleanField | Default `True` — auto-fallback to SMS when user not RCS-capable |
| `sms_fallback_app` | FK → `sms.SMSApp` | Nullable — which SMS app to use for fallback |
| **Pricing** | | |
| `price_per_message` | DecimalField(10,6) | Default rate per RCS message |
| `price_per_rich_message` | DecimalField(10,6) | Rate for rich card / media messages (US billing model) |
| `created_at` / `updated_at` | DateTimeField | Auto |

**Constraints:** `unique_together = ("tenant", "provider", "agent_id")`

**Provider Credentials Schema:**

```jsonc
// Google RBM
{
  "service_account_json": {           // Full GCP service account JSON key
    "type": "service_account",
    "project_id": "...",
    "private_key_id": "...",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...",
    "client_email": "...",
    "client_id": "...",
    "auth_uri": "...",
    "token_uri": "...",
    "auth_provider_x509_cert_url": "...",
    "client_x509_cert_url": "..."
  }
}

// Meta RCS (future)
{
  "access_token": "...",
  "app_id": "...",
  "app_secret": "..."
}
```

**Auto-generated fields on `save()`:**
```python
def save(self, *args, **kwargs):
    if not self.webhook_client_token:
        self.webhook_client_token = get_random_string(64)
    super().save(*args, **kwargs)
    # Set webhook_url AFTER super().save() so self.pk is available on first create
    base = getattr(settings, "DEFAULT_WEBHOOK_BASE_URL", "")
    if base and self.pk and not self.webhook_url:
        self.webhook_url = f"{base}/rcs/v1/webhooks/{self.pk}/"
        RCSApp.objects.filter(pk=self.pk).update(webhook_url=self.webhook_url)

def increment_daily_counter(self) -> bool:
    """Atomically increment counter only if under daily limit (mirrors SMSApp pattern)."""
    updated = RCSApp.objects.filter(
        pk=self.pk,
        messages_sent_today__lt=F("daily_limit"),
    ).update(
        messages_sent_today=F("messages_sent_today") + 1
    )
    return updated > 0
```

---

#### `RCSWebhookEvent` — Raw Inbound & Event Dumps

Extends `BaseWebhookDumps` (inherits: `payload`, `is_processed`, `processed_at`, `error_message`, `created_at`, `updated_at`, `is_active`).

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE |
| `rcs_app` | FK → `RCSApp` | CASCADE, `related_name="webhook_events"` |
| `event_type` | CharField(30) | `MESSAGE`, `SUGGESTION_RESPONSE`, `LOCATION`, `FILE`, `DELIVERED`, `READ`, `IS_TYPING`, `UNKNOWN` |
| `provider` | CharField(20) | `GOOGLE_RBM`, `META_RCS` |
| `sender_phone` | CharField(20) | User's phone number (E.164) |
| `provider_message_id` | CharField(120) | Google's `messageId` — indexed for DLR lookups |
| `retry_count` | IntegerField | Default 0 |

**Constraints:**
- `unique_together = ("rcs_app", "provider_message_id", "event_type")` — idempotency guard
- Indexes on: `(rcs_app, provider_message_id)`, `(is_processed, created_at)`

---

#### `RCSOutboundMessage` — Outbound Message Tracking

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE |
| `rcs_app` | FK → `RCSApp` | CASCADE |
| `contact` | FK → `TenantContact` | CASCADE, nullable |
| `to_phone` | CharField(20) | Destination phone (E.164) |
| `message_type` | CharField(20) | `TEXT`, `RICH_CARD`, `CAROUSEL`, `MEDIA`, `LOCATION` |
| `message_content` | JSONField | Structured content (text, rich card JSON, carousel JSON) |
| `suggestions` | JSONField | Suggested replies/actions sent with message |
| `provider_message_id` | CharField(120) | Agent-assigned UUID sent to Google, db_index=True |
| `status` | CharField(20) | `PENDING`, `SENT`, `DELIVERED`, `READ`, `FAILED`, `REVOKED` |
| `cost` | DecimalField(10,6) | Calculated cost |
| `traffic_type` | CharField(30) | `TRANSACTION`, `PROMOTION`, `AUTHENTICATION`, `SERVICEREQUEST` |
| `request_payload` | JSONField | Full request sent to provider |
| `response_payload` | JSONField | Provider response |
| `error_code` | CharField(30) | Provider error code |
| `error_message` | TextField | Error details |
| `sent_at` | DateTimeField | Nullable |
| `delivered_at` | DateTimeField | Nullable |
| `read_at` | DateTimeField | Nullable |
| `failed_at` | DateTimeField | Nullable |
| `fallback_sms_id` | FK → `sms.SMSOutboundMessage` | Nullable — links to SMS fallback message |
| `inbox_message` | FK → `team_inbox.Messages` | Nullable |
| `broadcast_message` | FK → `broadcast.BroadcastMessage` | Nullable |
| `created_at` / `updated_at` | DateTimeField | Auto |

**Ordering:** `["-created_at"]`

---

## 5. Step-by-Step Implementation

---

### Phase 1 — Core Channel Plumbing

**Goal:** Django app, Google RBM provider, send API, rich card builder, webhook receivers, capability checks, channel registration.

---

#### Step 1: Create `rcs/` Django App Skeleton

**Files:**
- `rcs/__init__.py`
- `rcs/apps.py` — `RCSConfig(AppConfig)` with `ready()` registering `"RCS"` in channel registry
- `rcs/admin.py` — Register `RCSApp`, `RCSOutboundMessage`, `RCSWebhookEvent`
- `rcs/constants.py` — Provider constants, event types, status maps
- `rcs/models.py` — `RCSApp`, `RCSWebhookEvent`, `RCSOutboundMessage`
- `rcs/signals.py` — `post_save` on `RCSWebhookEvent` → triggers `process_rcs_event_task`
- `rcs/migrations/0001_initial.py` — Auto-generated

**Pattern — `rcs/apps.py`:**
```python
from django.apps import AppConfig


class RCSConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rcs"

    def ready(self):
        import rcs.signals  # noqa: F401

        from jina_connect.channel_registry import register_channel

        def _rcs_adapter_factory(tenant):
            from rcs.models import RCSApp

            rcs_app = RCSApp.objects.filter(
                tenant=tenant,
                is_active=True,
            ).first()
            if not rcs_app:
                raise ValueError(f"Tenant {tenant.pk} has no active RCS app configured.")
            from rcs.services.message_sender import RCSMessageSender

            return RCSMessageSender(rcs_app)

        register_channel("RCS", _rcs_adapter_factory)
```

**Pattern — `rcs/constants.py`:**
```python
PROVIDER_CHOICES = [
    ("GOOGLE_RBM", "Google RBM"),
    ("META_RCS", "Meta RCS"),
]

STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("SENT", "Sent"),
    ("DELIVERED", "Delivered"),
    ("READ", "Read"),
    ("FAILED", "Failed"),
    ("REVOKED", "Revoked"),
]

MESSAGE_TYPE_CHOICES = [
    ("TEXT", "Text"),
    ("RICH_CARD", "Rich Card"),
    ("CAROUSEL", "Carousel"),
    ("MEDIA", "Media"),
    ("LOCATION", "Location"),
]

TRAFFIC_TYPE_CHOICES = [
    ("TRANSACTION", "Transactional"),
    ("PROMOTION", "Promotional"),
    ("AUTHENTICATION", "Authentication"),
    ("SERVICEREQUEST", "Service Request"),
]

# Google RBM error code → our status
GOOGLE_RBM_ERROR_MAP = {
    400: "FAILED",       # Bad Request
    401: "FAILED",       # Unauthorized (invalid credentials)
    403: "FAILED",       # Permission denied / agent not launched
    404: "FAILED",       # User not RCS-capable → trigger SMS fallback
    429: "PENDING",      # Rate limited — retry
    500: "PENDING",      # Server error — retry
}

# Google RBM event type → our event type
EVENT_TYPE_MAP = {
    "text": "MESSAGE",
    "suggestionResponse": "SUGGESTION_RESPONSE",
    "location": "LOCATION",
    "userFile": "FILE",
    "DELIVERED": "DELIVERED",
    "READ": "READ",
    "IS_TYPING": "IS_TYPING",
}
```

**Wiring:**
- Add `"rcs"` to `INSTALLED_APPS` in `jina_connect/settings.py`
- Add `path("rcs/", include("rcs.urls"))` to `jina_connect/urls.py`

---

#### Step 2: Multi-Provider Base Layer

**Files:**
- `rcs/providers/__init__.py` — `get_rcs_provider(rcs_app)` factory
- `rcs/providers/base.py` — `BaseRCSProvider` ABC + data classes

**Pattern — `rcs/providers/base.py`:**
```python
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RCSSendResult:
    """Uniform result from all RCS send operations."""
    success: bool
    provider: str
    message_id: Optional[str] = None
    status: str = "SENT"
    cost: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    is_rcs_capable: bool = True  # False triggers SMS fallback


@dataclass
class RCSInboundMessage:
    """Normalized inbound message from any RCS provider."""
    sender_phone: str       # E.164
    message_id: str
    message_type: str       # "text", "suggestion_response", "location", "file"
    text: Optional[str] = None
    postback_data: Optional[str] = None
    suggestion_text: Optional[str] = None
    location: Optional[Dict] = None  # {"latitude": float, "longitude": float}
    file_info: Optional[Dict] = None  # {"mimeType": str, "fileUri": str, "fileName": str}
    raw_payload: Optional[Dict] = None


@dataclass
class RCSEventReport:
    """Normalized delivery/read event from any RCS provider."""
    sender_phone: str
    message_id: str         # The agent message ID this event refers to
    event_type: str         # DELIVERED, READ, IS_TYPING
    event_id: Optional[str] = None
    raw_payload: Optional[Dict] = None


@dataclass
class RCSCapability:
    """Result of a capability check."""
    phone: str
    is_rcs_enabled: bool
    features: List[str] = field(default_factory=list)  # ["RICHCARD_STANDALONE", "RICHCARD_CAROUSEL", "ACTION_CREATE_CALENDAR_EVENT", etc.]
    raw_response: Optional[Dict] = None


class BaseRCSProvider(ABC):
    """Abstract base for all RCS providers."""

    PROVIDER_NAME: str = "base"

    def __init__(self, rcs_app: "RCSApp"):
        self.rcs_app = rcs_app
        raw = rcs_app.provider_credentials or "{}"
        if isinstance(raw, str):
            try:
                self.credentials = json.loads(raw)
            except (ValueError, TypeError):
                self.credentials = {}
        else:
            self.credentials = raw or {}

    @abstractmethod
    def send_message(
        self,
        to_phone: str,
        content_message: Dict[str, Any],
        *,
        message_id: Optional[str] = None,
        traffic_type: str = "TRANSACTION",
        ttl: Optional[str] = None,
        **kwargs,
    ) -> RCSSendResult:
        """Send an AgentContentMessage to a user."""

    @abstractmethod
    def send_event(
        self,
        to_phone: str,
        event_type: str,
        *,
        message_id: Optional[str] = None,
    ) -> RCSSendResult:
        """Send an agent event (IS_TYPING, READ) to a user."""

    @abstractmethod
    def revoke_message(self, to_phone: str, message_id: str) -> RCSSendResult:
        """Revoke an undelivered message."""

    @abstractmethod
    def check_capability(self, phone: str) -> RCSCapability:
        """Check if a phone number supports RCS."""

    @abstractmethod
    def batch_check_capability(self, phones: List[str]) -> Dict[str, RCSCapability]:
        """Batch check RCS capability for multiple phones."""

    @abstractmethod
    def upload_file(self, file_url: str, thumbnail_url: Optional[str] = None) -> Dict[str, str]:
        """Upload a file to the RCS platform. Returns {"fileName": "...", "thumbnailName": "..."}."""

    @abstractmethod
    def parse_inbound_webhook(self, payload: Dict) -> RCSInboundMessage:
        """Parse provider-specific inbound webhook into normalized form."""

    @abstractmethod
    def parse_event_webhook(self, payload: Dict) -> RCSEventReport:
        """Parse provider-specific event webhook into normalized form."""

    @abstractmethod
    def validate_webhook_signature(self, request) -> bool:
        """Verify that the webhook came from the RCS provider."""
```

**Pattern — `rcs/providers/__init__.py`:**
```python
from rcs.providers.base import BaseRCSProvider

_PROVIDER_REGISTRY = {}


def _lazy_load():
    if _PROVIDER_REGISTRY:
        return
    from rcs.providers.google_rbm_provider import GoogleRBMProvider

    _PROVIDER_REGISTRY.update({
        "GOOGLE_RBM": GoogleRBMProvider,
    })


def get_rcs_provider(rcs_app) -> BaseRCSProvider:
    _lazy_load()
    provider_cls = _PROVIDER_REGISTRY.get(rcs_app.provider)
    if provider_cls is None:
        raise NotImplementedError(f"No RCS provider for '{rcs_app.provider}'")
    return provider_cls(rcs_app)
```

---

#### Step 3: Google RBM Provider

**File:** `rcs/providers/google_rbm_provider.py`

**Implementation details:**
- **Auth:** Google OAuth2 service account → access token via `google.oauth2.service_account.Credentials`
  - Scope: `https://www.googleapis.com/auth/rcsbusinessmessaging`
  - Token cached in Django cache with TTL = 55 min (token expires at 60 min)
- **Send API:** `POST https://rcsbusinessmessaging.googleapis.com/v1/phones/{E.164}/agentMessages?messageId={uuid}&agentId={agent_id}`
  - Body: `AgentMessage` JSON (contentMessage with text/richCard/file + suggestions)
- **Event API:** `POST https://rcsbusinessmessaging.googleapis.com/v1/phones/{E.164}/agentEvents?eventId={uuid}&agentId={agent_id}`
  - Body: `{"eventType": "IS_TYPING"}` or `{"eventType": "READ", "messageId": "..."}`
- **Revoke API:** `DELETE https://rcsbusinessmessaging.googleapis.com/v1/phones/{E.164}/agentMessages/{messageId}?agentId={agent_id}`
- **Capability check:** `GET https://rcsbusinessmessaging.googleapis.com/v1/phones/{E.164}/capabilities?requestId={uuid}&agentId={agent_id}`
  - `404` = not RCS-capable
- **File upload:** `POST https://rcsbusinessmessaging.googleapis.com/upload/v1/files`
- **Webhook signature:** `X-Goog-Signature` = HMAC-SHA512 of base64-decoded `message.data` using `clientToken`
  - Webhook payload is a Pub/Sub push message: `{"message": {"data": "<base64-encoded JSON>"}}`
  - Must base64-decode `message.data` to get the actual `UserMessage` or `UserEvent`
- **Dependencies:**
  - `google-auth` (PyPI package for GCP OAuth2 — must be explicitly pinned in `requirements.txt`)
  - `requests` (already installed)

```python
class GoogleRBMProvider(BaseRCSProvider):
    PROVIDER_NAME = "GOOGLE_RBM"
    API_BASE = "https://rcsbusinessmessaging.googleapis.com"

    def _get_access_token(self) -> str:
        """Get cached OAuth2 access token from service account."""
        cache_key = f"rcs:google:token:{self.rcs_app.pk}"
        token = cache.get(cache_key)
        if token:
            return token

        from google.oauth2 import service_account as sa
        creds = sa.Credentials.from_service_account_info(
            self.credentials.get("service_account_json", {}),
            scopes=["https://www.googleapis.com/auth/rcsbusinessmessaging"],
        )
        creds.refresh(google.auth.transport.requests.Request())
        cache.set(cache_key, creds.token, timeout=3300)  # 55 min
        return creds.token

    def send_message(self, to_phone, content_message, *, message_id=None, traffic_type="TRANSACTION", ttl=None, **kwargs):
        msg_id = message_id or str(uuid.uuid4())
        url = f"{self.API_BASE}/v1/phones/{to_phone}/agentMessages"
        params = {"messageId": msg_id, "agentId": self.rcs_app.agent_id}
        body = {"contentMessage": content_message}
        if traffic_type:
            body["messageTrafficType"] = traffic_type
        if ttl:
            body["ttl"] = ttl

        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        resp = requests.post(url, json=body, params=params, headers=headers, timeout=30)

        if resp.status_code == 404:
            return RCSSendResult(success=False, provider=self.PROVIDER_NAME, is_rcs_capable=False,
                                 error_code="404", error_message="User not RCS-capable")
        if resp.status_code >= 400:
            return RCSSendResult(success=False, provider=self.PROVIDER_NAME,
                                 error_code=str(resp.status_code), error_message=resp.text,
                                 raw_response=resp.json() if resp.text else None)

        return RCSSendResult(success=True, provider=self.PROVIDER_NAME, message_id=msg_id,
                             raw_response=resp.json())

    def check_capability(self, phone):
        url = f"{self.API_BASE}/v1/phones/{phone}/capabilities"
        params = {"requestId": str(uuid.uuid4()), "agentId": self.rcs_app.agent_id}
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)

        if resp.status_code == 404:
            return RCSCapability(phone=phone, is_rcs_enabled=False)

        data = resp.json()
        features = data.get("features", [])
        return RCSCapability(phone=phone, is_rcs_enabled=True, features=features, raw_response=data)

    def batch_check_capability(self, phones):
        """Batch check capability via Google RBM /v1/users:batchGet endpoint."""
        if not phones:
            return {}
        
        url = f"{self.API_BASE}/v1/users:batchGet"
        params = {"requestId": str(uuid.uuid4()), "agentId": self.rcs_app.agent_id}
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        body = {"users": [{"phoneNumber": p} for p in phones]}
        
        try:
            resp = requests.post(url, json=body, params=params, headers=headers, timeout=30)
            if resp.status_code >= 400:
                logger.error(f"Batch capability check failed: {resp.status_code} {resp.text}")
                return {p: RCSCapability(phone=p, is_rcs_enabled=False) for p in phones}
            
            data = resp.json()
            results = {}
            for user in data.get("users", []):
                phone = user["phoneNumber"]
                features = user.get("capabilities", [])
                results[phone] = RCSCapability(
                    phone=phone,
                    is_rcs_enabled=len(features) > 0,
                    features=features,
                    raw_response=user
                )
            # Handle phones not in response (assume not capable)
            for phone in phones:
                if phone not in results:
                    results[phone] = RCSCapability(phone=phone, is_rcs_enabled=False)
            return results
        except Exception as e:
            logger.exception(f"Batch capability check exception: {e}")
            return {p: RCSCapability(phone=p, is_rcs_enabled=False) for p in phones}

    def validate_webhook_signature(self, request):
        client_token = self.rcs_app.webhook_client_token
        if not client_token:
            return False
        signature = request.headers.get("X-Goog-Signature", "")
        if not signature:
            return False

        import base64
        import hashlib
        import hmac

        # Pub/Sub push: body has {"message": {"data": "<base64>"}}
        body = request.body if hasattr(request, "body") else b""
        payload = json.loads(body)
        encoded_data = payload.get("message", {}).get("data", "")
        decoded_data = base64.b64decode(encoded_data)

        computed = base64.b64encode(
            hmac.new(client_token.encode("utf-8"), decoded_data, hashlib.sha512).digest()
        ).decode("utf-8")

        return hmac.compare_digest(signature, computed)
```

---

#### Step 3b: Meta RCS Provider (Phase 2)

**File:** `rcs/providers/meta_rcs_provider.py`

Meta's RCS implementation mirrors the WhatsApp Business API structure. Tenants already using Meta for WhatsApp get shared verification, media assets, and a unified Business Suite experience.

**Implementation details:**
- **Auth:** System User Access Token from Meta Business Suite (long-lived token)
- **Send API:** `POST https://graph.facebook.com/v21.0/{phone_number_id}/messages`
  - Body mirrors WhatsApp Cloud API format — `type`, `to`, `text`/`interactive`/`template`
- **Media upload:** `POST https://graph.facebook.com/v21.0/{phone_number_id}/media`
- **Capability check:** Meta handles RCS discovery internally at the carrier level — no separate capability API. The send request returns a specific error code if the recipient is not RCS-capable.
- **Webhook signature:** `X-Hub-Signature-256` = HMAC-SHA256 of request body using `app_secret`
- **Webhook format:** Same structure as WhatsApp webhooks — `entry[].changes[].value.messages[]` / `entry[].changes[].value.statuses[]`

**Key Mapping Differences from Google:**

| Feature | Google RBM | Meta RCS |
|---|---|---|
| Rich card | Build JSON `richCard.standaloneCard` object per message | Use pre-approved `template_id` OR build `interactive` object |
| Carousel limit | Up to 10 cards | Up to 10 cards |
| Suggested reply | `reply` object with text + postback | `button` object with `type: postback` |
| Media hosting | Google-hosted (via uploadMedia) | Meta-hosted or external URL |
| Fallback | Carrier-level (optional) + API 404 | API-level trigger to SMS Gateway |

```python
class MetaRCSProvider(BaseRCSProvider):
    PROVIDER_NAME = "META_RCS"
    API_BASE = "https://graph.facebook.com/v21.0"

    def _get_phone_number_id(self) -> str:
        return self.credentials.get("phone_number_id", "")

    def _get_headers(self) -> dict:
        token = self.credentials.get("access_token", "")
        return {"Authorization": f"Bearer {token}"}

    def send_message(self, to_phone, content_message, *, message_id=None, traffic_type="TRANSACTION", ttl=None, **kwargs):
        phone_id = self._get_phone_number_id()
        url = f"{self.API_BASE}/{phone_id}/messages"
        
        # Convert RCS content_message to Meta format
        body = self._convert_to_meta_format(to_phone, content_message, traffic_type)
        
        resp = requests.post(url, json=body, headers=self._get_headers(), timeout=30)
        
        if resp.status_code >= 400:
            data = resp.json() if resp.text else {}
            error = data.get("error", {})
            # Error code 131047 = recipient not RCS-capable
            is_capable = error.get("code") != 131047
            return RCSSendResult(
                success=False, provider=self.PROVIDER_NAME,
                is_rcs_capable=is_capable,
                error_code=str(error.get("code", resp.status_code)),
                error_message=error.get("message", resp.text),
                raw_response=data,
            )
        
        data = resp.json()
        msg_id = data.get("messages", [{}])[0].get("id", "")
        return RCSSendResult(success=True, provider=self.PROVIDER_NAME, message_id=msg_id, raw_response=data)

    def _convert_to_meta_format(self, to_phone, content_message, traffic_type):
        """Convert unified RCS content format to Meta's WhatsApp-style API format."""
        body = {
            "messaging_product": "rcs",
            "to": to_phone,
        }
        
        if "text" in content_message and "richCard" not in content_message:
            body["type"] = "text"
            body["text"] = {"body": content_message["text"]}
        elif "richCard" in content_message:
            # Map to Meta interactive format
            body["type"] = "interactive"
            body["interactive"] = self._rich_card_to_interactive(content_message["richCard"])
        else:
            body["type"] = "text"
            body["text"] = {"body": str(content_message)}
        
        return body

    def _rich_card_to_interactive(self, rich_card):
        """Convert Google-style richCard to Meta interactive format."""
        interactive = {"type": "button"}
        
        if "standaloneCard" in rich_card:
            card = rich_card["standaloneCard"].get("cardContent", {})
            if card.get("title"):
                interactive["header"] = {"type": "text", "text": card["title"]}
            if card.get("description"):
                interactive["body"] = {"text": card["description"]}
            if card.get("media"):
                media_url = card["media"].get("contentInfo", {}).get("fileUrl", "")
                if media_url:
                    interactive["header"] = {"type": "image", "image": {"link": media_url}}
            if card.get("suggestions"):
                interactive["action"] = {"buttons": [
                    {"type": "reply", "reply": {"id": s.get("reply", {}).get("postbackData", ""), "title": s.get("reply", {}).get("text", "")}}
                    for s in card["suggestions"] if "reply" in s
                ][:3]}  # Meta limit: 3 buttons per interactive
        
        return interactive

    def check_capability(self, phone):
        # Meta does not expose a separate capability check API.
        # Assume RCS-capable; handle 131047 error on send to detect non-capable.
        return RCSCapability(phone=phone, is_rcs_enabled=True)

    def batch_check_capability(self, phones):
        # Meta doesn't have batch capability check — assume all capable
        return {p: RCSCapability(phone=p, is_rcs_enabled=True) for p in phones}

    def validate_webhook_signature(self, request):
        """Validate Meta webhook using X-Hub-Signature-256 (same as WhatsApp)."""
        import hashlib, hmac
        app_secret = self.credentials.get("app_secret", "")
        if not app_secret:
            return False
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not signature.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(
            app_secret.encode("utf-8"),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def parse_inbound_webhook(self, payload):
        """Parse Meta-style webhook into normalized RCSInboundMessage."""
        entry = (payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value", {})
        msg = (value.get("messages") or [{}])[0]
        
        msg_type = msg.get("type", "text")
        text = None
        postback = None
        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
        elif msg_type == "interactive":
            ir = msg.get("interactive", {})
            if "button_reply" in ir:
                postback = ir["button_reply"].get("id", "")
                text = ir["button_reply"].get("title", "")
                msg_type = "suggestion_response"
        
        return RCSInboundMessage(
            sender_phone=msg.get("from", ""),
            message_id=msg.get("id", ""),
            message_type=msg_type,
            text=text,
            postback_data=postback,
            raw_payload=payload,
        )

    def parse_event_webhook(self, payload):
        entry = (payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value", {})
        status = (value.get("statuses") or [{}])[0]
        
        status_map = {"delivered": "DELIVERED", "read": "READ", "sent": "SENT"}
        return RCSEventReport(
            sender_phone=status.get("recipient_id", ""),
            message_id=status.get("id", ""),
            event_type=status_map.get(status.get("status", ""), "UNKNOWN"),
            raw_payload=payload,
        )

    def revoke_message(self, to_phone, message_id):
        # Meta does not support message revocation for RCS
        return RCSSendResult(success=False, provider=self.PROVIDER_NAME,
                             error_message="Meta RCS does not support message revocation")

    def upload_file(self, file_url, thumbnail_url=None):
        phone_id = self._get_phone_number_id()
        url = f"{self.API_BASE}/{phone_id}/media"
        # Meta requires multipart upload or URL reference
        resp = requests.post(url, json={"messaging_product": "rcs", "url": file_url, "type": "image/jpeg"},
                             headers=self._get_headers(), timeout=30)
        if resp.status_code >= 400:
            return {}
        data = resp.json()
        return {"fileName": data.get("id", "")}
```

**Wire into provider registry — `rcs/providers/__init__.py`:**
```python
def _lazy_load():
    if _PROVIDER_REGISTRY:
        return
    from rcs.providers.google_rbm_provider import GoogleRBMProvider
    from rcs.providers.meta_rcs_provider import MetaRCSProvider

    _PROVIDER_REGISTRY.update({
        "GOOGLE_RBM": GoogleRBMProvider,
        "META_RCS": MetaRCSProvider,
    })
```

---

#### Step 4: Rich Card & Suggestion Builders

**File:** `rcs/services/rich_card_builder.py`

Builds Google RBM `RichCard` payloads from channel-agnostic data.

**iOS rendering note:** Rich card media on iPhone uses a fixed **3:2 aspect ratio** with strict cropping. When the recipient is known to be on iOS (via capability check device profile), use `media_height="MEDIUM"` and ensure images are pre-cropped to 3:2. On Android, dynamic heights are supported.

```python
class RichCardBuilder:
    """Construct RCS RichCard payloads.
    
    iOS considerations:
    - Media is rendered at a fixed 3:2 aspect ratio on iPhone (iOS 18+)
    - Use MEDIUM height for cross-platform compatibility
    - Suggestions text should be ≤20 chars for iOS (vs 25 for Android)
    """

    # iOS-safe defaults
    IOS_SAFE_MEDIA_HEIGHT = "MEDIUM"  # Best cross-platform rendering
    IOS_SUGGESTION_TEXT_LIMIT = 20
    ANDROID_SUGGESTION_TEXT_LIMIT = 25

    @staticmethod
    def standalone_card(
        *,
        title: str = None,
        description: str = None,
        media_url: str = None,
        media_height: str = "MEDIUM",  # SHORT, MEDIUM, TALL
        thumbnail_url: str = None,
        suggestions: list = None,
        orientation: str = "VERTICAL",
        thumbnail_alignment: str = "LEFT",
    ) -> dict:
        card_content = {}
        if title:
            card_content["title"] = title[:200]
        if description:
            card_content["description"] = description[:2000]
        if media_url:
            card_content["media"] = {
                "height": media_height,
                "contentInfo": {"fileUrl": media_url},
            }
            if thumbnail_url:
                card_content["media"]["contentInfo"]["thumbnailUrl"] = thumbnail_url
        if suggestions:
            card_content["suggestions"] = suggestions[:4]  # Max 4 per card
        return {
            "richCard": {
                "standaloneCard": {
                    "cardOrientation": orientation,
                    "thumbnailImageAlignment": thumbnail_alignment,
                    "cardContent": card_content,
                }
            }
        }

    @staticmethod
    def carousel(
        cards: list,  # List of CardContent dicts
        card_width: str = "MEDIUM",  # SMALL (120dp) or MEDIUM (232dp)
    ) -> dict:
        if len(cards) < 2:
            raise ValueError("Carousel requires at least 2 cards")
        if len(cards) > 10:
            raise ValueError("Carousel supports maximum 10 cards")
        return {
            "richCard": {
                "carouselCard": {
                    "cardWidth": card_width,
                    "cardContents": cards[:10],
                }
            }
        }

    @staticmethod
    def card_content(
        *,
        title: str = None,
        description: str = None,
        media_url: str = None,
        media_height: str = "MEDIUM",
        suggestions: list = None,
    ) -> dict:
        content = {}
        if title:
            content["title"] = title[:200]
        if description:
            content["description"] = description[:2000]
        if media_url:
            content["media"] = {
                "height": media_height,
                "contentInfo": {"fileUrl": media_url},
            }
        if suggestions:
            content["suggestions"] = suggestions[:4]
        return content
```

**File:** `rcs/services/suggestion_builder.py`

Builds RCS Suggestions (replies + actions) from channel-agnostic button specs:

```python
class SuggestionBuilder:
    """Construct RCS Suggestion payloads."""

    @staticmethod
    def suggested_reply(text: str, postback_data: str) -> dict:
        return {
            "reply": {
                "text": text[:25],  # RCS limit
                "postbackData": postback_data,
            }
        }

    @staticmethod
    def suggested_action_dial(text: str, phone_number: str, postback_data: str = "") -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "dialAction": {"phoneNumber": phone_number},
            }
        }

    @staticmethod
    def suggested_action_open_url(text: str, url: str, postback_data: str = "") -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "openUrlAction": {"url": url},
            }
        }

    @staticmethod
    def suggested_action_view_location(text: str, lat: float, lng: float, label: str = "", postback_data: str = "") -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "viewLocationAction": {
                    "latLong": {"latitude": lat, "longitude": lng},
                    "label": label,
                },
            }
        }

    @staticmethod
    def suggested_action_calendar(text: str, title: str, description: str, start_time: str, end_time: str, postback_data: str = "") -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "createCalendarEventAction": {
                    "title": title[:100],
                    "description": description[:500],
                    "startTime": start_time,
                    "endTime": end_time,
                },
            }
        }

    @staticmethod
    def suggested_action_share_location(text: str, postback_data: str = "") -> dict:
        return {
            "action": {
                "text": text[:25],
                "postbackData": postback_data,
                "shareLocationAction": {},
            }
        }

    @staticmethod
    def from_channel_agnostic_keyboard(keyboard: list) -> list:
        """Convert BaseChannelAdapter keyboard spec to RCS suggestions.

        Input format (channel-agnostic):
        [
            {"text": "Yes", "callback_data": "yes"},
            {"text": "Call Us", "type": "phone", "phone": "+1234567890"},
            {"text": "Visit", "type": "url", "url": "https://example.com"},
        ]
        """
        suggestions = []
        for btn in keyboard[:11]:  # Max 11 suggestions
            btn_type = btn.get("type", "reply")
            if btn_type == "phone":
                suggestions.append(SuggestionBuilder.suggested_action_dial(
                    btn.get("text", ""), btn.get("phone", ""), btn.get("callback_data", "")
                ))
            elif btn_type == "url":
                suggestions.append(SuggestionBuilder.suggested_action_open_url(
                    btn.get("text", ""), btn.get("url", ""), btn.get("callback_data", "")
                ))
            elif btn_type == "location":
                suggestions.append(SuggestionBuilder.suggested_action_share_location(
                    btn.get("text", "Share Location"), btn.get("callback_data", "")
                ))
            else:
                # Default: suggested reply
                suggestions.append(SuggestionBuilder.suggested_reply(
                    btn.get("text", ""), btn.get("callback_data", btn.get("text", ""))
                ))
        return suggestions
```

---

#### Step 5: Capability Checker Service

**File:** `rcs/services/capability_checker.py`

Caches RCS capability results to avoid redundant API calls:

```python
from django.core.cache import cache


class RCSCapabilityChecker:
    """Check and cache RCS capability for phone numbers."""

    CACHE_TTL = 3600  # 1 hour — balance between freshness and API quota

    def __init__(self, provider):
        self.provider = provider

    def is_rcs_capable(self, phone: str) -> bool:
        """Check if a single phone supports RCS. Uses cache."""
        return self.get_capability(phone).is_rcs_enabled

    def get_capability(self, phone: str):
        """Get full RCSCapability for a phone (including features for device detection). Uses cache."""
        cache_key = f"rcs:cap:full:{self.provider.rcs_app.agent_id}:{phone}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        result = self.provider.check_capability(phone)
        cache.set(cache_key, result, timeout=self.CACHE_TTL)
        return result

    def batch_check(self, phones: list) -> dict:
        """Batch check multiple phones. Returns {phone: RCSCapability}."""
        results = {}
        uncached = []
        for phone in phones:
            cache_key = f"rcs:cap:full:{self.provider.rcs_app.agent_id}:{phone}"
            cached = cache.get(cache_key)
            if cached is not None:
                results[phone] = cached
            else:
                uncached.append(phone)

        if uncached:
            batch_result = self.provider.batch_check_capability(uncached)
            for phone, cap in batch_result.items():
                results[phone] = cap
                cache_key = f"rcs:cap:full:{self.provider.rcs_app.agent_id}:{phone}"
                cache.set(cache_key, cap, timeout=self.CACHE_TTL)

        return results

    @staticmethod
    def invalidate(agent_id: str, phone: str):
        """Invalidate cached capability for a phone (e.g., after 404 on send)."""
        cache.delete(f"rcs:cap:full:{agent_id}:{phone}")
```

---

#### Step 6: RCS Message Sender Service (Channel Adapter)

**File:** `rcs/services/message_sender.py`

Implements `BaseChannelAdapter` interface with RCS-specific features + SMS fallback:

```python
class RCSMessageSender(BaseChannelAdapter):
    """Implements BaseChannelAdapter for RCS channel with SMS fallback."""

    def __init__(self, rcs_app):
        self.rcs_app = rcs_app
        self.provider = get_rcs_provider(rcs_app)
        self.capability_checker = RCSCapabilityChecker(self.provider)

    def send_text(self, chat_id: str, text: str, **kwargs) -> Dict[str, Any]:
        """Send plain text RCS message with optional suggestions."""
        content_message = {"text": text[:3072]}  # RCS text limit
        suggestions = kwargs.get("suggestions")
        if suggestions:
            content_message["suggestions"] = suggestions[:11]

        return self._send_with_fallback(chat_id, content_message, "TEXT", **kwargs)

    def send_media(self, chat_id: str, media_type: str, media_url: str,
                   caption: str = None, **kwargs) -> Dict[str, Any]:
        """Send media as a rich card with optional caption."""
        content_message = RichCardBuilder.standalone_card(
            title=caption,
            media_url=media_url,
            media_height=kwargs.get("media_height", "MEDIUM"),
            thumbnail_url=kwargs.get("thumbnail_url"),
            suggestions=kwargs.get("suggestions"),
        )
        return self._send_with_fallback(chat_id, content_message, "RICH_CARD", **kwargs)

    def send_keyboard(self, chat_id: str, text: str, keyboard: list, **kwargs) -> Dict[str, Any]:
        """Send text with suggested replies/actions."""
        suggestions = SuggestionBuilder.from_channel_agnostic_keyboard(keyboard)
        content_message = {"text": text[:3072], "suggestions": suggestions}
        return self._send_with_fallback(chat_id, content_message, "TEXT", **kwargs)

    def send_rich_card(self, chat_id: str, *, title: str = None, description: str = None,
                       media_url: str = None, suggestions: list = None, **kwargs) -> Dict[str, Any]:
        """RCS-specific: send a standalone rich card."""
        content_message = RichCardBuilder.standalone_card(
            title=title, description=description, media_url=media_url,
            suggestions=suggestions, **{k: v for k, v in kwargs.items() if k in ("media_height", "thumbnail_url", "orientation")},
        )
        return self._send_with_fallback(chat_id, content_message, "RICH_CARD", **kwargs)

    def send_carousel(self, chat_id: str, cards: list, card_width: str = "MEDIUM", **kwargs) -> Dict[str, Any]:
        """RCS-specific: send a carousel of rich cards."""
        content_message = RichCardBuilder.carousel(cards, card_width)
        chip_suggestions = kwargs.get("suggestions")
        if chip_suggestions:
            content_message["suggestions"] = chip_suggestions[:11]
        return self._send_with_fallback(chat_id, content_message, "CAROUSEL", **kwargs)

    def get_channel_name(self) -> str:
        return "RCS"

    def _send_with_fallback(self, chat_id, content_message, message_type, **kwargs):
        """Send via RCS; fallback to SMS if user not capable.
        
        Args:
            chat_id: The recipient phone number (E.164 format). Named ``chat_id`` to
                     match ``BaseChannelAdapter`` convention used by all channels.
        
        Device-aware routing:
            If capability check returns device features, we detect iOS vs Android
            and adjust the payload accordingly (e.g., 3:2 media ratios for iPhone).
        """
        phone = chat_id  # RCS uses E.164 phone as chat_id
        # Check capability before sending (optional optimization)
        if kwargs.get("check_capability", True):
            capability = self.capability_checker.get_capability(phone)
            if not capability.is_rcs_enabled:
                return self._sms_fallback(phone, content_message, message_type, **kwargs)
            
            # iOS-aware rendering adjustment
            device_os = self._detect_device_os(capability)
            if device_os == "ios":
                content_message = self._adjust_for_ios(content_message)

        # Check rate limit
        from rcs.services.rate_limiter import check_rate_limit
        if not check_rate_limit(str(self.rcs_app.pk)):
            return {"success": False, "error": "Rate limited", "channel": "RCS"}

        # Atomic daily limit gate — must pass BEFORE sending (mirrors SMSApp pattern)
        if not self.rcs_app.increment_daily_counter():
            return {"success": False, "error": "Daily limit reached", "channel": "RCS"}

        # Send via RCS
        result = self.provider.send_message(
            to_phone=phone,
            content_message=content_message,
            traffic_type=kwargs.get("traffic_type", "TRANSACTION"),
        )

        # If user not RCS-capable (404), fallback to SMS
        # Decrement the daily counter since the RCS message was never delivered
        if not result.success and not result.is_rcs_capable:
            self.capability_checker.invalidate(self.rcs_app.agent_id, phone)
            self._decrement_daily_counter()
            return self._sms_fallback(phone, content_message, message_type, **kwargs)

        # Persist outbound
        outbound = self._persist_outbound(phone, content_message, message_type, result, **kwargs)

        return {
            "success": result.success,
            "message_id": result.message_id,
            "outbound_id": str(outbound.pk) if outbound else None,
            "channel": "RCS",
            "error": result.error_message,
        }

    def _sms_fallback(self, phone, content_message, message_type, **kwargs):
        """Fall back to SMS channel when user is not RCS-capable."""
        if not self.rcs_app.sms_fallback_enabled or not self.rcs_app.sms_fallback_app:
            return {"success": False, "error": "User not RCS-capable and no SMS fallback configured", "channel": "RCS"}

        from jina_connect.channel_registry import get_channel_adapter
        try:
            sms_adapter = get_channel_adapter("SMS", self.rcs_app.tenant)
        except (ValueError, NotImplementedError):
            return {"success": False, "error": "SMS fallback adapter not available", "channel": "RCS"}

        # Extract text from rich content for SMS fallback
        text = self._extract_text_from_content(content_message)
        result = sms_adapter.send_text(phone, text, **kwargs)
        result["channel"] = "SMS_FALLBACK"
        result["original_channel"] = "RCS"
        return result

    @staticmethod
    def _extract_text_from_content(content_message):
        """Extract plain text from RCS content for SMS fallback.
        
        Handles text messages, standalone cards, and carousels by extracting
        all readable text + media URLs for SMS compatibility.
        """
        if "text" in content_message:
            return content_message["text"]
        
        parts = []
        rich_card = content_message.get("richCard", {})
        
        # Handle standalone cards
        standalone = rich_card.get("standaloneCard", {})
        card_content = standalone.get("cardContent", {})
        if card_content.get("title"):
            parts.append(card_content["title"])
        if card_content.get("description"):
            parts.append(card_content["description"])
        media = card_content.get("media", {}).get("contentInfo", {}).get("fileUrl")
        if media:
            parts.append(f"[Media] {media}")
        
        # Handle carousels (multiple cards) — join all card titles + descriptions
        carousel = rich_card.get("carouselCard", {})
        card_contents = carousel.get("cardContents", [])
        for idx, card in enumerate(card_contents, 1):
            if card.get("title"):
                parts.append(f"Card {idx}: {card['title']}")
            if card.get("description"):
                parts.append(card["description"])
        
        return "\n".join(parts) if parts else "Message from RCS"

    def _persist_outbound(self, phone, content_message, message_type, result, **kwargs):
        """Create RCSOutboundMessage + optional inbox timeline entry."""
        from rcs.models import RCSOutboundMessage
        try:
            outbound = RCSOutboundMessage.objects.create(
                tenant=self.rcs_app.tenant,
                rcs_app=self.rcs_app,
                contact=kwargs.get("contact"),
                to_phone=phone,
                message_type=message_type,
                message_content=content_message,
                suggestions=content_message.get("suggestions", []),
                provider_message_id=result.message_id or "",
                status="SENT" if result.success else "FAILED",
                cost=result.cost or self.rcs_app.price_per_message,
                traffic_type=kwargs.get("traffic_type", "TRANSACTION"),
                request_payload=content_message,
                response_payload=result.raw_response or {},
                error_code=result.error_code or "",
                error_message=result.error_message or "",
                broadcast_message=kwargs.get("broadcast_message"),
            )

            # Create inbox timeline entry
            if kwargs.get("contact") and kwargs.get("create_inbox_entry", True):
                from team_inbox.utils.inbox_message_factory import create_inbox_message
                inbox_msg = create_inbox_message(
                    tenant=self.rcs_app.tenant,
                    contact=kwargs["contact"],
                    platform="RCS",
                    direction="OUTGOING",
                    author=kwargs.get("author", "USER"),
                    content=self._to_inbox_content(content_message, message_type),
                    external_message_id=str(outbound.pk),
                    tenant_user=kwargs.get("tenant_user"),
                    is_read=True,
                )
                outbound.inbox_message = inbox_msg
                outbound.save(update_fields=["inbox_message"])
            return outbound
        except Exception:
            logger.exception("Failed to persist RCS outbound message")
            return None

    @staticmethod
    def _to_inbox_content(content_message, message_type):
        """Convert RCS content to team inbox content format."""
        if message_type == "TEXT":
            return {"type": "text", "body": {"text": content_message.get("text", "")}}
        if message_type in ("RICH_CARD", "CAROUSEL"):
            return {"type": "rcs_rich_card", "body": content_message}
        return {"type": "text", "body": {"text": str(content_message)}}

    # ── Daily Counter Management ──────────────────────────────────────────

    def _decrement_daily_counter(self):
        """Decrement daily counter when an RCS send gets a 404 (not capable) and falls
        back to SMS. The counter was atomically incremented before the send attempt,
        but the RCS message was never delivered, so we release the slot."""
        RCSApp.objects.filter(
            pk=self.rcs_app.pk,
            messages_sent_today__gt=0,
        ).update(
            messages_sent_today=F("messages_sent_today") - 1
        )

    # ── iOS-Aware Rendering ──────────────────────────────────────────────

    @staticmethod
    def _detect_device_os(capability) -> str:
        """Detect device OS from capability features.
        
        Google RBM capability response may include device hints.
        If features contain iOS-specific indicators, return 'ios'.
        Default to 'android' (most common RCS client).
        """
        features = capability.features if capability else []
        # Google capability response includes feature set — iOS devices
        # typically report a subset of Android features. Check for
        # specific Android-only features to differentiate.
        ios_indicators = {"RICHCARD_STANDALONE"}  # iOS supports standalone but no carousel on <18.2
        android_only = {"ACTION_CREATE_CALENDAR_EVENT", "ACTION_DIAL"}
        
        feature_set = set(features)
        if feature_set and not feature_set.intersection(android_only):
            # Has RCS features but none of the Android-specific ones → likely iOS
            return "ios"
        return "android"

    @staticmethod
    def _adjust_for_ios(content_message):
        """Adjust RCS payload for optimal iPhone rendering.
        
        iOS renders rich card media at a fixed 3:2 aspect ratio with strict
        cropping. Suggestions render as bottom-docked buttons with less space.
        
        Adjustments:
        1. Force media height to MEDIUM (best cross-platform result)
        2. Trim suggestion text to ≤20 chars (iPhone button width limit)
        3. If carousel with >10 cards, truncate (iOS 18.2+ max)
        """
        import copy
        msg = copy.deepcopy(content_message)
        
        # Adjust media heights in rich cards for 3:2 safe area
        rich_card = msg.get("richCard", {})
        standalone = rich_card.get("standaloneCard", {})
        card_content = standalone.get("cardContent", {})
        if "media" in card_content:
            card_content["media"]["height"] = "MEDIUM"  # 3:2 safe on iOS
        
        # Carousel card adjustment
        carousel = rich_card.get("carouselCard", {})
        for card in carousel.get("cardContents", []):
            if "media" in card:
                card["media"]["height"] = "MEDIUM"
            # Trim per-card suggestion text for iOS
            for s in card.get("suggestions", []):
                if "reply" in s:
                    s["reply"]["text"] = s["reply"]["text"][:20]
                if "action" in s:
                    s["action"]["text"] = s["action"]["text"][:20]
        
        # Trim top-level suggestion text for iOS button rendering
        for s in msg.get("suggestions", []):
            if "reply" in s:
                s["reply"]["text"] = s["reply"]["text"][:20]
            if "action" in s:
                s["action"]["text"] = s["action"]["text"][:20]
        
        return msg
```

---

#### Step 7: Rate Limiter

**File:** `rcs/services/rate_limiter.py`

**Reuse atomic pattern from `sms/services/rate_limiter.py`:**
```python
from django.conf import settings
from django.core.cache import cache


def check_rate_limit(app_id: str) -> bool:
    limit = (getattr(settings, "PLATFORM_RATE_LIMITS", {}) or {}).get("rcs", 300)
    key = f"rcs:rate:{app_id}"
    cache.add(key, 0, timeout=60)
    current = cache.incr(key)
    return current <= int(limit)
```

---

#### Step 8: Webhook Receivers

**File:** `rcs/views.py`

Two endpoints — message webhook + event webhook (or combined, since Google sends both to same URL).

**Provider-aware routing:** The shared webhook URL dispatches to the correct parsing logic based on `rcs_app.provider`. Google uses Pub/Sub envelopes (`message.data` base64); Meta uses `entry[].changes[]` with `X-Hub-Signature-256`.

```python
@method_decorator(csrf_exempt, name="dispatch")
class RCSWebhookView(View):
    """Receives all RCS webhook notifications (messages + events) for any provider.

    Google RBM sends Pub/Sub push messages:
    POST body: {"message": {"data": "<base64-encoded JSON>"}, "subscription": "..."}

    Meta RCS sends WhatsApp-style webhooks:
    POST body: {"entry": [{"changes": [{"value": {"messages": [...]}}]}]}
    Signature: X-Hub-Signature-256 = HMAC-SHA256 of body with app_secret
    
    Pub/Sub spec: Must return 200 OK within 10s. If 4xx, message is discarded; if 5xx/timeout, 
    message is retried and Pub/Sub sends a subscription confirmation challenge.
    """

    def post(self, request, rcs_app_id):
        try:
            rcs_app = RCSApp.objects.select_related("tenant").get(
                id=rcs_app_id, is_active=True,
            )
        except RCSApp.DoesNotExist:
            return JsonResponse({"ok": True})  # Silent — prevent probing

        provider = get_rcs_provider(rcs_app)

        # ── Provider-specific dispatch ───────────────────────────────────────
        if rcs_app.provider == "META_RCS":
            return self._handle_meta_webhook(request, rcs_app, provider)
        else:
            return self._handle_google_webhook(request, rcs_app, provider)

    def _handle_google_webhook(self, request, rcs_app, provider):
        """Handle Google RBM Pub/Sub push webhook."""
        # Decode Pub/Sub envelope first (before signature validation for confirmations)
        payload = _decode_pubsub_payload(request)
        if not payload:
            return JsonResponse({"ok": True})  # Invalid Pub/Sub frame

        # Check for Pub/Sub subscription confirmation (no signature validation needed)
        pubsub_event = request.headers.get("ce-type", "")
        if pubsub_event == "google.pubsub.v1.PubsubMessage.SUBSCRIPTION_CONFIRMATION":
            logger.info(f"Pub/Sub confirmation received for RCS app {rcs_app.id}")
            return JsonResponse({"ok": True})

        if not provider.validate_webhook_signature(request):
            return JsonResponse({"ok": True})  # Silent reject

        # Classify event type
        event_type = _classify_event(payload)

        # Extract identifiers
        sender_phone = payload.get("senderPhoneNumber", "")
        message_id = payload.get("messageId", "") or payload.get("eventId", "")

        # Idempotent create
        RCSWebhookEvent.objects.get_or_create(
            rcs_app=rcs_app,
            provider_message_id=message_id,
            event_type=event_type,
            defaults={
                "tenant": rcs_app.tenant,
                "provider": rcs_app.provider,
                "sender_phone": sender_phone,
                "payload": payload,
            },
        )
        return JsonResponse({"ok": True})

    def _handle_meta_webhook(self, request, rcs_app, provider):
        """Handle Meta RCS webhook (WhatsApp-style entry[].changes[] format)."""
        if not provider.validate_webhook_signature(request):
            return JsonResponse({"ok": True})  # Silent reject

        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, Exception):
            return JsonResponse({"ok": True})

        # Meta sends messages and statuses in one webhook — process both
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # Process inbound messages
                for msg in value.get("messages", []):
                    message_id = msg.get("id", "")
                    sender_phone = msg.get("from", "")
                    event_type = _classify_meta_message(msg)

                    RCSWebhookEvent.objects.get_or_create(
                        rcs_app=rcs_app,
                        provider_message_id=message_id,
                        event_type=event_type,
                        defaults={
                            "tenant": rcs_app.tenant,
                            "provider": rcs_app.provider,
                            "sender_phone": sender_phone,
                            "payload": msg,
                        },
                    )

                # Process delivery/read status updates
                for status in value.get("statuses", []):
                    message_id = status.get("id", "")
                    recipient = status.get("recipient_id", "")
                    status_map = {"delivered": "DELIVERED", "read": "READ", "sent": "SENT"}
                    event_type = status_map.get(status.get("status", ""), "UNKNOWN")

                    RCSWebhookEvent.objects.get_or_create(
                        rcs_app=rcs_app,
                        provider_message_id=message_id,
                        event_type=event_type,
                        defaults={
                            "tenant": rcs_app.tenant,
                            "provider": rcs_app.provider,
                            "sender_phone": recipient,
                            "payload": status,
                        },
                    )

        return JsonResponse({"ok": True})


def _decode_pubsub_payload(request):
    """Decode Google Pub/Sub push message envelope."""
    import base64
    try:
        body = json.loads(request.body)
        encoded_data = body.get("message", {}).get("data", "")
        if encoded_data:
            return json.loads(base64.b64decode(encoded_data))
        return body  # Direct webhook (non-Pub/Sub)
    except (json.JSONDecodeError, Exception):
        return None


def _classify_event(payload):
    """Classify Google RBM webhook payload into event type."""
    if "text" in payload:
        return "MESSAGE"
    if "suggestionResponse" in payload:
        return "SUGGESTION_RESPONSE"
    if "location" in payload:
        return "LOCATION"
    if "userFile" in payload:
        return "FILE"
    event_type = payload.get("eventType", "")
    if event_type in ("DELIVERED", "READ", "IS_TYPING"):
        return event_type
    return "UNKNOWN"


def _classify_meta_message(msg):
    """Classify Meta RCS inbound message into event type."""
    msg_type = msg.get("type", "text")
    if msg_type == "text":
        return "MESSAGE"
    if msg_type == "interactive":
        return "SUGGESTION_RESPONSE"
    if msg_type == "location":
        return "LOCATION"
    if msg_type in ("image", "video", "audio", "document"):
        return "FILE"
    return "UNKNOWN"
```

---

#### Step 9: Inbound Event Processing (Celery Task)

**File:** `rcs/tasks.py`

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_rcs_event_task(self, event_id: str):
    event = RCSWebhookEvent.objects.select_related("rcs_app", "tenant").get(id=event_id)

    if event.is_processed:
        return

    try:
        if event.event_type in ("MESSAGE", "SUGGESTION_RESPONSE", "LOCATION", "FILE"):
            _handle_inbound_message(event)
        elif event.event_type in ("DELIVERED", "READ"):
            _handle_delivery_event(event)
        # IS_TYPING — ignored (no action needed)

        event.is_processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["is_processed", "processed_at"])
    except Exception as exc:
        event.retry_count += 1
        event.error_message = str(exc)
        event.save(update_fields=["retry_count", "error_message"])
        raise self.retry(exc=exc)


def _handle_inbound_message(event):
    """Process inbound RCS message: upsert contact → inbox → chatflow."""
    provider = get_rcs_provider(event.rcs_app)
    inbound = provider.parse_inbound_webhook(event.payload)

    # 1. Upsert contact by phone
    contact, _ = TenantContact.objects.get_or_create(
        tenant=event.tenant,
        phone=inbound.sender_phone,
        defaults={"source": "RCS", "first_name": ""},
    )

    # 2. Build inbox content
    if inbound.message_type == "text":
        content = {"type": "text", "body": {"text": inbound.text or ""}}
    elif inbound.message_type == "suggestion_response":
        content = {"type": "text", "body": {"text": inbound.suggestion_text or inbound.postback_data or ""}}
    elif inbound.message_type == "location":
        content = {"type": "location", "body": inbound.location or {}}
    elif inbound.message_type == "file":
        content = {"type": "file", "body": inbound.file_info or {}}
    else:
        content = {"type": "text", "body": {"text": str(inbound.raw_payload)}}

    # 3. Create team inbox message
    create_inbox_message(
        tenant=event.tenant,
        contact=contact,
        platform="RCS",
        direction="INCOMING",
        author="CONTACT",
        content=content,
        external_message_id=inbound.message_id,
    )

    # 4. Route to chat flow if assigned
    _route_to_chatflow(event.rcs_app, contact, inbound)


def _handle_delivery_event(event):
    """Update outbound message status from DELIVERED/READ event."""
    provider = get_rcs_provider(event.rcs_app)
    report = provider.parse_event_webhook(event.payload)

    try:
        outbound = RCSOutboundMessage.objects.get(
            rcs_app=event.rcs_app,
            provider_message_id=report.message_id,
        )
    except RCSOutboundMessage.DoesNotExist:
        logger.warning("RCS event for unknown message: %s", report.message_id)
        return

    update_fields = ["status"]
    if report.event_type == "DELIVERED":
        outbound.status = "DELIVERED"
        outbound.delivered_at = timezone.now()
        update_fields.append("delivered_at")
    elif report.event_type == "READ":
        outbound.status = "READ"
        outbound.read_at = timezone.now()
        update_fields.append("read_at")
        # Also set delivered_at if not set
        if not outbound.delivered_at:
            outbound.delivered_at = timezone.now()
            update_fields.append("delivered_at")

    outbound.save(update_fields=update_fields)

    # Update linked BroadcastMessage
    if outbound.broadcast_message:
        bm = outbound.broadcast_message
        status_map = {"DELIVERED": "DELIVERED", "READ": "DELIVERED"}
        bm.status = status_map.get(report.event_type, bm.status)
        bm.save(update_fields=["status"])


def _route_to_chatflow(rcs_app, contact, inbound):
    """Route inbound RCS message to active chat flow session."""
    from chat_flow.models import UserChatFlowSession
    from chat_flow.tasks import process_chatflow_input_task

    session = UserChatFlowSession.objects.filter(
        contact=contact, is_active=True,
    ).select_related("chat_flow").first()
    if not session:
        return

    # Determine user input
    if inbound.message_type == "suggestion_response":
        user_input = inbound.postback_data or inbound.suggestion_text or ""
    elif inbound.message_type == "text":
        user_input = inbound.text or ""
    else:
        return  # Location/file don't trigger chatflow

    if not user_input:
        return

    # Enqueue async task using CORRECT SIGNATURE: (chatflow_id, contact_id, user_input)
    # Do NOT use GraphExecutor directly - must use Celery task to avoid blocking
    process_chatflow_input_task.delay(
        chatflow_id=session.chat_flow.id,
        contact_id=contact.id,
        user_input=user_input,
    )
```

---

#### Step 10: Signals & URL Configuration

**File:** `rcs/signals.py`
```python
@receiver(post_save, sender=RCSWebhookEvent)
def queue_rcs_event_processing(sender, instance, created, **kwargs):
    if created:
        process_rcs_event_task.delay(str(instance.pk))
```

**File:** `rcs/urls.py`
```python
router = DefaultRouter()
router.register(r"v1/apps", RCSAppViewSet, basename="rcs-apps")
router.register(r"v1/messages", RCSOutboundMessageViewSet, basename="rcs-messages")

urlpatterns = [
    path("v1/webhooks/<uuid:rcs_app_id>/", RCSWebhookView.as_view(), name="rcs-webhook"),
    path("", include(router.urls)),
]
```

---

### Phase 2 — Product Integration

**Goal:** Wire RCS into broadcast, team inbox, chat flow, and pricing.

---

#### Step 11: Replace Broadcast `handle_rcs_message()` Stub

**File:** `broadcast/tasks.py`

Add RCS to `_PLATFORM_HANDLERS` dict:
```python
_PLATFORM_HANDLERS = {
    "WHATSAPP": handle_whatsapp_message,
    "TELEGRAM": handle_telegram_message,
    "SMS": handle_sms_message,
    "RCS": handle_rcs_message,  # NEW
}
```

Implement `handle_rcs_message()`:
```python
def handle_rcs_message(message):
    """Send RCS message for a BroadcastMessage. Falls back to SMS if needed."""
    try:
        from rcs.models import RCSApp
        from rcs.services.message_sender import RCSMessageSender

        broadcast = message.broadcast
        rcs_app = RCSApp.objects.filter(
            tenant=broadcast.tenant, is_active=True,
        ).first()
        if not rcs_app:
            return {"success": False, "error": "No active RCS app configured"}

        sender = RCSMessageSender(rcs_app)
        rendered_text = message.rendered_content or broadcast.name

        result = sender.send_text(
            chat_id=str(message.contact.phone),
            text=rendered_text,
            contact=message.contact,
            broadcast_message=message,
            tenant_user=broadcast.created_by,
            create_inbox_entry=False,
            traffic_type="PROMOTION",
        )
        return result
    except Exception as e:
        logger.exception("RCS broadcast message failed: %s", e)
        return {"success": False, "error": str(e)}
```

---

#### Step 12: RCS Pricing

**File:** `broadcast/models.py`

Add RCS branch to the existing `get_message_price()` dispatcher **and** add `_get_rcs_message_price()` implementation:

```python
# In get_message_price() — add before the `else: return Decimal("0")` fallback:
elif self.platform == BroadcastPlatformChoices.RCS:
    return self._get_rcs_message_price()
```

Then add the implementation method:
```python
def _get_rcs_message_price(self):
    """Return RCS message price from app config, considering message type for rich card pricing."""
    from rcs.models import RCSApp
    import json
    rcs_app = RCSApp.objects.filter(
        tenant=self.tenant, is_active=True,
    ).first()
    if not rcs_app:
        return Decimal("0")
    
    # Check if this is a rich card / carousel message (higher price in Google's US billing model)
    is_rich = False
    try:
        content = json.loads(self.message_template or "{}")
        is_rich = "richCard" in content or "richCard" in (content.get("contentMessage") or {})
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Use price_per_rich_message if field exists AND is_rich=True
    if is_rich and hasattr(rcs_app, "price_per_rich_message") and rcs_app.price_per_rich_message:
        return rcs_app.price_per_rich_message
    return rcs_app.price_per_message
```

---

#### Step 12b: `filter_by_product` — Include RCS in "all" filter

**File:** `tenants/filters.py`

The `filter_by_product()` method's `"all"` branch currently only covers `wa_apps` and `sms_apps`. Add RCS (and Telegram, which is also missing):

```python
# In filter_by_product(), update the "all" branch:
elif value_lower == "all":
    return queryset.filter(
        Q(wa_apps__is_active=True)
        | Q(sms_apps__is_active=True)
        | Q(telegram_apps__is_active=True)
        | Q(rcs_apps__is_active=True)
    ).distinct()
```

Also add an explicit `"rcs"` filter branch:
```python
elif value_lower == "rcs":
    return queryset.filter(rcs_apps__is_active=True).distinct()
```

---

#### Step 13: Team Inbox — RCS Message Handling

Already handled in Step 9 (`_handle_inbound_message` calls `create_inbox_message(platform="RCS")`).

**RCS message expiry:**
- RCS has no session window (unlike WhatsApp's 24h)
- `Messages.expires_at` should return `None` for RCS — same as Telegram/SMS

Verify `team_inbox/models.py` → `expires_at` handles RCS:
```python
@property
def expires_at(self):
    if self.platform == MessagePlatformChoices.WHATSAPP:
        return self.timestamp + timedelta(hours=24)
    # Telegram, SMS, RCS, VOICE — no session window
    return None
```

---

#### Step 14: Chat Flow RCS Routing

Already handled in Step 9 (`_route_to_chatflow()`).

**IMPORTANT:** The `GraphExecutor` does **not** use `get_channel_adapter()` for session message dispatch. The function `send_session_message()` in `chat_flow/services/graph_executor.py` uses explicit `if/elif` platform branching (WhatsApp is the default fall-through, Telegram and SMS each have their own `if platform ==` branch).

We must add an RCS branch to `send_session_message()`. Add the following block **before** the SMS branch:

```python
# ── RCS branch ──────────────────────────────────────────────────
if platform == "RCS":
    from rcs.models import RCSApp
    from rcs.services.message_sender import RCSMessageSender

    rcs_phone = getattr(contact, "phone", None)
    if not rcs_phone:
        result["error"] = f"Contact {contact_id} has no phone number for RCS"
        result["status"] = "failed"
        return result

    rcs_app = RCSApp.objects.filter(tenant=contact.tenant, is_active=True).first()
    if not rcs_app:
        result["error"] = "No active RCS app found for tenant"
        result["status"] = "failed"
        return result

    sender = RCSMessageSender(rcs_app)

    message_content = node_data.get("message_content", "")
    body_text = node_data.get("body", "") or message_content
    buttons = node_data.get("buttons", [])
    has_quick_reply = any(
        (b.get("type", "QUICK_REPLY") or "").upper() in ("QUICK_REPLY", "QUICK-REPLY") for b in buttons
    )

    if has_quick_reply:
        send_result = sender.send_keyboard(
            chat_id=str(rcs_phone),
            text=body_text or "Please choose:",
            keyboard=buttons,
            contact=contact,
        )
    else:
        send_result = sender.send_text(
            chat_id=str(rcs_phone),
            text=body_text or message_content,
            contact=contact,
        )

    if send_result.get("success"):
        result["success"] = True
        result["status"] = "queued"
        result["outgoing_message_id"] = send_result.get("message_id")
    else:
        result["status"] = "failed"
        result["error"] = send_result.get("error") or "Failed to send RCS session message"

    return result
```

**File to modify:** `chat_flow/services/graph_executor.py` → `send_session_message()` function (around line 1575, after the Telegram branch and before the SMS branch).

**RCS-specific enhancement:** Suggested replies on flow nodes map to RCS suggestions (not numbered text menu like SMS). The `send_keyboard()` method on `RCSMessageSender` handles this transparently.

---

#### Step 15: Message Revocation Endpoint

**File:** `rcs/viewsets/rcs_app.py` (add custom action)

Add a `revoke_message` endpoint to allow revoking pending messages before delivery:

```python
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

class RCSAppViewSet(ModelViewSet):
    queryset = RCSApp.objects.all()
    serializer_class = RCSAppSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [TenantFilter]

    @action(detail=False, methods=["post"], url_path="revoke_message")
    def revoke_message(self, request):
        """Revoke a pending outbound message before delivery.
        
        POST body: {"outbound_id": "<UUID>"}
        """
        from rcs.models import RCSOutboundMessage
        from rcs.providers import get_rcs_provider
        import uuid as _uuid
        
        # Guard: ensure authenticated user has a tenant
        tenant = getattr(request.user, "tenant", None)
        if not tenant:
            return Response({"error": "No tenant associated with user"}, status=403)
        
        # Validate outbound_id is a valid UUID
        outbound_id = request.data.get("outbound_id")
        if not outbound_id:
            return Response({"error": "outbound_id is required"}, status=400)
        try:
            _uuid.UUID(str(outbound_id))
        except (ValueError, AttributeError):
            return Response({"error": "outbound_id must be a valid UUID"}, status=400)
        
        try:
            message = RCSOutboundMessage.objects.get(
                id=outbound_id,
                rcs_app__tenant=tenant,
            )
        except RCSOutboundMessage.DoesNotExist:
            return Response({"error": "Message not found"}, status=404)
        
        if message.status not in ("PENDING", "SENT"):
            return Response(
                {"error": f"Can only revoke PENDING/SENT messages (current: {message.status})"},
                status=400
            )
        
        provider = get_rcs_provider(message.rcs_app)
        result = provider.revoke_message(message.to_phone, message.provider_message_id)
        
        if result.success:
            message.status = "REVOKED"
            message.save(update_fields=["status"])
            return Response({
                "success": True,
                "status": "REVOKED",
                "message_id": str(message.pk),
            })
        else:
            return Response(
                {
                    "error": f"Revocation failed: {result.error_message}",
                    "error_code": result.error_code,
                },
                status=400
            )
```

**Endpoint:** `POST /rcs/v1/apps/revoke_message/`

**Request body:**
```json
{"outbound_id": "550e8400-e29b-41d4-a716-446655440000"}
```

**Response (success):**
```json
{"success": true, "status": "REVOKED", "message_id": "550e8400-e29b-41d4-a716-446655440000"}
```

---

#### Step 16: Serializers & ViewSets

**File:** `rcs/serializers.py`
```python
class RCSAppSerializer(serializers.ModelSerializer):
    provider_credentials = serializers.JSONField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = RCSApp
        fields = "__all__"
        extra_kwargs = {
            "webhook_client_token": {"write_only": True},
        }

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)
        creds = ret.get("provider_credentials")
        if isinstance(creds, dict):
            ret["provider_credentials"] = json.dumps(creds)
        elif creds is None:
            ret["provider_credentials"] = None
        return ret


class RCSOutboundMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = RCSOutboundMessage
        fields = "__all__"
```

**ViewSets:** Mirror `sms/viewsets/` pattern — CRUD for RCSApp + read-only for outbound messages.

| Endpoint | Method | Description |
|---|---|---|
| `/rcs/v1/apps/` | GET | List tenant's RCS apps |
| `/rcs/v1/apps/` | POST | Create RCS app |
| `/rcs/v1/apps/{id}/` | GET | Retrieve RCS app config |
| `/rcs/v1/apps/{id}/` | PATCH | Update config |
| `/rcs/v1/apps/{id}/` | DELETE | Deactivate RCS app |
| `/rcs/v1/apps/{id}/test/` | POST | Send test RCS message |
| `/rcs/v1/apps/{id}/capability/` | POST | Check if phone is RCS-capable |
| `/rcs/v1/messages/` | GET | List outbound messages |
| `/rcs/v1/messages/{id}/` | GET | Get outbound message detail |

---

### Phase 3 — MCP & Hardening

**Goal:** MCP multi-channel, comprehensive tests, admin, documentation.

---

#### Step 17: MCP `send_message()` — Add RCS Channel

**File:** `mcp_server/tools/messaging.py`

```python
_ALLOWED_CHANNELS = {"WHATSAPP", "TELEGRAM", "SMS", "RCS"}  # ← Add RCS

# In send_message():
if normalized == "RCS":
    return _send_rcs_message(api_key, phone, text)

def _send_rcs_message(api_key, phone, text):
    tenant = _resolve_tenant(api_key)
    adapter = get_channel_adapter("RCS", tenant)
    result = adapter.send_text(phone, text)
    return {
        "success": result["success"],
        "message_id": result.get("message_id"),
        "channel": result.get("channel", "RCS"),  # May be "SMS_FALLBACK"
    }
```

---

#### Step 18: MCP `create_broadcast()` — Add RCS Channel

**File:** `mcp_server/tools/campaigns.py`

Add `"RCS"` to `_ALLOWED_CHANNELS`. RCS broadcasts use plain text (no template system required).

---

#### Step 19: MCP Server Description Update

**File:** `mcp_server/server.py`

Update server instructions to mention RCS support alongside WhatsApp, Telegram, and SMS.

---

#### Step 20: Admin Configuration

**File:** `rcs/admin.py`

```python
@admin.register(RCSApp)
class RCSAppAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "provider", "agent_id", "agent_name", "is_active",
                    "messages_sent_today", "daily_limit", "sms_fallback_enabled", "created_at")
    list_filter = ("provider", "is_active", "sms_fallback_enabled")
    search_fields = ("agent_id", "agent_name", "tenant__name")
    readonly_fields = ("webhook_url", "webhook_client_token", "messages_sent_today")

@admin.register(RCSOutboundMessage)
class RCSOutboundMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "to_phone", "message_type", "status", "traffic_type",
                    "cost", "created_at")
    list_filter = ("status", "message_type", "traffic_type")
    search_fields = ("to_phone", "provider_message_id")
    date_hierarchy = "created_at"

@admin.register(RCSWebhookEvent)
class RCSWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "provider", "sender_phone", "is_processed", "created_at")
    list_filter = ("event_type", "provider", "is_processed")
    date_hierarchy = "created_at"
```

---

#### Step 21: Daily Counter Reset Cron

**File:** `rcs/cron.py`

```python
def reset_daily_rcs_counters():
    from rcs.models import RCSApp
    RCSApp.objects.filter(is_active=True).update(messages_sent_today=0)
```

Wire into `jina_connect/settings.py` → `CRONJOBS`:
```python
CRONJOBS += [
    ("0 0 * * *", "rcs.cron.reset_daily_rcs_counters"),
]
```

---

#### Step 22: Comprehensive Test Suite

**Target: 57+ tests across 11 files**

| Test File | Tests | Description |
|---|---|---|
| `test_google_rbm_provider.py` | 8 | send_message, capability check, signature validation, error handling, OAuth token caching |
| `test_meta_rcs_provider.py` | 7 | send_message, rich card→interactive conversion, webhook signature (X-Hub-Signature-256), parse inbound/event, capability check (always-true), error code 131047 handling |
| `test_webhook.py` | 10 | Pub/Sub decode, Meta webhook decode, message classification (Google + Meta), idempotency, signature validation (Google X-Goog-Signature + Meta X-Hub-Signature-256), event types, **Meta inbound message→task→inbox integration**, **Meta status update→outbound status integration** |
| `test_message_sender.py` | 10 | send_text, send_media→rich card, send_keyboard→suggestions, SMS fallback, rate limit, daily limit, iOS media adjustment, iOS suggestion text trim, device OS detection |
| `test_capability_checker.py` | 6 | Cache hit, cache miss→API call, batch check, invalidation, TTL expiry, device OS detection from features |
| `test_rich_card_builder.py` | 6 | Standalone vertical, standalone horizontal, carousel (2-10 cards), media heights, truncation, iOS 3:2 safe area constants |
| `test_suggestion_builder.py` | 5 | Reply, dial action, URL action, location action, from_channel_agnostic_keyboard conversion |
| `test_broadcast_rcs_handler.py` | 3 | Successful send, no RCS app, SMS fallback on not-capable |
| `test_sms_fallback.py` | 4 | Fallback triggers on 404, text extraction from rich card, fallback disabled, no SMS app |
| `test_ios_rendering.py` | 4 | `_adjust_for_ios()` media height, suggestion text trim, carousel adjustment, passthrough for Android |
| `conftest.py` | — | Shared fixtures: `rcs_app_factory`, `google_rbm_payload`, `meta_rcs_payload`, `pubsub_envelope`, `mock_google_provider`, `mock_meta_provider` |

---

#### Step 23: Tenant Filter — RCS Product

**File:** `tenants/filters.py`

Add:
```python
elif value_lower == "rcs":
    return queryset.filter(rcs_apps__is_active=True).distinct()
```

---

#### Step 24: README Roadmap Update

**File:** `README.md`

Update RCS status from `🚧 Coming soon` to `✅ Google RBM + Meta RCS (rich cards, carousels, suggested replies/actions, SMS fallback, iOS-optimised rendering)`.

---

## 6. Security Checklist

| # | Check | Implementation |
|---|---|---|
| 1 | **Credential encryption** | `provider_credentials` stored as `EncryptedTextField` — same pattern as SMS + Telegram |
| 2 | **Webhook signature** | `X-Goog-Signature` HMAC-SHA512 validated via `hmac.compare_digest()` |
| 3 | **Pub/Sub envelope** | Base64-decode `message.data` before processing — prevent raw payload injection |
| 4 | **OAuth token caching** | Service account tokens cached 55 min (expire at 60) — token never logged |
| 5 | **Tenant isolation** | Every query filtered by `tenant=` or `rcs_app__tenant=` |
| 6 | **Anti-replay** | `unique_together = ("rcs_app", "provider_message_id", "event_type")` prevents duplicates |
| 7 | **Rate limiting** | Redis-backed per-app throttle + daily counter with atomic F() increment |
| 8 | **No credentials in responses** | `provider_credentials` and `webhook_client_token` are `write_only` in serializer |
| 9 | **CSRF exempt** | Only webhook view uses `@csrf_exempt` (public endpoint, validated by HMAC signature) |
| 10 | **Silent webhook responses** | Always return `{"ok": true}` — no internal state leaked |
| 11 | **Suggestion text limits** | All suggestion text capped at 25 chars; postback data at 2048 chars |
| 12 | **Rich card payload limit** | Max 250 KB per rich card payload — validated in builder |

---

## 7. Testing Plan

### Unit Tests (45+ tests)
See Step 21 above.

### Integration Tests
```bash
# Run all RCS tests
pytest rcs/ -v

# Run broadcast RCS regression
pytest broadcast/tests/ -v

# Run team inbox regression
pytest team_inbox/tests/ -v

# Django system check
python manage.py check --deploy
```

### Manual E2E Test Script
Create `rcs_e2e_test.py` (mirrors `telegram_e2e_test.py`):
1. Create `RCSApp` with Google RBM test credentials
2. Check capability for test phone
3. Send test text message (Google RBM)
4. Send test rich card (Google RBM)
5. Send test carousel (Google RBM)
6. Verify `RCSOutboundMessage` records created
7. Simulate inbound webhook (Pub/Sub format) → verify contact + inbox message
8. Simulate DELIVERED/READ events → verify status update
9. Test SMS fallback with non-RCS phone
10. Create `RCSApp` with Meta RCS test credentials
11. Send test text message (Meta RCS)
12. Simulate inbound webhook (Meta format) → verify shared inbox
13. Test iOS rendering adjustment (verify media height set to MEDIUM)

---

## 8. Configuration & Environment Variables

```env
# RCS Provider Credentials (stored in RCSApp.provider_credentials per tenant)
# These are NOT global settings — they're per-tenant in DB

# Global RCS Settings
RCS_RATE_LIMIT=300                    # Messages per minute per tenant (Google allows 300/s per agent)
RCS_MAX_RETRIES=3                     # Max retry attempts for failed sends
RCS_REQUEST_TIMEOUT=30                # HTTP timeout for provider API calls
RCS_CAPABILITY_CACHE_TTL=3600         # Cache RCS capability check results (seconds)

# Google RBM Test Credentials (for E2E testing)
# Store as JSON in RCSApp.provider_credentials

# Field encryption (already exists)
FIELD_ENCRYPTION_KEY=...              # Same key used for SMS + Telegram
```

Add to `jina_connect/settings.py`:
```python
# RCS settings
RCS_MAX_RETRIES = config("RCS_MAX_RETRIES", 3, cast=int)
RCS_REQUEST_TIMEOUT = config("RCS_REQUEST_TIMEOUT", 30, cast=int)
RCS_CAPABILITY_CACHE_TTL = config("RCS_CAPABILITY_CACHE_TTL", 3600, cast=int)

# Update PLATFORM_RATE_LIMITS
PLATFORM_RATE_LIMITS = {
    "whatsapp": config("WA_RATE_LIMIT", 80, cast=int),
    "telegram": config("TELEGRAM_RATE_LIMIT", 30, cast=int),
    "sms": config("SMS_RATE_LIMIT", 100, cast=int),
    "rcs": config("RCS_RATE_LIMIT", 300, cast=int),
}
```

### Dependencies to Add

```
# requirements.txt — add:
google-auth>=2.0.0            # OAuth2 service account authentication for Google RBM
                              # (Also required for google-cloud-storage to support token refresh)
```

**Note:** `google-auth` must be added explicitly to `requirements.txt`. While `google-cloud-storage` (already in requirements.txt) depends on `google-auth`, it does not lock the dependency, so we pin it explicitly here.

---

## 9. Migration & Rollout Checklist

| # | Step | Command |
|---|---|---|
| 1 | Create branch | `git checkout -b rcs-channel` |
| 2 | Create `rcs/` app skeleton | Step 1 |
| 3 | **Add to INSTALLED_APPS (BEFORE makemigrations)** | `"rcs"` in settings.py |
| 4 | Generate initial migration | `python manage.py makemigrations rcs` |
| 5 | Run migration | `python manage.py migrate` |
| 6 | Register URLs | `path("rcs/", include("rcs.urls"))` in main urls.py |
| 7 | Implement Google RBM provider | Step 3 |
| 8 | Implement rich card + suggestion builders | Step 4 |
| 9 | Implement capability checker | Step 5 |
| 10 | Implement message sender + SMS fallback | Step 6 |
| 11 | Implement webhooks | Steps 8–9 |
| 12 | Wire broadcast handler | Step 11 |
| 13 | Wire MCP tools | Steps 16–18 |
| 14 | Run full test suite | `pytest` |
| 15 | Django system check | `python manage.py check --deploy` |
| 16 | Update README | Step 23 |
| 17 | PR review + merge | `rcs-channel` → `main` |

### Post-deployment
- Create first `RCSApp` via admin or API with Google RBM service account
- Configure webhook URL in Google Business Communications Console
- Register test devices in Google Console
- Send test message to verify setup
- Monitor Celery worker logs for event processing

### Rollback plan
- Disable `RCSApp.is_active` — stops outbound
- RCS fields on contacts (none added — uses existing `phone` field) — safe
- The `rcs/` app can be removed from INSTALLED_APPS without affecting other channels

---

## 10. Out of Scope (Later)

| Feature | Why Deferred |
|---|---|
| **Apple Messages for Business (AMB)** | Premium iOS-only channel ("Blue Bubble") with Apple Pay. Different protocol from RCS — separate adapter needed. Consider for high-value iOS segments where branded Apple experience matters more than RCS reach. |
| **RCS agent management API** | Create/update/delete agents via Management API — deferring to manual console setup |
| **Brand verification automation** | Google brand verification process is manual; Meta verification ties into existing BMM status. No API integration needed for MVP |
| **Carrier-specific routing** | Different carriers have different RCS support; defer carrier-level logic (Google/Meta handle this internally) |
| **RCS templates** | RCS doesn't have a formal template approval system like WhatsApp; use plain messages + rich cards |
| **Typing indicators (inbound IS_TYPING)** | Nice-to-have; log but don't process |
| **Message revocation UI** | Revoke API implemented in Google provider (Meta does not support revocation); no UI to trigger it |
| **RCS analytics dashboard** | Reuse broadcast dashboard with RCS filter; custom analytics later |
| **Sinch / Infobip adapters** | Third-party RCS aggregators — add when demand justifies |
| **Rich card templates** | Pre-built reusable card layouts with dynamic fields — Phase 4 |
| **Branded sender profiles** | Metadata store for logos, hero images, brand colors per sender identity — Phase 4 |
| **Payment requests via RCS** | Google Pay (via RBM) or Meta Pay integration — requires `PaymentAdapter` that hooks into `RCSAdapter`. Phase 4 |

---

## 11. Definition of Done

### Phase 1 (Core)
- [ ] `rcs/` Django app created with all models, migrations, admin
- [ ] Google RBM provider: send text, rich card, carousel, media, events
- [ ] Capability checker with caching + device OS detection (iOS vs Android)
- [ ] Rich card + suggestion builders (with iOS 3:2 media ratio safe area)
- [ ] `RCSMessageSender` implements `BaseChannelAdapter` (send_text, send_media, send_keyboard + RCS-specific send_rich_card, send_carousel)
- [ ] iOS-aware rendering: `_adjust_for_ios()` auto-adjusts media heights and suggestion text limits
- [ ] SMS fallback when user not RCS-capable (404 → SMS channel adapter)
- [ ] `"RCS"` registered in `channel_registry` via `RCSConfig.ready()`
- [ ] Webhook endpoint receiving and persisting Pub/Sub push messages
- [ ] Celery task processing inbound messages → contact upsert + inbox
- [ ] DELIVERED/READ event processing updates `RCSOutboundMessage` + `BroadcastMessage`
- [ ] Rate limiter + daily counter integrated
- [ ] All unit tests passing (25+)

### Phase 2 (Integration + Meta RCS)
- [ ] **Meta RCS provider:** send text, rich card, interactive, media, webhooks (WhatsApp-style API)
- [ ] Meta RCS webhook handler: `X-Hub-Signature-256` validation, WhatsApp-style payload parsing
- [ ] Meta provider registered in `_PROVIDER_REGISTRY` → `get_rcs_provider()` factory
- [ ] `broadcast/tasks.py` → `handle_rcs_message()` implemented with SMS fallback
- [ ] RCS pricing logic implemented in `_get_rcs_message_price()` + dispatcher branch in `get_message_price()`
- [ ] Team inbox shows RCS messages (inbound + outbound) from both providers
- [ ] Chat flow executor `send_session_message()` RCS branch implemented
- [ ] ViewSets + serializers for RCS app management
- [ ] `tenants/filters.py` RCS product filter + `filter_by_product("all")` includes RCS
- [ ] Broadcast + inbox regression tests passing
- [ ] Device-aware routing: iOS vs Android rendering adjustments in message sender

### Phase 3 (MCP + Hardening)
- [ ] MCP `send_message()` supports `channel=RCS`
- [ ] MCP `create_broadcast()` supports `channel=RCS`
- [ ] MCP server description updated
- [ ] 55+ tests passing across 11 test files (includes Meta provider + iOS rendering tests)
- [ ] Admin UI complete for all 3 RCS models
- [ ] Daily counter reset cron configured
- [ ] README roadmap updated
- [ ] E2E test script created and validated (Google RBM + Meta RCS)
- [ ] Django system check: 0 issues
- [ ] PR reviewed and merged

### Security (all phases)
- [ ] Service account credentials encrypted at rest; never logged
- [ ] Webhook `X-Goog-Signature` HMAC-SHA512 validated
- [ ] Tenant isolation enforced on every query
- [ ] Silent webhook responses prevent information leakage
- [ ] OAuth tokens cached 55 min, never stored in DB

---

## Appendix: File Change Summary

### New Files (Phase 1–3)

| File | Phase | Description |
|---|---|---|
| `rcs/__init__.py` | 1 | Package init |
| `rcs/apps.py` | 1 | AppConfig + channel_registry registration |
| `rcs/admin.py` | 3 | Admin for RCSApp, RCSOutboundMessage, RCSWebhookEvent |
| `rcs/constants.py` | 1 | Provider constants, event types, status maps |
| `rcs/models.py` | 1 | RCSApp, RCSWebhookEvent, RCSOutboundMessage |
| `rcs/serializers.py` | 2 | API serializers |
| `rcs/signals.py` | 1 | post_save → Celery task dispatch |
| `rcs/tasks.py` | 1 | process_rcs_event_task, inbound/event handling |
| `rcs/urls.py` | 1 | Webhook + API routes |
| `rcs/views.py` | 1 | RCSWebhookView (Pub/Sub push receiver) |
| `rcs/cron.py` | 3 | Daily counter reset |
| `rcs/providers/__init__.py` | 1 | get_rcs_provider() factory |
| `rcs/providers/base.py` | 1 | BaseRCSProvider ABC + data classes |
| `rcs/providers/google_rbm_provider.py` | 1 | Google RBM REST API + OAuth2 |
| `rcs/providers/meta_rcs_provider.py` | 2 | Meta RCS API (WhatsApp-style) + webhook validation |
| `rcs/services/__init__.py` | 1 | Package init |
| `rcs/services/message_sender.py` | 1 | RCSMessageSender (BaseChannelAdapter) |
| `rcs/services/rate_limiter.py` | 1 | Per-tenant RCS rate limiting |
| `rcs/services/capability_checker.py` | 1 | RCS capability check with caching |
| `rcs/services/rich_card_builder.py` | 1 | StandaloneCard + CarouselCard construction |
| `rcs/services/suggestion_builder.py` | 1 | SuggestedReply + SuggestedAction construction |
| `rcs/migrations/0001_initial.py` | 1 | Auto-generated |
| `rcs/tests/__init__.py` | 3 | Package init |
| `rcs/tests/conftest.py` | 3 | Shared fixtures |
| `rcs/tests/test_google_rbm_provider.py` | 3 | Google provider tests |
| `rcs/tests/test_meta_rcs_provider.py` | 3 | Meta provider tests |
| `rcs/tests/test_webhook.py` | 3 | Webhook tests (Pub/Sub + Meta format) |
| `rcs/tests/test_message_sender.py` | 3 | Sender + fallback + iOS adjust tests |
| `rcs/tests/test_capability_checker.py` | 3 | Capability check + device detection tests |
| `rcs/tests/test_rich_card_builder.py` | 3 | Rich card builder tests |
| `rcs/tests/test_suggestion_builder.py` | 3 | Suggestion builder tests |
| `rcs/tests/test_broadcast_rcs_handler.py` | 3 | Broadcast integration tests |
| `rcs/tests/test_sms_fallback.py` | 3 | SMS fallback tests |
| `rcs/tests/test_ios_rendering.py` | 3 | iOS media/suggestion adjustment tests |
| `rcs/viewsets/__init__.py` | 2 | Package init |
| `rcs/viewsets/rcs_app.py` | 2 | RCSApp CRUD viewset |
| `rcs/viewsets/rcs_message.py` | 2 | RCS message list/filter viewset |
| `rcs_e2e_test.py` | 3 | End-to-end test script |

### Modified Files

| File | Phase | Change |
|---|---|---|
| `jina_connect/settings.py` | 1 | Add `"rcs"` to INSTALLED_APPS, add RCS settings, update PLATFORM_RATE_LIMITS |
| `jina_connect/urls.py` | 1 | Add `path("rcs/", include("rcs.urls"))` |
| `jina_connect/platform_choices.py` | 1 | Add `RCS` to PlatformChoices |
| `broadcast/tasks.py` | 2 | Add `"RCS": handle_rcs_message` to _PLATFORM_HANDLERS |
| `broadcast/models.py` | 2 | Add `RCS` to BroadcastPlatformChoices, add `_get_rcs_message_price()` |
| `team_inbox/models.py` | 1 | Add `RCS` to MessagePlatformChoices |
| `contacts/models.py` | 1 | Add `RCS` to ContactSource |
| `tenants/models.py` | 1 | Add `GOOGLE_RBM`, `META_RCS` to BSPChoices |
| `tenants/filters.py` | 3 | Add RCS product filter |
| `mcp_server/tools/messaging.py` | 3 | Add `"RCS"` to _ALLOWED_CHANNELS, add `_send_rcs_message()` |
| `mcp_server/tools/campaigns.py` | 3 | Add `"RCS"` to _ALLOWED_CHANNELS |
| `mcp_server/server.py` | 3 | Update instructions for RCS support |
| `requirements.txt` | 1 | Add `google-auth>=2.0.0` |
| `README.md` | 3 | Update RCS roadmap status |

**Total: ~38 new files, ~14 modified files**
