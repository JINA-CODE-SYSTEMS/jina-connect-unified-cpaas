# Telegram Bot API — Complete Implementation Plan

> **Roadmap item:** Telegram Bot API | 🚧 Coming soon | Native Telegram Bot API
>
> **Branch:** `hement-dev`
>
> **Goal:** First-class Telegram Bot API channel adapter — inbound webhooks, outbound messaging, inline keyboards, broadcast, team inbox, chat-flow routing, and MCP multi-channel support.

---

## Issue Tracker Map

All work is tracked against the following GitHub Issues. **Every step in this plan references the issue that owns it.**

| Issue | Title | Priority | Phase |
|---|---|---|---|
| [#75](../../issues/75) | Phase 0: DRY extraction and channel abstraction | P1 | Phase 0 |
| [#7](../../issues/7) | Telegram Bot API adapter | P1 | Phases 1–2 |
| [#76](../../issues/76) | Chat flow executor: platform-agnostic routing | P1 | Phase 2 |
| [#16](../../issues/16) | Unified inbox across all channels | P1 | Phase 2 |
| [#15](../../issues/15) | Multi-channel routing in MCP tools | P1 | Phase 3 |
| [#60](../../issues/60) | Unified customer profile across all channels | P3 | Deferred |

---

## Table of Contents

1. [Current State Audit](#1-current-state-audit)
2. [Architecture Overview](#2-architecture-overview)
3. [Data Model Changes](#3-data-model-changes)
4. [Step-by-Step Implementation](#4-step-by-step-implementation)
   - [Phase 0 — DRY Extraction & Channel Abstraction](#phase-0--dry-extraction--channel-abstraction) — **[#75](../../issues/75)**
   - [Phase 1 — Core Channel Plumbing](#phase-1--core-channel-plumbing) — **[#7](../../issues/7)**
   - [Phase 2 — Product Integration](#phase-2--product-integration) — **[#7](../../issues/7) · [#16](../../issues/16) · [#76](../../issues/76)**
   - [Phase 3 — MCP & Hardening](#phase-3--mcp--hardening) — **[#15](../../issues/15)**
5. [Security Checklist](#5-security-checklist)
6. [Testing Plan](#6-testing-plan)
7. [Infrastructure & Networking](#7-infrastructure--networking)
8. [Configuration & Environment Variables](#8-configuration--environment-variables)
9. [Migration & Rollout Checklist](#9-migration--rollout-checklist)
10. [Out of Scope (Later)](#10-out-of-scope-later)
11. [Definition of Done](#11-definition-of-done)

---

## 1. Current State Audit

### What already exists

| Area | File(s) | Status |
|---|---|---|
| Platform enum in **Contacts** | `contacts/models.py` → `ContactSource.TELEGRAM` | ✅ Enum exists |
| Platform enum in **Broadcast** | `broadcast/models.py` → `BroadcastPlatformChoices.TELEGRAM` | ✅ Enum exists |
| Platform enum in **Team Inbox** | `team_inbox/models.py` → `MessagePlatformChoices.TELEGRAM` | ✅ Enum exists |
| Platform enum in **Tenants** | `tenants/models.py` → `PlatformChoices.TELEGRAM` | ✅ Enum exists |
| Rate limit setting | `jina_connect/settings.py` → `PLATFORM_RATE_LIMITS["telegram"]` | ✅ Defaults to 30/min |
| Broadcast router | `broadcast/tasks.py` → `route_to_platform_handler()` | ✅ Routes to `handle_telegram_message()` |
| Broadcast sender | `broadcast/tasks.py` → `handle_telegram_message()` | ⚠️ Stub — simulated, no real API call |
| Broadcast pricing | `broadcast/models.py` → `_get_telegram_message_price()` | ⚠️ Stub — returns `Decimal("0.00")` |
| Dashboard filter | `broadcast/viewsets/dashboard.py` | ✅ Accepts `TELEGRAM` filter |
| Telethon library | `.venv/Lib/site-packages/telethon/` | ✅ Installed (MTProto client — **not** Bot API; evaluate if needed) |

### What does NOT exist yet

- No `telegram/` Django app.
- No Telegram Bot API HTTP client.
- No `TelegramBotApp` model (bot token, webhook secret, etc.).
- No webhook receiver endpoint.
- No inbound event processing pipeline.
- No outbound message service (text, media, inline keyboards).
- No team inbox write path for Telegram messages.
- No MCP multi-channel routing.
- No `BaseChannelAdapter` ABC or `wa/adapters/channel_base.py`.
- No `team_inbox/utils/inbox_message_factory.py` shared helper.
- Chat flow executor is hard-coded to WhatsApp.

### Files already created (ahead of tickets — tracked in [#75](../../issues/75))

| File | Phase | Status |
|---|---|---|
| `broadcast/utils/placeholder_renderer.py` | Phase 0A | ✅ Created — needs wiring into `broadcast/tasks.py` |
| `jina_connect/platform_choices.py` | Phase 0B | ✅ Created — needs import in all apps |
| `jina_connect/channel_registry.py` | Phase 0E | ✅ Created — needs adapter registrations |

### DRY / technical debt to fix ([#75](../../issues/75))

| # | Issue | Location |
|---|---|---|
| 1 | Placeholder rendering duplicated 3× | `broadcast/tasks.py` — `_render_template_field`, template buttons/cards |
| 2 | Platform enum defined independently in 4 models | `tenants/`, `broadcast/`, `team_inbox/`, `contacts/` models |
| 3 | `route_to_platform_handler()` is an if/elif chain | `broadcast/tasks.py` |
| 4 | Inbox message creation is a 150-line monolith | `_create_team_inbox_message_from_broadcast()` in `broadcast/tasks.py` |
| 5 | No channel-agnostic adapter base | `wa/adapters/base.py` — `BaseBSPAdapter` is WhatsApp-specific |

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Telegram Bot API                          │
│                  https://api.telegram.org/bot<token>/             │
└──────────────┬───────────────────────────────┬───────────────────┘
               │ Webhook POST                  ▲ sendMessage / etc.
               ▼                               │
┌──────────────────────────┐    ┌──────────────────────────────────┐
│  telegram/views.py       │    │  telegram/services/              │
│  TelegramWebhookView     │    │    bot_client.py (HTTP client)   │
│  • Validate secret token │    │    message_sender.py             │
│  • Persist event row     │    │    media_handler.py              │
│  • Return 200 fast       │    │    keyboard_builder.py           │
└──────────┬───────────────┘    └──────────────▲───────────────────┘
           │ post_save signal                  │
           ▼                                   │
┌──────────────────────────┐    ┌──────────────┴───────────────────┐
│  telegram/tasks.py       │    │  broadcast/tasks.py              │
│  process_tg_event_task   │    │  handle_telegram_message()       │
│  • Parse update type     │    │  (replace stub with real call)   │
│  • Upsert contact        │    │                                  │
│  • Write team inbox msg  │    │  mcp_server/tools/messaging.py   │
│  • Route to chat flow    │    │  send_message(channel=TELEGRAM)  │
└──────────────────────────┘    └──────────────────────────────────┘
```

### Key Principles

1. **Mirror the WhatsApp pattern** — `wa/` app structure is the reference architecture.
2. **Unified primitives** — send, broadcast, inbox, flow routing work identically across channels.
3. **Minimal model extensions** — add Telegram identity to existing `TenantContact`, don't duplicate contacts.
4. **Tenant isolation** — every query scoped to tenant; bot tokens encrypted at rest.

---

## 3. Data Model Changes

### 3.1 Extend `TenantContact` (contacts app)

```python
# contacts/models.py — add fields:
telegram_chat_id = models.BigIntegerField(
    null=True, blank=True, db_index=True,
    help_text="Telegram chat ID for this contact"
)
telegram_username = models.CharField(
    max_length=255, blank=True, null=True,
    help_text="Telegram @username (without @)"
)
```

**Migration:** `contacts/migrations/0004_add_telegram_fields.py`

### 3.2 New Django App — `telegram/`

```
telegram/
├── __init__.py
├── admin.py
├── apps.py
├── constants.py
├── models.py
├── serializers.py
├── signals.py
├── tasks.py
├── urls.py
├── views.py
├── migrations/
│   └── 0001_initial.py
├── services/
│   ├── __init__.py
│   ├── bot_client.py          # Low-level Telegram Bot API HTTP client
│   ├── message_sender.py      # High-level send orchestration
│   ├── media_handler.py       # Photo/document/video upload + send
│   └── keyboard_builder.py    # InlineKeyboardMarkup helpers
├── tests/
│   ├── __init__.py
│   ├── test_bot_client.py
│   ├── test_webhook.py
│   ├── test_message_sender.py
│   ├── test_contact_upsert.py
│   └── test_inbox_integration.py
└── viewsets/
    ├── __init__.py
    ├── bot_app.py
    └── webhook_event.py
```

### 3.3 New Models

#### `TelegramBotApp`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE, `related_name="telegram_bots"` |
| `bot_token` | EncryptedCharField(255) | **Encrypted at rest** via `django-encrypted-model-fields` or custom Fernet wrapper |
| `bot_username` | CharField(255) | e.g. `MyCompanyBot` |
| `bot_user_id` | BigIntegerField | Telegram's numeric user ID for the bot |
| `webhook_secret` | CharField(64) | Random secret for `X-Telegram-Bot-Api-Secret-Token` validation |
| `webhook_url` | URLField | Auto-generated: `{BASE}/telegram/v1/webhooks/{app.id}/` |
| `is_active` | BooleanField | Default `True` |
| `daily_limit` | IntegerField | Default 1000 |
| `messages_sent_today` | IntegerField | Default 0 |
| `created_at` / `updated_at` | DateTimeField | Auto |

**Constraints:** `unique_together = ("tenant", "bot_user_id")`

#### `TelegramWebhookEvent`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE |
| `bot_app` | FK → `TelegramBotApp` | CASCADE, `related_name="webhook_events"` |
| `update_id` | BigIntegerField | Telegram's update ID — indexed, unique per bot_app |
| `event_type` | CharField(30) | `MESSAGE`, `CALLBACK_QUERY`, `EDITED_MESSAGE`, `INLINE_QUERY`, `UNKNOWN` |
| `payload` | JSONField | Raw Telegram Update object |
| `is_processed` | BooleanField | Default `False` |
| `retry_count` | IntegerField | Default 0 |
| `error_message` | TextField | Nullable |
| `processed_at` | DateTimeField | Nullable |
| `created_at` | DateTimeField | Auto |

**Constraints:** `unique_together = ("bot_app", "update_id")` — idempotency guard

#### `TelegramOutboundMessage`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto |
| `tenant` | FK → `Tenant` | CASCADE |
| `bot_app` | FK → `TelegramBotApp` | CASCADE |
| `contact` | FK → `TenantContact` | CASCADE |
| `chat_id` | BigIntegerField | Target chat |
| `message_type` | CharField(20) | `TEXT`, `PHOTO`, `DOCUMENT`, `VIDEO`, `CALLBACK_ANSWER` |
| `request_payload` | JSONField | What was sent to Telegram |
| `provider_message_id` | BigIntegerField | `message_id` from Telegram response |
| `status` | CharField(20) | Uses existing `MessageStatusChoices` |
| `sent_at` | DateTimeField | Nullable |
| `delivered_at` | DateTimeField | Nullable (Telegram doesn't confirm delivery — mark `SENT` = final) |
| `failed_at` | DateTimeField | Nullable |
| `error_message` | TextField | Nullable |
| `inbox_message` | FK → `team_inbox.Messages` | Nullable, links outbound to inbox timeline |
| `created_at` / `updated_at` | DateTimeField | Auto |

---

## 4. Step-by-Step Implementation

---

### Phase 0 — DRY Extraction & Channel Abstraction

> **Issue: [#75](../../issues/75)** · Must land before any Telegram code. Extracts shared utilities and creates the channel adapter abstraction.

---

#### Step 0A: Shared placeholder renderer — [#75](../../issues/75)

**Status:** `broadcast/utils/placeholder_renderer.py` ✅ created ahead of ticket.

**Remaining work:** Refactor `broadcast/tasks.py` to remove 3 duplicate inline placeholder lambdas and import from the shared module.

**Files:** `broadcast/utils/placeholder_renderer.py` ✅ · `broadcast/tasks.py` ⬜

---

#### Step 0B: Canonical platform choices — [#75](../../issues/75)

**Status:** `jina_connect/platform_choices.py` ✅ created ahead of ticket.

**Remaining work:** Update all apps to import from the canonical source instead of defining local enums.

**Files:** `jina_connect/platform_choices.py` ✅ · `tenants/models.py` ⬜ · `broadcast/models.py` ⬜ · `team_inbox/models.py` ⬜ · `contacts/models.py` ⬜

---

#### Step 0C: Shared inbox message factory — [#75](../../issues/75)

Extract final `Messages.objects.create()` call from `_create_team_inbox_message_from_broadcast()` into a reusable helper that both Telegram inbound (Step 6) and broadcast outbound can share.

**Files to create:** `team_inbox/utils/__init__.py` · `team_inbox/utils/inbox_message_factory.py` → `create_inbox_message(tenant, contact, platform, content, direction, ...)`

**Files to modify:** `broadcast/tasks.py` → call `create_inbox_message()` instead of inline `Messages.objects.create()`

---

#### Step 0D: Dispatch registry — [#75](../../issues/75)

Replace `route_to_platform_handler()` if/elif chain with a `_PLATFORM_HANDLERS` dict in `broadcast/tasks.py`.

**Files to modify:** `broadcast/tasks.py`

---

#### Step 0E: `BaseChannelAdapter` ABC + channel registry — [#75](../../issues/75)

**Status:** `jina_connect/channel_registry.py` ✅ created ahead of ticket. `wa/adapters/channel_base.py` ⬜ not started.

1. Create `wa/adapters/channel_base.py` → `BaseChannelAdapter(ABC)` with abstract `send_text`, `send_media`, `send_keyboard`, `get_channel_name`
2. Make `BaseBSPAdapter` inherit `BaseChannelAdapter`
3. Register WhatsApp in channel registry from `wa/apps.py` `ready()`

**Files to create:** `wa/adapters/channel_base.py`

**Files to modify:** `wa/adapters/base.py` · `wa/apps.py`

---

### Phase 1 — Core Channel Plumbing

> **Issue: [#7](../../issues/7)** · Depends on Phase 0 being merged first.

---

#### Step 1: Create the `telegram` Django app scaffold — [#7](../../issues/7)

```bash
python manage.py startapp telegram
```

**Actions:**
1. Create directory structure as shown in §3.2.
2. Add `"telegram"` to `INSTALLED_APPS` in `jina_connect/settings.py`.
3. Create `telegram/apps.py`:
   ```python
   from django.apps import AppConfig

   class TelegramConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "telegram"

       def ready(self):
           import telegram.signals  # noqa: F401
   ```

**Files created/modified:**
- `telegram/__init__.py`
- `telegram/apps.py`
- `jina_connect/settings.py` — add to `INSTALLED_APPS`

---

#### Step 2: Define models and create initial migration — [#7](../../issues/7)

**Actions:**
1. Write `TelegramBotApp`, `TelegramWebhookEvent`, `TelegramOutboundMessage` in `telegram/models.py` (schema from §3.3).
2. Add `telegram_chat_id` and `telegram_username` to `TenantContact` in `contacts/models.py`.
3. Generate migrations:
   ```bash
   python manage.py makemigrations telegram
   python manage.py makemigrations contacts
   python manage.py migrate
   ```

**Files created/modified:**
- `telegram/models.py`
- `contacts/models.py` — 2 new fields
- `telegram/migrations/0001_initial.py` (auto-generated)
- `contacts/migrations/0004_add_telegram_fields.py` (auto-generated)

---

#### Step 3: Build the Telegram Bot API HTTP client — [#7](../../issues/7)

Implement `telegram/services/bot_client.py` — a thin, synchronous `requests`-based wrapper around the Telegram Bot API.

```python
"""
Low-level Telegram Bot API HTTP client.

Usage:
    client = TelegramBotClient(token="123456:ABC-DEF...")
    me = client.get_me()
    client.send_message(chat_id=12345, text="Hello!")
"""
```

**Methods to implement:**

| Method | Telegram API | Priority |
|---|---|---|
| `get_me()` | `getMe` | MVP |
| `set_webhook(url, secret_token)` | `setWebhook` | MVP |
| `delete_webhook()` | `deleteWebhook` | MVP |
| `send_message(chat_id, text, parse_mode, reply_markup)` | `sendMessage` | MVP |
| `send_photo(chat_id, photo, caption, reply_markup)` | `sendPhoto` | MVP |
| `send_document(chat_id, document, caption)` | `sendDocument` | MVP |
| `send_video(chat_id, video, caption)` | `sendVideo` | MVP |
| `answer_callback_query(callback_query_id, text)` | `answerCallbackQuery` | MVP |
| `edit_message_reply_markup(chat_id, message_id, reply_markup)` | `editMessageReplyMarkup` | MVP |
| `get_file(file_id)` | `getFile` | Nice-to-have |

**Design decisions:**
- Base URL: `https://api.telegram.org/bot{token}/`
- Timeout: 30s connect, 60s read.
- Retry: exponential backoff (1s, 2s, 4s) on 429/5xx, max 3 retries.
- Raise `TelegramAPIError(status_code, description)` on non-ok responses.
- Never log the bot token — mask in all log output.

**Files created:**
- `telegram/services/__init__.py`
- `telegram/services/bot_client.py`

---

#### Step 4: Build the webhook receiver view — [#7](../../issues/7)

Implement `telegram/views.py` — public, unauthenticated endpoint that Telegram POSTs updates to.

**Endpoint:** `POST /telegram/v1/webhooks/<uuid:bot_app_id>/`

**Flow:**
```
1. Validate X-Telegram-Bot-Api-Secret-Token header
2. Parse JSON body
3. Extract update_id
4. Check idempotency (skip if update_id already exists for this bot)
5. Classify event_type: MESSAGE | CALLBACK_QUERY | EDITED_MESSAGE | INLINE_QUERY | UNKNOWN
6. Persist TelegramWebhookEvent row
7. Return HTTP 200 immediately (Telegram retries on non-200)
8. post_save signal queues Celery task
```

**Verification endpoint:** `GET /telegram/v1/webhooks/<uuid:bot_app_id>/` — returns 200 OK for health checks.

**Files created:**
- `telegram/views.py`

---

#### Step 5: Register URL routes — [#7](../../issues/7)

**Actions:**
1. Create `telegram/urls.py`:
   ```python
   from django.urls import include, path
   from rest_framework.routers import DefaultRouter
   from telegram.views import TelegramWebhookView
   from telegram.viewsets.bot_app import TelegramBotAppViewSet
   from telegram.viewsets.webhook_event import TelegramWebhookEventViewSet

   router = DefaultRouter()
   router.register(r"v1/bots", TelegramBotAppViewSet, basename="tg-bots")
   router.register(r"v1/webhook-events", TelegramWebhookEventViewSet, basename="tg-webhook-events")

   urlpatterns = [
       path("v1/webhooks/<uuid:bot_app_id>/", TelegramWebhookView.as_view(), name="tg-webhook"),
       path("", include(router.urls)),
   ]
   ```
2. Add to `jina_connect/urls.py`:
   ```python
   path("telegram/", include(("telegram.urls", "telegram"), namespace="telegram")),
   ```

**Files created/modified:**
- `telegram/urls.py`
- `jina_connect/urls.py` — add one `path()` line

---

#### Step 6: Implement the event processor Celery task — [#7](../../issues/7)

Create `telegram/tasks.py` with `process_tg_event_task`.

**Processing logic by event type:**

| Event Type | Action |
|---|---|
| `MESSAGE` | 1. Upsert `TenantContact` by `telegram_chat_id` + tenant<br>2. Create `team_inbox.Messages` (INCOMING, TELEGRAM)<br>3. Check chatflow routing<br>4. Mark event processed |
| `CALLBACK_QUERY` | 1. Resolve contact by chat_id<br>2. Answer callback query (ack to Telegram)<br>3. Extract `callback_data`<br>4. Route to chatflow or log<br>5. Mark event processed |
| `EDITED_MESSAGE` | 1. Log for audit<br>2. Mark event processed |
| `UNKNOWN` | Mark processed with note |

**Contact upsert logic:**
```python
contact, created = TenantContact.objects.update_or_create(
    tenant=bot_app.tenant,
    telegram_chat_id=chat_id,
    defaults={
        "first_name": from_user.get("first_name", ""),
        "last_name": from_user.get("last_name", ""),
        "telegram_username": from_user.get("username"),
        "source": ContactSource.TELEGRAM,
    }
)
```

**Files created:**
- `telegram/tasks.py`

---

#### Step 7: Wire up signals — [#7](../../issues/7)

Create `telegram/signals.py`:
```python
@receiver(post_save, sender=TelegramWebhookEvent)
def queue_tg_event_processing(sender, instance, created, **kwargs):
    if created:
        from telegram.tasks import process_tg_event_task
        process_tg_event_task.delay(str(instance.pk))
```

**Files created:**
- `telegram/signals.py`

---

#### Step 8: Bot app registration management command — [#7](../../issues/7)

Create `telegram/management/commands/register_telegram_webhook.py`:
```bash
python manage.py register_telegram_webhook --bot-app-id <uuid>
```

**What it does:**
1. Reads `TelegramBotApp` from DB.
2. Calls `client.set_webhook(url, secret_token)`.
3. Calls `client.get_me()` and stores `bot_username` + `bot_user_id` if blank.
4. Prints success/failure.

**Files created:**
- `telegram/management/__init__.py`
- `telegram/management/commands/__init__.py`
- `telegram/management/commands/register_telegram_webhook.py`

---

#### Step 9: Admin registration — [#7](../../issues/7)

Register models with Django Admin for debugging:

```python
# telegram/admin.py
@admin.register(TelegramBotApp)
class TelegramBotAppAdmin(admin.ModelAdmin):
    list_display = ("bot_username", "tenant", "is_active", "created_at")
    list_filter = ("is_active", "tenant")
    actions = ["register_webhook", "test_bot_auth"]

@admin.register(TelegramWebhookEvent)
class TelegramWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("update_id", "event_type", "is_processed", "created_at")
    list_filter = ("event_type", "is_processed")
    actions = ["reprocess_events"]

@admin.register(TelegramOutboundMessage)
class TelegramOutboundMessageAdmin(admin.ModelAdmin):
    list_display = ("contact", "message_type", "status", "sent_at")
    list_filter = ("status", "message_type")
```

**Files created:**
- `telegram/admin.py`

---

### Phase 2 — Product Integration

> **Issues: [#7](../../issues/7) · [#16](../../issues/16) · [#76](../../issues/76)**

---

#### Step 10: Build the outbound message sender service — [#7](../../issues/7)

Implement `telegram/services/message_sender.py`:

```python
class TelegramMessageSender:
    """High-level message sending with logging, rate limiting, and model persistence."""

    def __init__(self, bot_app: TelegramBotApp):
        self.bot_app = bot_app
        self.client = TelegramBotClient(token=bot_app.decrypted_token)

    def send_text(self, contact, text, reply_markup=None) -> TelegramOutboundMessage:
        """Send a text message to a contact, persist result."""

    def send_photo(self, contact, photo_url, caption=None, reply_markup=None) -> TelegramOutboundMessage:
        """Send a photo message."""

    def send_document(self, contact, document_url, caption=None) -> TelegramOutboundMessage:
        """Send a document."""
```

**Each method:**
1. Resolve `chat_id` from `contact.telegram_chat_id`.
2. Call `self.client.send_*()`.
3. Create `TelegramOutboundMessage` with status.
4. Create `team_inbox.Messages` with direction=OUTGOING, platform=TELEGRAM.
5. Return the outbound message object.

**Files created:**
- `telegram/services/message_sender.py`

---

#### Step 11: Build inline keyboard helpers — [#7](../../issues/7)

Implement `telegram/services/keyboard_builder.py`:

```python
def build_inline_keyboard(buttons: list[list[dict]]) -> dict:
    """
    Build a Telegram InlineKeyboardMarkup from a simple button spec.

    Args:
        buttons: [[{"text": "Yes", "callback_data": "v1:flow:node_1:abc"}]]

    Returns:
        {"inline_keyboard": [[{"text": "Yes", "callback_data": "v1:flow:node_1:abc"}]]}
    """

def parse_callback_data(data: str) -> dict:
    """
    Parse versioned callback_data string.
    Format: v1:<action>:<id>:<nonce>

    Returns:
        {"version": "v1", "action": "flow", "id": "node_1", "nonce": "abc"}
    """
```

**Safety:** Validate `callback_data` ≤ 64 bytes (Telegram limit). Strict regex parsing.

**Files created:**
- `telegram/services/keyboard_builder.py`

---

#### Step 12: Replace broadcast stub with real Telegram sending — [#7](../../issues/7)

**Modify** `broadcast/tasks.py` → `handle_telegram_message()`:

```python
def handle_telegram_message(message):
    """Handle Telegram message sending — real implementation."""
    try:
        from telegram.models import TelegramBotApp
        from telegram.services.message_sender import TelegramMessageSender

        # Resolve bot app for this tenant
        bot_app = TelegramBotApp.objects.filter(
            tenant=message.broadcast.tenant,
            is_active=True,
        ).first()

        if not bot_app:
            return {"success": False, "error": "No active Telegram bot configured for this tenant"}

        sender = TelegramMessageSender(bot_app)
        contact = message.contact

        if not contact.telegram_chat_id:
            return {"success": False, "error": f"Contact {contact.id} has no telegram_chat_id"}

        # Build message from broadcast placeholder_data
        text = message.broadcast.placeholder_data.get("message", "")
        media_url = message.broadcast.placeholder_data.get("media_url")

        if media_url:
            result = sender.send_photo(contact, photo_url=media_url, caption=text)
        else:
            result = sender.send_text(contact, text=text)

        return {
            "success": True,
            "message_id": str(result.provider_message_id),
            "response": "Telegram message sent",
        }

    except Exception as e:
        error_msg = f"Telegram sending failed: {str(e)}"
        logger.exception(error_msg)
        return {"success": False, "error": error_msg}
```

**Files modified:**
- `broadcast/tasks.py` — replace stub `handle_telegram_message()`

---

#### Step 13: Implement Telegram pricing logic — [#7](../../issues/7)

**Modify** `broadcast/models.py` → `_get_telegram_message_price()`:

```python
def _get_telegram_message_price(self):
    """
    Telegram Bot API message pricing.
    Telegram itself is free, but the platform may charge a per-message fee.
    """
    # Check tenant-level Telegram pricing configuration
    bot_app = self.tenant.telegram_bots.filter(is_active=True).first()
    if bot_app and hasattr(bot_app, "message_price"):
        return bot_app.message_price
    return Decimal("0.00")  # Free by default (Telegram doesn't charge)
```

**Files modified:**
- `broadcast/models.py` — replace pricing stub

---

#### Step 14: Team inbox — inbound message handling — [#16](../../issues/16)

Ensure `telegram/tasks.py` → `process_tg_event_task` creates proper inbox messages:

```python
from team_inbox.models import (
    AuthorChoices,
    MessageDirectionChoices,
    MessageEventIds,
    MessagePlatformChoices,
    Messages,
)

# Inside MESSAGE handler:
event_id = MessageEventIds.objects.create()
Messages.objects.create(
    tenant=bot_app.tenant,
    message_id=event_id,
    content={
        "type": msg_type,   # "text", "photo", "document", etc.
        "body": {"text": text_content},
        "media": media_info,  # if applicable
    },
    direction=MessageDirectionChoices.INCOMING,
    platform=MessagePlatformChoices.TELEGRAM,
    author=AuthorChoices.CONTACT,
    contact=contact,
)
```

**Files modified:**
- `telegram/tasks.py` — already started in Step 6, add team inbox writes

---

#### Step 15: Team inbox — Telegram message expiry — [#7](../../issues/7)

Update `team_inbox/models.py` → `Messages.expires_at` property:

```python
@property
def expires_at(self):
    if self.platform == MessagePlatformChoices.WHATSAPP:
        return int(self.timestamp.timestamp() + 24 * 60 * 60)
    # Telegram messages don't expire — no 24-hour window
    if self.platform == MessagePlatformChoices.TELEGRAM:
        return None
    return None
```

**Files modified:**
- `team_inbox/models.py` — update `expires_at` (already returns `None` for non-WA, but add explicit Telegram comment for clarity)

---

#### Step 16: Chat flow routing for Telegram callbacks — [#76](../../issues/76)

In `telegram/tasks.py`, add callback_query handling that invokes existing chat flow engine:

```python
def _handle_callback_query(event, bot_app):
    """Route Telegram callback queries to chat flow engine."""
    callback = event.payload.get("callback_query", {})
    chat_id = callback.get("message", {}).get("chat", {}).get("id")
    callback_data = callback.get("data", "")
    callback_query_id = callback.get("id")

    # Acknowledge immediately
    client = TelegramBotClient(token=bot_app.decrypted_token)
    client.answer_callback_query(callback_query_id)

    # Parse callback_data
    parsed = parse_callback_data(callback_data)

    # Resolve contact
    contact = TenantContact.objects.filter(
        tenant=bot_app.tenant,
        telegram_chat_id=chat_id,
    ).first()

    if not contact:
        return

    # Route to chatflow (reuses wa/tasks.py pattern)
    _handle_chatflow_routing_telegram(contact, parsed)
```

**Files modified:**
- `telegram/tasks.py` — add callback handler

---

#### Step 17: Serializers — [#7](../../issues/7)

Create `telegram/serializers.py`:

```python
class TelegramBotAppSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramBotApp
        fields = ["id", "bot_username", "bot_user_id", "is_active", "webhook_url", "daily_limit", "created_at"]
        read_only_fields = ["id", "bot_user_id", "webhook_url", "created_at"]

class TelegramBotAppCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramBotApp
        fields = ["bot_token"]
    # Validate token via getMe call on create

class TelegramWebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramWebhookEvent
        fields = "__all__"

class TelegramOutboundMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramOutboundMessage
        fields = "__all__"
```

**Files created:**
- `telegram/serializers.py`

---

#### Step 18: ViewSets — [#7](../../issues/7)

Create CRUD viewsets for bot management and event audit:

**`telegram/viewsets/bot_app.py`:**
- `TelegramBotAppViewSet` — CRUD for bot apps.
- Custom actions: `register_webhook`, `test_auth`, `deactivate`.

**`telegram/viewsets/webhook_event.py`:**
- `TelegramWebhookEventViewSet` — read-only list + retry action.
- Filtering by `event_type`, `is_processed`, date range.

**Files created:**
- `telegram/viewsets/__init__.py`
- `telegram/viewsets/bot_app.py`
- `telegram/viewsets/webhook_event.py`

---

### Phase 3 — MCP & Hardening

> **Issue: [#15](../../issues/15)**

---

#### Step 19: MCP multi-channel routing — messaging tools — [#15](../../issues/15)

**Modify** `mcp_server/tools/messaging.py`:

Add `channel` parameter (default `"WHATSAPP"`) to `send_template` and `send_message`:

```python
@mcp.tool()
def send_message(
    ctx: Context,
    phone: str,
    text: str,
    channel: str = "WHATSAPP",  # NEW — "WHATSAPP" | "TELEGRAM"
):
    """Send a message to a phone number or Telegram user.

    Args:
        phone: Phone number (WhatsApp) or Telegram chat ID.
        text: The message text to send.
        channel: Channel — WHATSAPP (default) or TELEGRAM.
    """
    tenant = resolve_tenant(ctx)

    if channel.upper() == "TELEGRAM":
        return _send_telegram_message(tenant, phone, text)
    else:
        return _send_whatsapp_message(tenant, phone, text)
```

**Files modified:**
- `mcp_server/tools/messaging.py` — add channel routing
- `mcp_server/tools/campaigns.py` — add channel parameter to `create_broadcast`

---

#### Step 20: MCP multi-channel routing — campaign tools — [#15](../../issues/15)

**Modify** `mcp_server/tools/campaigns.py`:

Add `channel` parameter to `create_broadcast`:
```python
@mcp.tool()
def create_broadcast(
    ctx: Context,
    name: str,
    template_name: str,
    phones: list[str],
    channel: str = "WHATSAPP",  # NEW
    ...
):
```

**Files modified:**
- `mcp_server/tools/campaigns.py`

---

#### Step 21: Update MCP server description — [#15](../../issues/15)

**Modify** `mcp_server/server.py`:

```python
mcp = FastMCP(
    "Jina Connect",
    instructions=(
        "Jina Connect is a multi-channel CPaaS supporting WhatsApp and Telegram. "
        "Use these tools to send messages, manage templates, contacts, "
        "broadcasts, and providers. Specify channel='TELEGRAM' to route via Telegram Bot API."
    ),
    ...
)
```

**Files modified:**
- `mcp_server/server.py`

---

#### Step 22: Rate limiting and throttling — [#7](../../issues/7)

Implement per-bot rate limiting using existing `PLATFORM_RATE_LIMITS["telegram"]` from settings:

```python
# telegram/services/rate_limiter.py
from django.core.cache import cache

def check_rate_limit(bot_app_id: str, limit_per_minute: int = 30) -> bool:
    """Returns True if under limit, False if throttled."""
    key = f"tg_rate:{bot_app_id}"
    current = cache.get(key, 0)
    if current >= limit_per_minute:
        return False
    cache.set(key, current + 1, timeout=60)
    return True
```

**Files created:**
- `telegram/services/rate_limiter.py`

---

#### Step 23: Constants and error mapping — [#7](../../issues/7)

Create `telegram/constants.py`:

```python
# Telegram Bot API error codes → our status
TELEGRAM_ERROR_MAP = {
    400: "FAILED",       # Bad Request
    401: "FAILED",       # Unauthorized (invalid token)
    403: "BLOCKED",      # Bot blocked by user
    404: "FAILED",       # Chat not found
    429: "PENDING",      # Rate limited — retry
    500: "PENDING",      # Server error — retry
}

# Event type classification
UPDATE_TYPE_MAP = {
    "message": "MESSAGE",
    "edited_message": "EDITED_MESSAGE",
    "callback_query": "CALLBACK_QUERY",
    "inline_query": "INLINE_QUERY",
}

# Callback data format
CALLBACK_DATA_VERSION = "v1"
CALLBACK_DATA_MAX_LENGTH = 64  # Telegram limit
```

**Files created:**
- `telegram/constants.py`

---

#### Step 24: Media handler service — [#7](../../issues/7)

Implement `telegram/services/media_handler.py`:

```python
class TelegramMediaHandler:
    """Handle media upload and download for Telegram messages."""

    def __init__(self, client: TelegramBotClient):
        self.client = client

    def download_file(self, file_id: str) -> tuple[bytes, str]:
        """Download a file from Telegram servers. Returns (content, filename)."""

    def get_media_from_message(self, message: dict) -> dict | None:
        """Extract media info from a Telegram message update."""
```

**Files created:**
- `telegram/services/media_handler.py`

---

#### Step 25: Comprehensive test suite — [#7](../../issues/7)

**Unit tests:**

| Test file | What it tests |
|---|---|
| `test_bot_client.py` | HTTP client methods, error handling, retry, token masking |
| `test_webhook.py` | Secret validation, idempotency (duplicate update_id), event classification |
| `test_message_sender.py` | Send text/photo/document, model persistence, rate limiting |
| `test_contact_upsert.py` | Create new contact, update existing, handle missing fields |
| `test_inbox_integration.py` | Full flow: webhook → task → inbox message created |
| `test_keyboard_builder.py` | Keyboard building, callback_data parsing, length validation |
| `test_broadcast_integration.py` | Broadcast send via Telegram, status tracking |

**Integration tests:**
```python
class TestTelegramWebhookFlow(TransactionTestCase):
    """End-to-end: POST webhook → event persisted → task runs → inbox message exists."""

    def test_text_message_creates_inbox_entry(self):
        ...

    def test_callback_query_routes_to_chatflow(self):
        ...

    def test_duplicate_update_id_is_ignored(self):
        ...
```

**Regression tests:**
```python
class TestWhatsAppUnchanged(TestCase):
    """Verify WA behavior is unaffected by Telegram integration."""

    def test_wa_broadcast_still_works(self):
        ...

    def test_wa_inbox_messages_unchanged(self):
        ...
```

**Files created:**
- `telegram/tests/test_bot_client.py`
- `telegram/tests/test_webhook.py`
- `telegram/tests/test_message_sender.py`
- `telegram/tests/test_contact_upsert.py`
- `telegram/tests/test_inbox_integration.py`
- `telegram/tests/test_keyboard_builder.py`
- `telegram/tests/test_broadcast_integration.py`

---

#### Step 26: Update README roadmap — [#7](../../issues/7)

**Modify** `README.md`:

Change line 66:
```markdown
| **Telegram Bot API** | ✅ Native Bot API | Bot commands, inline keyboards, file handling |
```

Change line 140:
```markdown
| **Telegram Bot API** | Native Bot adapter, bot commands, inline keyboards, file handling | Telegram Mini Apps, payments | — |
```

**Files modified:**
- `README.md`

---

## 5. Security Checklist

| # | Item | Implementation |
|---|---|---|
| 1 | **Bot token encryption at rest** | Use `django-encrypted-model-fields` Fernet field or custom `EncryptedCharField` |
| 2 | **Token never logged** | Mask in all `logger.*` calls; bot client `__repr__` hides token |
| 3 | **Webhook secret validation** | Compare `X-Telegram-Bot-Api-Secret-Token` header using `hmac.compare_digest()` |
| 4 | **Tenant isolation** | Every DB query filtered by `tenant=` or `bot_app__tenant=` |
| 5 | **Anti-replay** | `unique_together = ("bot_app", "update_id")` rejects duplicates |
| 6 | **Callback data validation** | Strict regex parse, max 64 bytes, reject malformed |
| 7 | **Rate limiting** | Redis-backed per-bot throttle respecting `TELEGRAM_RATE_LIMIT` |
| 8 | **Input sanitization** | JSON schema validation on webhook payloads before processing |
| 9 | **CSRF exempt** | Webhook view uses `@csrf_exempt` (public endpoint, validated by secret token) |
| 10 | **No sensitive data in responses** | Webhook always returns bare `{"ok": true}` — no internal state |

---

## 6. Testing Plan

### Test Matrix

| Layer | Tool | Scope |
|---|---|---|
| Unit | `pytest` + `unittest.mock` | Bot client, sender, parsers, validators |
| Integration | `pytest` + Django `TransactionTestCase` | Webhook → task → DB → inbox |
| Regression | `pytest` | WhatsApp flows unaffected |
| Load | `locust` or `k6` | Burst 1000 webhook POSTs/sec, measure queue throughput |

### Run commands
```bash
# All Telegram tests
pytest telegram/tests/ -v

# Only unit tests
pytest telegram/tests/ -v -k "not integration"

# Full suite including regression
pytest telegram/ broadcast/tests/ team_inbox/tests/ -v
```

---

## 7. Configuration & Environment Variables

Add to `.env`:

```env
# Bot token encryption (new — required for production)
TELEGRAM_BOT_TOKEN_ENCRYPTION_KEY=   # 32-byte Fernet key

# Optional tuning (defaults already in settings.py)
TELEGRAM_MAX_RETRIES=3
TELEGRAM_REQUEST_TIMEOUT=30
```

> **Note:** `DEFAULT_WEBHOOK_BASE_URL` (already set in settings for WhatsApp/Razorpay webhooks) is reused for Telegram — no new base URL env var needed. Webhook URL auto-generated as `{DEFAULT_WEBHOOK_BASE_URL}/telegram/v1/webhooks/{bot_app_id}/`.

Add to `jina_connect/settings.py`:
```python
# Telegram settings
TELEGRAM_BOT_TOKEN_ENCRYPTION_KEY = config("TELEGRAM_BOT_TOKEN_ENCRYPTION_KEY", "")
TELEGRAM_MAX_RETRIES = config("TELEGRAM_MAX_RETRIES", 3, cast=int)
TELEGRAM_REQUEST_TIMEOUT = config("TELEGRAM_REQUEST_TIMEOUT", 30, cast=int)
```

---

## 8. Migration & Rollout Checklist

### Pre-deployment
- [ ] All migrations tested on staging DB
- [ ] `telegram_chat_id` field is nullable — no data migration needed
- [ ] Bot token encryption key generated and stored in secrets manager
- [ ] `DEFAULT_WEBHOOK_BASE_URL` configured with valid HTTPS domain (reused — no new setting needed)

### Deployment
- [ ] Run `python manage.py migrate`
- [ ] Verify `telegram_*` tables created
- [ ] Verify `contacts_tenantcontact.telegram_chat_id` column added

### Post-deployment
- [ ] Create first `TelegramBotApp` via admin or API
- [ ] Run `python manage.py register_telegram_webhook --bot-app-id <id>`
- [ ] Verify webhook registered: send `/start` to bot, check admin for event
- [ ] Send test broadcast with platform=TELEGRAM
- [ ] Verify team inbox shows Telegram messages
- [ ] Monitor Celery worker logs for event processing

### Rollback plan
- Disable `TelegramBotApp.is_active` — stops outbound
- Delete webhook via `python manage.py register_telegram_webhook --bot-app-id <id> --delete`
- Telegram fields on contacts are nullable — safe to keep

---

## 9. Out of Scope (Later)

These are explicitly deferred to future phases per the roadmap:

| Item | Why deferred |
|---|---|
| Telegram Mini Apps | Requires frontend web app development |
| Telegram Payments | Depends on payment provider integration with Telegram Commerce |
| Telegram Groups/Channels | MVP focuses on 1:1 bot conversations |
| Inline query handling | Low priority — most bots don't use inline mode |
| Telegram Login Widget | Separate auth flow, not messaging |
| Telethon / MTProto | Bot API is sufficient; MTProto adds unnecessary complexity |

---

## 10. Definition of Done

### [#75](../../issues/75) — Phase 0
- [ ] `render_placeholders()` is the single implementation; no duplicate regex in `broadcast/tasks.py`
- [ ] `PlatformChoices` imported from `jina_connect.platform_choices` in all apps
- [ ] `create_inbox_message()` shared helper exists and used by broadcast tasks + Telegram tasks
- [ ] `route_to_platform_handler()` uses dict dispatch (no if/elif)
- [ ] `BaseChannelAdapter` ABC exists; `BaseBSPAdapter` extends it; WhatsApp registered in channel registry

### [#7](../../issues/7) — Core Telegram adapter
- [ ] **Inbound:** Telegram messages arrive in team inbox with correct tenant, contact, platform
- [ ] **Media:** Inbound photo/doc/audio downloaded via `get_file` and stored
- [ ] **Outbound:** `TelegramMessageSender` delivers text, photo, document, video, audio, voice, location
- [ ] **Broadcast:** `handle_telegram_message()` sends real messages; skips template resolution (no approval on Telegram)
- [ ] **Inline keyboards:** Callback queries parsed and routed to chat flow
- [ ] **Bot commands:** `/start`, `/help`, `/stop` handled; `setMyCommands` called on webhook registration
- [ ] **Session:** `telegram_last_active_at` updated on inbound; 403 → `telegram_is_blocked = True`; `expires_at = None`
- [ ] **Admin:** Bot apps manageable via Django admin with register/test/deactivate actions
- [ ] **Tests:** Full Telegram unit + integration suite passes; WhatsApp regression tests pass

### [#16](../../issues/16) — Unified inbox
- [ ] Inbox API `?platform=TELEGRAM` filter works; `platform` field in serializer response
- [ ] WebSocket group `inbox_{tenant_id}` receives Telegram message push events
- [ ] Agent reply auto-routes via contact's last-used channel with optional `channel` override

### [#76](../../issues/76) — Chat flow routing
- [ ] `GraphExecutor` sends via channel registry (platform-agnostic; no hard-coded WA)
- [ ] Telegram text messages and callback queries route to active chat flows
- [ ] WhatsApp chat flows completely unaffected

### [#15](../../issues/15) — MCP multi-channel
- [ ] `send_message(channel="TELEGRAM")` works via channel registry
- [ ] `create_broadcast(channel="TELEGRAM")` works; template resolution skipped for Telegram
- [ ] WhatsApp MCP calls backward compatible (default `channel="WHATSAPP"`)

### Security (all issues)
- [ ] Bot tokens encrypted at rest; never appear in logs
- [ ] Webhook `X-Telegram-Bot-Api-Secret-Token` validated via `hmac.compare_digest()`
- [ ] Tenant isolation enforced on every query
- [ ] README roadmap updated; `DEFAULT_WEBHOOK_BASE_URL` documented as reused setting

---

## File Change Summary

### New files — Phase 0 ([#75](../../issues/75))
| File | Purpose | Status |
|---|---|---|
| `broadcast/utils/placeholder_renderer.py` | Shared `render_placeholders()` | ✅ Created |
| `jina_connect/platform_choices.py` | Canonical `PlatformChoices` enum | ✅ Created |
| `jina_connect/channel_registry.py` | `get_channel_adapter()` factory | ✅ Created |
| `wa/adapters/channel_base.py` | `BaseChannelAdapter` ABC | ⬜ |
| `team_inbox/utils/__init__.py` | Utils package init | ⬜ |
| `team_inbox/utils/inbox_message_factory.py` | `create_inbox_message()` helper | ⬜ |

### Modified files — Phase 0 ([#75](../../issues/75))
| File | Change | Status |
|---|---|---|
| `broadcast/tasks.py` | Remove 3× duplicate placeholder logic; dict dispatch; use shared factory | ⬜ |
| `wa/adapters/base.py` | `BaseBSPAdapter` inherits `BaseChannelAdapter` | ⬜ |
| `wa/apps.py` | Register `WHATSAPP` in channel registry at `ready()` | ⬜ |
| `tenants/models.py` | Import `PlatformChoices` from canonical source | ⬜ |
| `broadcast/models.py` | Import platform choices from canonical source | ⬜ |
| `team_inbox/models.py` | Import platform choices from canonical source | ⬜ |
| `contacts/models.py` | Import platform choices from canonical source | ⬜ |

### New files — Telegram app ([#7](../../issues/7))
| File | Purpose |
|---|---|
| `telegram/__init__.py` | App init |
| `telegram/apps.py` | AppConfig |
| `telegram/models.py` | TelegramBotApp, TelegramWebhookEvent, TelegramOutboundMessage |
| `telegram/admin.py` | Admin registration |
| `telegram/constants.py` | Error maps, event types, callback format |
| `telegram/serializers.py` | DRF serializers |
| `telegram/signals.py` | post_save event queuing |
| `telegram/tasks.py` | Celery event processor |
| `telegram/urls.py` | URL routing |
| `telegram/views.py` | Webhook receiver |
| `telegram/viewsets/__init__.py` | ViewSet init |
| `telegram/viewsets/bot_app.py` | Bot CRUD viewset |
| `telegram/viewsets/webhook_event.py` | Event audit viewset |
| `telegram/services/__init__.py` | Services init |
| `telegram/services/bot_client.py` | Telegram Bot API HTTP client |
| `telegram/services/message_sender.py` | High-level message sender |
| `telegram/services/media_handler.py` | Media upload/download |
| `telegram/services/keyboard_builder.py` | Inline keyboard helpers |
| `telegram/services/rate_limiter.py` | Redis rate limiter |
| `telegram/management/__init__.py` | Management init |
| `telegram/management/commands/__init__.py` | Commands init |
| `telegram/management/commands/register_telegram_webhook.py` | Webhook registration command |
| `telegram/tests/test_bot_client.py` | Bot client tests |
| `telegram/tests/test_webhook.py` | Webhook tests |
| `telegram/tests/test_message_sender.py` | Sender tests |
| `telegram/tests/test_contact_upsert.py` | Contact tests |
| `telegram/tests/test_inbox_integration.py` | Inbox integration tests |

### Modified files (7)
| File | Change |
|---|---|
| `jina_connect/settings.py` | Add `"telegram"` to INSTALLED_APPS + Telegram settings |
| `jina_connect/urls.py` | Add `telegram/` URL include |
| `contacts/models.py` | Add `telegram_chat_id`, `telegram_username` fields |
| `broadcast/tasks.py` | Replace `handle_telegram_message()` stub |
| `broadcast/models.py` | Update `_get_telegram_message_price()` |
| `mcp_server/tools/messaging.py` | Add `channel` parameter |
| `mcp_server/tools/campaigns.py` | Add `channel` parameter |
| `mcp_server/server.py` | Update instructions text |
| `README.md` | Update roadmap status |
