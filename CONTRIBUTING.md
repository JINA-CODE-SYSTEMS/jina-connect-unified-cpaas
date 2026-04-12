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
