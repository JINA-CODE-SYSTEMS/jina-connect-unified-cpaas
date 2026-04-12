# Jina Connect — Unified CPaaS

**Open-source communication infrastructure for WhatsApp, Telegram, SMS, RCS, and Voice. Multi-provider. AI-native. Self-hostable.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-compose-blue.svg)](docker-compose.yml)
[![MCP](https://img.shields.io/badge/MCP-ready-purple.svg)](#mcp-server)

---

## What it is

Jina Connect is a complete, self-hostable CPaaS platform — the communication infrastructure your business, agency, or AI product runs on. One stack, every customer channel: **WhatsApp**, **Telegram**, **SMS**, **RCS**, and **Voice**. Broadcasts, automated flows, templates, contacts, team inbox, billing, and AI-agent access through MCP — all in one self-hostable project.

**WhatsApp ships production-ready today.** Telegram, SMS, RCS, and Voice are on the roadmap, each built on the same unified channel abstraction. Pick a channel, write once, and the same primitives (send, broadcast, flow, inbox, analytics) work everywhere.

It also ships the first and only **multi-provider MCP server for business messaging**, letting AI agents (Claude, ChatGPT, Cursor, custom LLM apps) operate your communication channels natively through 13 tool-callable functions.

**If you're building products, campaigns, or AI agents that talk to customers — and you don't want to be locked into one channel or one vendor — this is your infrastructure.**

---

## Why it exists

Every commercial CPaaS tool on the market is closed SaaS. Switching channels or providers means rewriting integrations. Self-hosting isn't possible. AI agents have to be rebuilt per vendor. Agencies juggle one dashboard per client, per channel, per provider.

We're building the open-source alternative that solves all of this at once:

- **Every customer channel, one stack.** WhatsApp today, Telegram + SMS + RCS + Voice coming. The same primitives across all of them.
- **Multi-provider by design.** For each channel, swap providers without rewriting code. WhatsApp already routes across Meta Cloud API and Gupshup; more channels and providers land the same way.
- **AI-native from day one.** The MCP server lets any AI agent operate your channels the way a human would — create templates, send campaigns, query analytics — without glue code.
- **Self-hostable.** `docker-compose up` and you have a full Jina Connect instance. Keep it on your own infrastructure, forever.
- **AGPL v3.** Fork it, audit it, extend it. Commercial license available for enterprises whose legal teams require it.

---

## Quickstart

```bash
# Clone and start
git clone https://github.com/jina-code-systems/jina-connect-unified-cpaas.git
cd jina-connect-unified-cpaas
docker-compose up

# Visit http://localhost:8000

# Seed demo data (sample tenant, contacts, and templates)
docker-compose exec web python manage.py seed_demo
```

From a fresh clone to your first WhatsApp message sent: **under 5 minutes**. If it takes longer, [open an issue](../../issues) — we'll fix it.

---

## What's in the box

Everything below is open-source, shipped in this repo, and works out of the box.

### Channels

**Unified channel engine (`wa` today, more coming)** — The core abstraction that lets every other app (broadcast, flows, inbox, analytics) work across any communication channel. The same "send" primitive handles WhatsApp today and will handle Telegram, SMS, RCS, and Voice as they ship.

| Channel | Status | Providers supported |
|---|---|---|
| **WhatsApp Business** | ✅ Production-ready | Meta Cloud API, Gupshup |
| **Telegram Bot API** | 🚧 Coming soon | Native Telegram Bot API |
| **SMS** | 🚧 Coming soon | Twilio, MSG91, Fast2SMS |
| **RCS Business Messaging** | 🚧 Coming soon | Meta RCS, Google RCS |
| **Voice (calls, IVR)** | 🚧 Coming soon | SIP trunking, Twilio Voice |

Each channel plugs into the same unified abstraction, so features you use today (campaigns, flows, inbox, analytics) work on new channels the moment the adapter lands.

**Message templates (`message_templates`)** — Template lifecycle across channels. Create, submit for approval (where required by the channel, like WhatsApp), track status, manage variables.

### Outbound engagement

**Broadcast engine (`broadcast`)** — Campaign management for outbound marketing. Draft → schedule → send lifecycle, batch processing with per-channel rate limiting, URL click tracking, and pre-send cost estimation. Works today on WhatsApp; expands to SMS and RCS automatically when those adapters ship.

**Chat flow builder (`chat_flow`)** — Visual flow designer for automated customer journeys. Node-edge graph model, conditional branching rules engine, multi-turn session tracking. Channel-agnostic by design — a flow you build for WhatsApp can route over SMS when that adapter arrives.

### Inbound and support

**Team inbox (`team_inbox`)** — Real-time unified inbox across channels. WebSocket-based message delivery, activity event stream, sequential ordering across multiple agents. One conversation view no matter which channel the customer reached you on.

**Contacts (`contacts`)** — Contact management at scale. Multi-source ingestion (CSV, API, webhook), lead scoring, tagging, segmentation. Channel-neutral: the same contact can be reached via WhatsApp today, SMS tomorrow, or Voice next month, all from the same record.

### Platform

**Multi-tenancy (`tenants`)** — Every feature is tenant-aware. Full RBAC, per-tenant branding, wallet management, API key provisioning. Run multiple isolated client workspaces on one deployment — essential for agencies.

**Users and auth (`users`)** — JWT-based authentication. Login, registration, cross-tenant user management.

**Transaction ledger (`transaction`)** — Wallet operations with full audit trail. Debit/credit tracking, holds for pending operations, multi-currency support. Every billable action across every channel writes to this ledger.

**Payments (`razorpay`)** — Razorpay integration for wallet top-ups (India-first). Other gateways slot in through the payment abstraction.

**Notifications (`notifications`)** — In-app alerts and WebSocket-based real-time updates used across all apps.

### MCP server — the AI-native differentiator

**`mcp_server`** — A FastMCP-based stateless HTTP MCP server exposing **13 tools** for AI agents:

| Tool category | Tools |
|---|---|
| Template lifecycle | validate, create, list, get by name, check status, delete |
| Messaging | send single message, send bulk |
| Contacts & segments | manage contacts, query segments |
| Campaigns | trigger broadcast, check campaign status |
| Analytics | query delivery/read stats |

Today the MCP server operates WhatsApp. As Telegram, SMS, RCS, and Voice adapters ship, the same tools start routing across channels — your AI agent can send a WhatsApp message, fall back to SMS, and escalate to a voice call using the same tool calls.

Works with Claude Desktop, Claude.ai, ChatGPT, Cursor, VS Code Copilot, Windsurf, and any MCP-compatible client. Two transports today: `stdio` and streamable HTTP. SSE transport coming soon. Authentication via tenant API keys — one MCP server can safely serve multiple tenants.

**What makes this unique:** every competing business-messaging MCP server is locked to a single provider and usually a single channel. This one is multi-provider from day one and multi-channel by design.

---

## Who it's for

**Developers and technical founders** building products that need customer messaging across any channel. Stop rewriting template management, webhook handling, and channel-specific quirks. Start with a foundation that works for WhatsApp today and extends to every other channel as those adapters land.

**Marketing agencies** managing customer communication for multiple clients across multiple providers and channels. One dashboard. One bill. Consistent features across channels. Client portability if you ever leave us — the code is yours.

**AI-native builders** putting agents into production. Give Claude, ChatGPT, or your custom LLM direct access to business messaging through the MCP server. Start with WhatsApp, expand to SMS and Voice as those land. No glue code. No provider lock-in.

**D2C and e-commerce teams** who want proper team collaboration, rich automation, and audit trails across every channel their customers actually use — without paying enterprise SaaS prices.

---

## Roadmap

The roadmap below is organized by CPaaS capability. Each capability has a current state and what's coming next. Treat dates as directional — open-source pace depends on community feedback as much as our roadmap.

### 📱 Messaging Channels

| Capability | Now | Next | Later |
|---|---|---|---|
| **WhatsApp Business API** | Meta Cloud API, Gupshup | WATI adapter, Twilio adapter | Sinch, Infobip, MessageBird |
| **Telegram Bot API** | — | Native Telegram adapter with bot commands, inline keyboards, file handling | Telegram Mini Apps, payments |
| **SMS** | — | Twilio, MSG91, Fast2SMS adapters | Region-specific carriers, short-code support |
| **RCS Business Messaging** | — | Meta RCS, Google RCS adapters | Rich cards, suggested replies, verified sender |
| **Voice (calls, IVR)** | — | SIP trunking, basic IVR flows, Twilio Voice adapter | Advanced call routing, recording, transcription, agent handoff |

### 📤 Outbound Campaigns

| Capability | Now | Next | Later |
|---|---|---|---|
| **Broadcast campaigns (WhatsApp)** | Draft → schedule → send, batch processing, URL tracking, cost estimation | Advanced segmentation, A/B testing, drip sequences | Multi-channel orchestration (WhatsApp + SMS + email in one campaign) |
| **Quality score management** | Rate limiting per provider | Automated quality score alerts, auto-throttling | Predictive sending windows based on recipient behavior |
| **Click-to-WhatsApp (CTWA) optimization** | — *(coming soon)* | CTWA routing optimization, Meta ads integration | CTWA attribution, cost-per-conversation analytics |
| **Voice campaigns** | — *(coming soon)* | Bulk outbound calling with IVR menus, call recording | Predictive dialing, agent-assisted campaigns |

### 🤖 Automation and Flows

| Capability | Now | Next | Later |
|---|---|---|---|
| **Visual chat flow builder** | Node-edge designer, conditional branching, multi-turn sessions | Template gallery, flow analytics, webhook triggers | AI-assisted flow generation, natural language flow editing |
| **Cross-channel flows** | WhatsApp only | SMS fallback if WhatsApp undelivered, escalate to voice on failure | Full cross-channel orchestration with channel preferences per contact |
| **Rules engine** | Conditional routing, session-based context | Time-based triggers, scheduled automations | ML-based next-best-action |

### 👥 Customer Conversations

| Capability | Now | Next | Later |
|---|---|---|---|
| **Team inbox** | Real-time WebSocket delivery, multi-agent, activity events, sequential ordering (WhatsApp) | Inbox unified across WhatsApp + SMS + Telegram, internal notes, canned responses | Sentiment analysis, priority routing, SLA tracking |
| **Contact management** | Multi-source ingestion, scoring, tagging, segmentation | CRM integrations (HubSpot, Salesforce), custom fields | Unified customer profile across all channels |

### 🧠 AI Agent Integration (MCP)

| Capability | Now | Next | Later |
|---|---|---|---|
| **MCP server** | 13 tools, stateless HTTP, stdio + streamable HTTP, WhatsApp operations. SSE *(coming soon)* | Multi-channel routing in MCP tools (WhatsApp + SMS + Voice from same tool calls), richer analytics | Agent-to-agent handoff, contextual memory across channels, voice-to-text bridges |
| **AI-native content** | — *(coming soon)* | AI-assisted template writing, compliance checking per channel | Localization agents, tone adaptation, multi-language flows |

### 💰 Billing and Commerce

| Capability | Now | Next | Later |
|---|---|---|---|
| **Wallet and transactions** | Debit/credit ledger, multi-currency, holds | Prepaid/postpaid switching, usage-based billing per channel | Invoice generation, tax handling |
| **Payment gateways** | Razorpay (India) | Stripe (global), PayPal, local gateways per region | Pay-via-WhatsApp transaction flows |
| **Commerce catalog** | — *(coming soon)* | — | WhatsApp Commerce catalog, Instagram Shop integration *(partial availability in hosted Jina Connect today; open-sourcing planned)* |

### 🏢 Platform and Governance

| Capability | Now | Next | Later |
|---|---|---|---|
| **Multi-tenancy and RBAC** | Full tenant isolation, role-based access, per-tenant branding, API keys | Fine-grained permissions, audit logs | SSO (SAML, OIDC), compliance certifications |
| **Self-hosting** | docker-compose with Postgres, Redis, Daphne, Celery | Kubernetes Helm charts, one-click cloud deployments | Managed self-hosting, auto-updates |
| **Commercial licensing** | AGPL v3 + commercial option on request | Formalized commercial license tiers | Partner/reseller programs |

### 🔌 Integrations

| Capability | Now | Next | Later |
|---|---|---|---|
| **E-commerce** | — *(coming soon)* | Shopify, WooCommerce adapters | Magento, BigCommerce, custom webhooks |
| **CRMs** | — *(coming soon)* | HubSpot, Zoho, Salesforce connectors | Pipedrive, Freshworks, custom CRM adapters |
| **Analytics and BI** | Basic delivery/read stats | Grafana dashboards, CSV export, webhook stream | Segment, Mixpanel, Amplitude sinks |

---

## How we compare

| | Jina Connect | WATI | Gupshup | Twilio | AiSensy |
|---|---|---|---|---|---|
| **Open source** | ✅ AGPL v3 | ❌ | ❌ | ❌ | ❌ |
| **Self-hostable** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Multi-channel** | ✅ WhatsApp + roadmap for SMS/RCS/Telegram/Voice | ❌ WhatsApp only | ⚠️ Partial | ✅ Multi-channel | ❌ WhatsApp only |
| **Multi-provider per channel** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **MCP server** | ✅ Multi-provider | ✅ WATI only | ❌ | ✅ Twilio only | ❌ |
| **Team inbox** | ✅ | ✅ | ⚠️ Limited | ⚠️ Limited | ✅ |
| **Visual flow builder** | ✅ | ✅ | ✅ | ❌ | ✅ |
| **Starting price (hosted)** | ₹2,000/mo | ₹2,499/mo | Usage-based | Usage-based | ₹999/mo |
| **Price-independent of vendor** | ✅ | ❌ | ❌ | ❌ | ❌ |

Twilio is the most direct comparison on breadth (they also do multi-channel CPaaS), but Twilio is closed, expensive, and has no open-source story. Jina Connect is the only platform that combines multi-channel, multi-provider, open-source, and AI-native in one package.

---

## Hosted vs. Self-Hosted

You have two ways to run Jina Connect:

**Self-hosted (free forever).** Clone this repo, `docker-compose up`, and you have a complete Jina Connect instance. You own the infrastructure, the data, and the operations. Licensed under AGPL v3 — use it however you want within the license terms. This path is best for technical teams who want full control.

**Hosted (Jina Connect SaaS).** We run Jina Connect for you. Managed infrastructure, SLA, automatic upgrades, priority support, and extensions that aren't yet in the open-source repo (including some Voice AI capabilities currently in private development, and the WhatsApp Commerce catalog). Pricing starts at ₹2,000/month. Best for agencies and D2C brands who want to ship faster without infrastructure work.

Both paths use the same core. Self-hosted users can migrate to hosted (or vice versa) without data loss.

---

## Contributing

This project follows a [benevolent dictator governance model](GOVERNANCE.md) with a named maintainer from the Jina Code Systems LLP dev team. We welcome contributions, with a few principles:

- **Small, focused PRs** get reviewed fast. Large architectural changes should start as a discussion issue first.
- **Tests are required** for new features and for any change that touches the channel engine, broadcast, flows, or tenants.
- **Saying "no" is a valid outcome.** Not every feature belongs in the core. If we decline a PR, we'll explain why — and you're welcome to maintain it as a fork.
- **Response time commitment:** issues and PRs get a first response within 48 hours. Not "done" — just acknowledged.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## License

AGPL v3. See [LICENSE](LICENSE) for the full text.

**Why AGPL?** Because we want this to stay open. AGPL's network-use clause prevents well-funded competitors from running our code as a hosted service without contributing back, while keeping the code fully free for developers, agencies, and self-hosted users. It's the same license Grafana, Mastodon, and Nextcloud use for the same reason.

**For enterprises whose legal teams can't accept AGPL:** a commercial license is available. Email tapan@jinacode.systems with your use case.

---

## About

Built by [Jina Code Systems LLP](https://jinacode.systems) in India.

Jina Connect started as our own hosted CPaaS product. We open-sourced the core because we believe customer communication infrastructure should be open, multi-channel, multi-provider, and AI-native — and because the market deserves an alternative to single-vendor lock-in.

The hosted product remains. The open source is real. Both use the same core.

---

## Links

- [Documentation](https://docs.jinaconnect.com) *(coming soon)*
- [Hosted product](https://jinaconnect.com) *(coming soon)*
- [Discord community](https://discord.gg/jinaconnect) *(coming soon)*
- [Discussions](../../discussions) — questions, ideas, show-and-tell
- [Issues](../../issues) — bugs and feature requests
- [Twitter/X](https://twitter.com/jinaconnect) *(coming soon)*

---

*v1 — April 2026. Working document, updated as the product evolves.*
