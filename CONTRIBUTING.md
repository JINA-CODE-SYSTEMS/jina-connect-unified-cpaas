# Contributing to Jina Connect

Thanks for your interest in contributing. This document covers everything you need to get started.

## Prerequisites

- Python 3.11+
- PostgreSQL 15
- Redis 7
- Docker and Docker Compose (optional, but recommended)

## Local Setup

```bash
# Clone and enter the repo
git clone https://github.com/JINA-CODE-SYSTEMS/jina-connect-unified-cpaas.git
cd jina-connect-unified-cpaas

# Create a virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env
# Edit .env with your local database credentials

# Run migrations
python manage.py migrate

# Seed demo data (optional — gives you a tenant, contacts, and templates to work with)
python manage.py seed_demo

# Start the dev server
python manage.py runserver
```

Or with Docker:

```bash
docker-compose up
```

## Running Tests

```bash
# Full test suite
pytest

# Specific app
pytest wa/tests/
pytest tenants/tests/

# With coverage
pytest --cov=wa --cov=broadcast --cov=tenants --cov-report=term-missing
```

All PRs must pass the existing test suite. New features and bug fixes should include tests.

## Code Standards

We use **ruff** for linting and formatting, and **bandit** for security scanning. CI runs all three automatically.

```bash
# Lint
ruff check .

# Format
ruff format .

# Security scan
bandit -r wa/ broadcast/ chat_flow/ contacts/ tenants/ users/ team_inbox/ transaction/ notifications/ -c pyproject.toml
```

Additional rules:

- No `print()` in production code — use `logging`
- No hardcoded secrets or credentials
- No `AllowAny` on new endpoints without explicit approval

## Making a Pull Request

1. **Fork** the repo and create a branch from `main`
2. **Keep PRs small and focused** — one feature or fix per PR
3. **Write tests** for any new functionality or bug fix
4. **Run the test suite** locally before pushing
5. **Use conventional commits:**
   - `feat: add Twilio SMS adapter`
   - `fix: broadcast batch size off-by-one`
   - `docs: update MCP server setup guide`
   - `test: add carousel template edge cases`
   - `refactor: extract adapter registry`

## What We Accept

- Bug fixes with regression tests
- Test coverage improvements
- New BSP adapters (WhatsApp providers, future channel adapters)
- New MCP server tools
- Documentation improvements
- Performance improvements with benchmarks

## What Needs a Discussion First

Open a [discussion](../../discussions) or issue before starting work on:

- New Django apps
- Architectural changes (new middleware, changed model inheritance)
- New channel adapters (Telegram, SMS, etc.)
- Breaking changes to models or APIs
- Changes to the permission/RBAC system

## What We Won't Merge

- Features that require closed-source dependencies
- Vendor-specific logic that doesn't fit the adapter pattern
- Changes that break tenant isolation
- Code without tests for testable functionality

## Adding a voice provider

### New SIP trunk vendor

A new SIP carrier is a YAML profile, no Python required. Drop a file
in [`voice/sip_config/profiles/`](voice/sip_config/profiles/) named
after the carrier (e.g. `acme_telecom.yaml`). The minimum set of
templates the loader expects:

```yaml
name: acme_telecom
display_name: "Acme Telecom"
notes: |
  Carrier-specific gotchas. Codec preferences, registration vs
  IP-auth, anything a future operator should know before signing up.

endpoint_template: |
  [{endpoint_id}]
  type=endpoint
  context=jina-voice-inbound
  disallow=all
  allow=alaw
  allow=ulaw
  auth={auth_id}
  aors={aor_id}
  from_user={sip_username}
  from_domain={sip_realm}

auth_template: |
  [{auth_id}]
  type=auth
  auth_type=userpass
  username={sip_username}
  password={sip_password}
  realm={sip_realm}

aor_template: |
  [{aor_id}]
  type=aor
  contact=sip:{sip_proxy}
  qualify_frequency=60

registration_template: |
  [{reg_id}]
  type=registration
  outbound_auth={auth_id}
  server_uri=sip:{sip_proxy}
  client_uri=sip:{sip_username}@{sip_realm}
  retry_interval=60
```

Add a row to
[`docs/src/content/docs/channels/voice-vendor-configs.mdx`](docs/src/content/docs/channels/voice-vendor-configs.mdx)
under "Vendor profiles" so users can find your carrier. A regression
test in `voice/tests/test_sip.py` should load the profile via
`load_profile("acme_telecom")` and assert the rendered output
contains `type=endpoint`. That's enough — the rest of the SIP path
already has coverage.

### New HTTP voice provider

A new HTTP voice provider is a ~50–80 line subclass of
`HttpVoiceAdapter` plus a matching dialect module.

1. **Credential schema** — add a Pydantic model in
   `voice/adapters/credentials.py` (e.g. `SinchCredentials`) and an
   entry in `validate_credentials` for the new provider key.
2. **Adapter** — `voice/adapters/http_voice/<name>.py` subclassing
   `HttpVoiceAdapter`. Implement:
   - `initiate_call(...)` — POST to the provider's call API, return
     a `ProviderCallHandle`.
   - `verify_webhook(request)` — provider-specific signature check.
   - `parse_webhook(request)` — normalise into `NormalizedCallEvent`.
   - `_normalize_status` / `_normalize_hangup_cause` — map to the
     canonical enums.
   - `hangup(provider_call_id)` if the API supports it.
   - `capabilities = Capabilities(...)` with accurate flags.
   - Call `register_voice_adapter("<name>", YourAdapter)` at the
     module bottom.
3. **Dialect** — `voice/ivr/dialects/<name>.py` exposing the verb
   emitters (`play`, `gather_dtmf`, `gather_speech`, `record`,
   `transfer`, `hangup`) and an `assemble(...)` that produces the
   provider's wire format (XML / JSON / Call Control commands).
4. **Webhooks** — `voice/webhooks/<name>.py` subclassing
   `BaseWebhookHandler`, one handler per webhook URL the provider
   posts to. Wire them into `voice/urls.py`.
5. **Tests** — at minimum:
   - `webhook signature verify` (HMAC / JWT / Ed25519 — whatever
     the provider uses).
   - `dialect generation` — feed a sample tree, assert serialized
     output matches.
   - `parse_webhook normalization` — provider-shaped payload in,
     canonical `NormalizedCallEvent` out.
   - One end-to-end webhook test via `RequestFactory + as_view()` so
     the URL → handler → adapter wiring is exercised.
6. **Docs** — add a tab to the "HTTP voice providers" section of
   `docs/src/content/docs/channels/voice-vendor-configs.mdx` with
   the credential payload + auth gotchas.
7. **Hangup-cause map** — append a row to the troubleshooting table
   in `docs/src/content/docs/channels/voice.mdx` so support staff can
   read provider codes against the canonical enum.

The five shipped HTTP adapters
([twilio.py](voice/adapters/http_voice/twilio.py),
[plivo.py](voice/adapters/http_voice/plivo.py),
[vonage.py](voice/adapters/http_voice/vonage.py),
[telnyx.py](voice/adapters/http_voice/telnyx.py),
[exotel.py](voice/adapters/http_voice/exotel.py)) are the reference
implementations — read the closest one to your provider's auth shape
before starting.

## Issue Labels

- `good-first-issue` — small, well-scoped tasks for new contributors
- `help-wanted` — we'd welcome community help on these
- `bug` — confirmed bugs
- `enhancement` — feature requests
- `adapter` — new provider/channel adapter work

## Response Time

We commit to a **first response within 48 hours** on issues and PRs. "First response" means acknowledged — not necessarily resolved. If we're going to decline a PR, we'll explain why.

## Questions?

Open a [discussion](../../discussions) — we're happy to help.
