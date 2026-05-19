# `chat_flow.triggers` — event-trigger subsystem (#188)

Lets a `ChatFlow` declare *when* it auto-spawns instead of relying on
manual contact-assignment (`contact.assigned_to_type=CHATFLOW`). The
substrate is channel-agnostic — CTWA, inbound voice, SMS keywords,
RCS suggestion-taps, and Telegram commands all hook the same registry.

## Architecture in 30 seconds

```
[channel webhook]  ─parse─▶  emit(TriggerEvent)  ─▶  dispatch(event)
                                                         │
                                          ┌──────────────┴────────────┐
                                          │ For each active flow:     │
                                          │   For each trigger entry: │
                                          │     trigger.matches(...)? │
                                          │       └─▶ queue session   │
                                          └───────────────────────────┘
```

* `TriggerEvent` — frozen dataclass; the normalised cross-channel payload.
* `BaseTrigger` — ABC every trigger type subclasses.
* `@register_trigger("name")` — module-level decorator. Mirrors
  `voice.adapters.registry.register_voice_adapter` style.
* `dispatch(event)` — finds matching flows, queues
  `start_chatflow_session_task` via Celery, returns the number of
  spawns. Idempotent across webhook replays (Redis SETNX, 24h TTL).

## ChatFlow.triggers config shape

```jsonc
[
  {
    "type": "inbound_keyword_match",
    "config": {
      "keywords": ["sales", "pricing"],
      "channel": "wa",        // optional; "any" or null = all channels
      "case_sensitive": false // default false
    }
  },
  {
    "type": "ctwa_referral_received",
    "config": {
      "campaign_ids": "any"   // or ["<uuid>", "<uuid>", ...]
    }
  }
]
```

Validated at save time via `chat_flow.triggers.validators`. Unknown
trigger types, malformed config, and within-flow duplicates all raise
`ValidationError` on `ChatFlow.save()`.

## Adding a new trigger type

1. Create `chat_flow/triggers/types/<your_name>.py`.
2. Declare a Pydantic config model. This drives save-time validation
   and the frontend introspection endpoint.
3. Subclass `BaseTrigger`, implement `matches(event, config) -> bool`.
   * Pure function. No DB writes. Never raise.
   * Return `False` on partial / missing data; never raise.
4. Decorate the class with `@register_trigger("<your_name>")`.
5. Import the module from `chat_flow/triggers/types/__init__.py`.
6. Add tests under `chat_flow/triggers/tests/`.
7. Document any `event.extra` fields the trigger reads (below).

Skeleton:

```python
from pydantic import BaseModel
from chat_flow.triggers.base import BaseTrigger, TriggerEvent
from chat_flow.triggers.registry import register_trigger


class MyConfig(BaseModel):
    threshold: int = 1


@register_trigger("my_trigger")
class MyTrigger(BaseTrigger):
    config_model = MyConfig

    def matches(self, event: TriggerEvent, config: dict) -> bool:
        # Read event.extra fields you need; return True/False.
        return bool(event.body_text and len(event.body_text) >= config.get("threshold", 1))
```

## Adding a new channel emission point

Drop these four lines into the channel's inbound webhook processor,
right after the inbound row is persisted:

```python
from chat_flow.triggers import TriggerEvent, emit

emit(TriggerEvent(
    tenant_id=tenant.id,
    channel="<your_channel>",       # one of: wa | sms | voice | rcs | telegram
    contact_id=contact.id,
    inbound_row_id=str(message.pk),
    inbound_row_model="<app>.<Model>",
    body_text=text_or_none,
    received_at=timezone.now().isoformat(),
    extra={...},                    # channel-specific fields; see below
))
```

Wrap the call in `try/except` and log on failure — triggers are an
additive routing layer; a subsystem hiccup must never break inbound
message ingestion.

Currently wired:

| Channel | Site | Inbound row |
|---|---|---|
| `wa` | [`wa/tasks.py`](../../wa/tasks.py) — after `_handle_chatflow_routing()` | `team_inbox.Messages` |
| `voice` | [`voice/tasks.py:process_call_status`](../../voice/tasks.py) — on INITIATED / RINGING for INBOUND calls | `voice.VoiceCall` |

Pending (each gets its own follow-on issue):

| Channel | Why deferred |
|---|---|
| `sms` | Inbound persistence is spread across webhook handlers; needs its own consolidation pass. |
| `rcs` | Same. |
| `telegram` | Same; multiple inbound paths (text, edited_message, reaction) need uniform emit. |

## `event.extra` contract by channel

Triggers MUST tolerate missing keys (the contract is "best-effort enrichment").

### WhatsApp (`channel="wa"`)

| Key | Type | Notes |
|---|---|---|
| `wa_webhook_event_id` | str | Source webhook row PK; for audit. |
| `external_message_id` | str | Provider-assigned message id. |
| `referral_source_type` | str | `"ad"` / `"post"` — present when CTWA referral is in the payload. Populated by #192. |
| `referral_source_id` | str | Meta ad ID. |
| `referral_source_url` | str | |
| `referral_headline` | str | |
| `referral_body` | str | |
| `referral_media_type` | str | `"image"` / `"video"` / `""`. |
| `referral_media_url` | str | |
| `referral_ctwa_clid` | str \| null | Critical for CAPI match quality; not all BSPs expose it. |
| `campaign_id` | uuid \| null | CTWA campaign resolved from `referral_source_id`. Populated by #194 ingestion. Null = orphan ad. |

### Voice (`channel="voice"`)

| Key | Type | Notes |
|---|---|---|
| `provider_call_id` | str | Twilio CallSid / Plivo CallUUID / SIP Call-ID. |
| `from_number` | str | E.164. |
| `to_number` | str | E.164. |
| `event_type` | str | `"initiated"` or `"ringing"` (only these fire emit). |

## Idempotency guarantee

`dispatch(event)` claims `chatflow:trigger:dispatch:<tenant>:<channel>:<inbound_row_id>`
via Redis `SET NX EX=86400` before iterating flows. Webhook replays
within 24h are silent no-ops. Outside that window — extremely rare —
a duplicate session is preferable to a silent drop.

Redis-down behaviour is **fail open** (proceed with dispatch). The
alternative is silent ingestion drops on infrastructure hiccups,
which is the strictly worse failure mode.

## Anti-patterns

* **No DB writes in `matches()`.** It runs once per flow per event;
  any side effect ships duplicates as soon as a tenant has two
  matching flows.
* **Never raise from `matches()`.** The dispatcher catches and logs,
  but exceptions add noise and hide trigger-type bugs.
* **Don't mutate `event` or `config`.** Both are shared across
  per-flow iterations within a single dispatch.
* **Don't read tenant data inside `matches()`.** Anything that needs
  tenant context should go on `event.extra` at emission time.

## Introspection endpoint

Frontend uses `GET /chat_flow/api/v1/triggers/types/` to render the
trigger-config form in the flow builder. Response is a list of
`{type_name, config_schema}` where `config_schema` is the
Pydantic-generated JSON Schema for that trigger's config model.

Backwards-compatibility contract: never change a `type_name` after a
trigger ships. Removing a `type_name` requires a deprecation path
(stop registering, but tolerate the type in dispatcher; flow validator
warns instead of failing).
