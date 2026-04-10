# Jina Connect — Unified CPaaS

> The open-source, multi-provider WhatsApp platform.
> Self-hostable. Agency-safe. MCP-native.

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg)](https://python.org)
[![Django 5.1](https://img.shields.io/badge/Django-5.1-092E20.svg)](https://djangoproject.com)

---

## What is Jina Connect?

Jina Connect is a **unified CPaaS (Communication Platform as a Service)** that lets you send WhatsApp messages, manage broadcast campaigns, and build visual chatflows — all through a single self-hosted platform.

Unlike closed alternatives (WATI, Gupshup, AiSensy) that lock you into a single WhatsApp Business Solution Provider, Jina Connect supports **multiple BSPs through a pluggable adapter pattern**. Switch providers with zero code changes. No vendor lock-in.

### Why Jina Connect?

- **Multi-Provider** — Connect Meta Direct, Gupshup, WATI, or any BSP through a single adapter interface
- **Self-Hostable** — Run on your own infrastructure. Your data stays with you
- **MCP-Native** — The only MCP server that routes WhatsApp messages across multiple providers. Drop into Claude, Cursor, or VS Code Copilot
- **Agency-Ready** — White-label, sub-tenants, markup pricing — built for agencies managing multiple clients
- **Full-Stack** — WhatsApp + Broadcasts + ChatFlows + Team Inbox in one platform

---

## Current Status

> **This is the initial open-source release.** The core Django application is fully functional — all modules, models, APIs, and business logic are included. What's missing is the deployment layer (Docker, docs for local setup). See [What's Pending](#whats-pending) below.

### What's Included

| Module | Status | Description |
|--------|--------|-------------|
| **WhatsApp Engine** (`wa/`) | ✅ Complete | Multi-BSP messaging — templates, media, interactive, carousel, OTP, commerce. Webhook ingestion with signature verification. Adapters for Meta Direct, Gupshup, WATI |
| **Broadcast Engine** (`broadcast/`) | ✅ Complete | Campaign lifecycle, batch processing, per-recipient tracking, URL click analytics, cost estimation |
| **ChatFlow Builder** (`chat_flow/`) | ✅ Complete | Visual drag-and-drop conversation designer. Node types: template, delay, branch, handoff. Stateful graph execution via LangGraph |
| **Team Inbox** (`team_inbox/`) | ✅ Complete | Unified inbox with real-time WebSocket streaming, conversation assignment, bulk actions |
| **Contacts** (`contacts/`) | ✅ Complete | Multi-source contacts, lead scoring, assignment engine, tagging, CSV import/export |
| **Multi-Tenancy & RBAC** (`tenants/`, `users/`) | ✅ Complete | Workspace isolation, custom roles (OWNER/ADMIN/MANAGER/AGENT/VIEWER), granular per-endpoint permissions, API key auth |
| **Billing** (`transaction/`, `razorpay/`) | ✅ Complete | Wallet system, transaction ledger, Razorpay payment gateway integration |
| **Message Templates** (`message_templates/`) | ✅ Complete | Template CRUD, approval workflow, BSP-specific validation and submission |
| **Notifications** (`notifications/`) | ✅ Complete | In-app alerts via WebSocket — template approvals, broadcast status, low balance |
| **CI Pipeline** (`.github/workflows/ci.yml`) | ✅ Complete | Lint (ruff), security scan (bandit), tests (pytest), Django system checks, secret detection (TruffleHog) |

### What's Pending

| Item | Phase | Notes |
|------|-------|-------|
| Docker Compose setup | Phase 1 | One-command local development (`docker compose up`) |
| Dockerfile | Phase 1 | Production-ready image with health checks |
| Local setup documentation | Phase 1 | Step-by-step guide without Docker |
| Seed data / demo fixtures | Phase 1 | Bootstrap a demo tenant with sample data |
| Environment variable reference | Phase 1 | Document every config option in `.env.example` |
| OpenAPI 3.1 spec export | Phase 2 | Auto-generated, versioned API docs |
| Python & Node.js SDKs | Phase 2 | `pip install jina-connect` / `npm install @jina-connect/sdk` |
| MCP Server | Phase 3 | WhatsApp tools for Claude, Cursor, Copilot |
| White-label & agency features | Phase 4 | Sub-tenants, markup pricing, agency dashboard |

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                       Jina Connect                             │
│                                                                │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌────────────────┐  │
│  │ REST API │ │WebSocket │ │MCP Server │ │Webhook Ingress │  │
│  │  (DRF)   │ │(Channels)│ │  (MIT)    │ │(Meta/Gupshup)  │  │
│  └────┬─────┘ └────┬─────┘ └─────┬─────┘ └──────┬─────────┘  │
│       └─────────────┴─────────────┴──────────────┘            │
│                           │                                    │
│               ┌───────────▼────────────┐                      │
│               │   Django Application   │                      │
│               │                        │                      │
│               │  wa/ · broadcast/      │                      │
│               │  chat_flow/            │                      │
│               │  team_inbox/ · contacts│                      │
│               └───────────┬────────────┘                      │
│                           │                                    │
│               ┌───────────▼────────────┐                      │
│               │   BSP Adapter Layer    │                      │
│               │                        │                      │
│               │ Meta · Gupshup · WATI  │                      │
│               │      · Custom...       │                      │
│               └────────────────────────┘                      │
│                                                                │
│  PostgreSQL · Redis · Celery Workers                          │
└────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
jina-connect-unified-cpaas/
├── wa/                  # WhatsApp engine — BSP adapters, webhook processing, message routing
├── broadcast/           # Campaign management — scheduling, batching, delivery tracking
├── chat_flow/           # Visual chatflow builder — graph execution, node types
├── team_inbox/          # Real-time inbox — WebSocket consumers, conversation management
├── contacts/            # Contact management — import/export, lead scoring, tagging
├── message_templates/   # Template CRUD — approval workflow, BSP validation
├── tenants/             # Multi-tenancy — workspace isolation, RBAC, permissions
├── users/               # Authentication — JWT, API keys, deep linking
├── transaction/         # Wallet & ledger — balance tracking, transaction history
├── razorpay/            # Payment gateway — Razorpay integration
├── notifications/       # In-app notifications — WebSocket alerts
├── abstract/            # Shared base classes — models, serializers, viewsets
├── jina_connect/        # Django project config — settings, urls, celery, ASGI
├── docs/                # Internal documentation — PRDs, user guides, BSP specs
├── .github/workflows/   # CI pipeline
├── .env.example         # Environment variable template
├── requirements.txt     # Python dependencies
└── pyproject.toml       # Tool configuration (pytest, ruff, bandit)
```

---

## Roadmap

Development is organized into 5 phases:

### Phase 1 — Open-Source Foundation

**Goal:** Clone → `docker compose up` → working Jina Connect instance in under 5 minutes.

- One-command local setup with Docker Compose (PostgreSQL, Redis, Celery, Daphne, Nginx)
- Production-ready Docker image with health checks and restart policies
- Seed data to bootstrap a demo tenant with contacts, templates, and a sample chatflow
- Full environment variable reference so every config option is documented

### Phase 2 — Developer Experience

**Goal:** Integrate Jina Connect into any app using clear APIs, SDKs, and examples.

- **OpenAPI 3.1 spec** — auto-generated, versioned API docs at `/api/v1/`
- **Python SDK** — `pip install jina-connect` with async support
- **Node.js SDK** — `npm install @jina-connect/sdk` with TypeScript types
- **Per-tenant rate limiting** — configurable per-endpoint throttling
- **Cursor-based pagination** — consistent across all list endpoints
- **Standardized error responses** — structured error schema with codes and details
- **Webhook delivery retry** — exponential backoff for outbound webhooks
- **Example projects:**
  - Send a WhatsApp template message (Python)
  - Receive and process webhooks (Flask/FastAPI)
  - Schedule and monitor a broadcast campaign
  - Build an FAQ chatbot using the ChatFlow API
  - Send the same message through Meta Direct and Gupshup, compare delivery

### Phase 3 — MCP Server & AI-Native

**Goal:** The only MCP server that routes WhatsApp messages across multiple providers.

- **MCP Messaging Tools** — `send_template`, `send_message`, `get_message_status`, `list_templates` through any configured BSP
- **MCP Contact Tools** — `search_contacts`, `create_contact`, `update_contact`
- **MCP Campaign Tools** — `create_broadcast`, `schedule_broadcast`, `get_broadcast_status`
- **MCP Provider Tools** — `list_providers`, `get_provider_health`, `switch_provider`
- **Multi-provider routing** — same tool call, multiple BSP backends. Route by cost, reliability, or tenant preference
- **Knowledge Base RAG** — upload documents/PDFs, AI agent answers from tenant knowledge base
- **Conversation memory** — multi-turn context with LangGraph checkpointing
- **Multi-LLM router** — auto-fallback between OpenAI → Anthropic → local models
- **Server-Sent Events** — real-time event stream as an alternative to polling
- **Outbound webhooks** — tenant-configurable URLs for all event types with delivery logs and manual retry

### Phase 4 — Agency & Enterprise

**Goal:** Agencies white-label and resell. Enterprises self-host with confidence.

- **Full white-label** — custom domain, branding (logo, favicon, colors), email templates
- **Sub-tenant hierarchy** — Agency → Client relationship with rollup billing
- **Agency dashboard** — cross-client overview of message volume, costs, delivery rates
- **Revenue share / markup** — agencies set per-client pricing above platform cost
- **A/B testing** — split test template variants within a broadcast
- **Smart scheduling** — AI-optimized send times based on historical open rates
- **Drip campaigns** — multi-step automated sequences triggered by events
- **GDPR compliance** — tenant data export and full data purge endpoints
- **SSO** — SAML 2.0 / OpenID Connect for enterprise identity providers
- **Additional BSP adapters** — AiSensy, Interakt, Yellow.ai, Twilio
- **Adapter SDK** — base class + test harness for community-contributed adapters

### Phase 5 — Community & Ecosystem

**Goal:** An active community contributing adapters, integrations, and content.

- **Zapier / Make integration** — no-code automation triggers and actions
- **n8n node** — open-source workflow automation
- **Shopify app** — order notifications, abandoned cart recovery via WhatsApp
- **WooCommerce plugin** — order status updates via WhatsApp
- **HubSpot integration** — contact sync, WhatsApp as a CRM channel
- **Template gallery** — community-contributed chatflow templates (FAQ bot, appointment booking, e-commerce)
- **Adapter registry** — directory of community BSP adapters

---

## Feature Comparison

| Feature | Jina Connect | WATI | Gupshup | AiSensy |
|---------|:------------:|:----:|:-------:|:-------:|
| Open Source | ✅ AGPL-3.0 | ❌ | ❌ | ❌ |
| Self-Hostable | ✅ | ❌ | ❌ | ❌ |
| Multi-BSP | ✅ | ❌ | ❌ | ❌ |
| MCP Server | 🔜 Phase 3 | ❌ | ❌ | ❌ |
| Visual ChatFlow | ✅ | ✅ | ⚠️ | ⚠️ |
| White-Label | 🔜 Phase 4 | ❌ | ❌ | ❌ |
| Vendor Lock-In | None | 🔒 | 🔒 | 🔒 |

---

## Tech Stack

- **Backend:** Django 5.1, Django REST Framework, Django Channels (ASGI)
- **Task Queue:** Celery + Redis
- **Database:** PostgreSQL
- **Real-Time:** WebSocket via Daphne
- **ChatFlow Engine:** LangGraph (stateful graph execution)
- **Payments:** Razorpay (pluggable)

---

## Getting Started

### Prerequisites

- **Python 3.11+**
- **PostgreSQL 15+**
- **Redis 7+**

#### macOS

```bash
brew install python@3.11 postgresql@15 redis
brew services start postgresql@15
brew services start redis
```

#### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv postgresql redis-server libmagic1
sudo systemctl start postgresql redis-server
```

### 1. Clone & Set Up Virtual Environment

```bash
git clone https://github.com/jina-connect/jina-connect-unified-cpaas.git
cd jina-connect-unified-cpaas

python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Create the Database

```bash
# Create the PostgreSQL database (default name: jc6)
createdb jc6

# Or via psql:
# psql -U postgres -c "CREATE DATABASE jc6;"
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` — the defaults work for local development. At minimum, verify:

```env
DB_NAME=jc6
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
SECRET_KEY=change-me-in-production
DEBUG=True
```

### 4. Run Migrations

```bash
python manage.py migrate
```

### 5. Create a Superuser

```bash
python manage.py createsuperuser
```

### 6. Start the Development Server

You need **3 terminal sessions** (or use a process manager like `honcho` / `foreman`):

**Terminal 1 — Django (ASGI via Daphne):**
```bash
# Daphne for WebSocket + HTTP support
daphne -b 0.0.0.0 -p 8000 jina_connect.asgi:application

# Or for simple HTTP-only development:
# python manage.py runserver
```

**Terminal 2 — Celery Worker (async tasks):**
```bash
celery -A jina_connect worker --loglevel=info
```

**Terminal 3 — Celery Beat (scheduled tasks):**
```bash
celery -A jina_connect beat --loglevel=info
```

### 7. Verify It's Working

- **Admin panel:** http://localhost:8000/admin/
- **API docs (Swagger):** http://localhost:8000/swagger/
- **API docs (ReDoc):** http://localhost:8000/redoc/
- **Version endpoint:** http://localhost:8000/version/

### Connecting a WhatsApp BSP

Once the server is running, you need to connect at least one BSP to send/receive messages:

1. **Create a tenant** via the admin panel or API (`POST /tenants/`)
2. **Add a WhatsApp App** to the tenant with your BSP credentials:
   - **Meta Direct** — requires Meta App Secret, permanent token, and phone number ID
   - **Gupshup** — requires Gupshup App ID, API key, and phone number
   - **WATI** — requires WATI API endpoint and auth token
3. **Configure webhooks** — point your BSP's webhook URL to `https://your-domain/wa/webhook/{bsp}/`

> See `.env.example` for all BSP-specific environment variables.

---

## License

The core platform is licensed under [AGPL-3.0](LICENSE). You can self-host, modify, and redistribute — but modified versions deployed as a network service must keep the source open.

The MCP server (Phase 3) will be licensed under **MIT** for maximum ecosystem adoption.

Commercial licensing is available for organizations that cannot comply with AGPL. Contact: *TBD*