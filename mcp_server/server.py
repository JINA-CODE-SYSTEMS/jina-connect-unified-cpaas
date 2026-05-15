"""
Jina Connect MCP Server - entrypoint and tool registry.

Exposes WhatsApp, Telegram, and SMS messaging, contacts, campaigns,
and provider management as MCP tools that any AI client can call.
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Bootstrap Django ORM so we can import models / adapters
# ---------------------------------------------------------------------------
# Prepend project root to sys.path so "wa", "tenants", etc. are importable
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jina_connect.settings")

import django  # noqa: E402

django.setup()

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    "Jina Connect",
    instructions=(
        "Jina Connect is a multi-channel CPaaS supporting WhatsApp, Telegram, and SMS. "
        "Use these tools to send messages, manage templates, contacts, "
        "broadcasts, and providers. Use channel='WHATSAPP', channel='TELEGRAM', "
        "or channel='SMS' to route via the desired channel adapter."
    ),
    stateless_http=True,
    json_response=True,
)

# ---------------------------------------------------------------------------
# Import tool modules — each registers tools via @mcp.tool()
# ---------------------------------------------------------------------------
from mcp_server.auth import resolve_tenant  # noqa: E402, F401
from mcp_server.tools import campaigns, contacts, messaging, providers, voice  # noqa: E402, F401
