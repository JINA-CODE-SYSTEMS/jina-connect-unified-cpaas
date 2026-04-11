"""
Jina Connect MCP Server — WhatsApp tools for Claude, Cursor, Copilot.

Run standalone:
    python -m mcp_server                    # stdio (for Claude Desktop / Cursor)
    python -m mcp_server --transport http   # streamable HTTP (for remote clients)

Or add to Claude Code:
    claude mcp add --transport http jina-connect http://localhost:9000/mcp

Licensed under MIT — see LICENSE-MIT in this directory.
"""
